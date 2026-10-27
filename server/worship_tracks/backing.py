"""AI band backing tracks.

The song (without its vocals) goes to ACE-Step's "cover" mode, which plays a brand new
recording that keeps the song's structure, chords and timing, in the sound described by
the caption. The new recording is checked against the original and nudged into line,
then split into instruments so each musician can still mute their own part.
"""

import logging
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import audio, structure
from .acestep import AceStep, AceStepError
from .chords import Chart
from .rhythm import Grid

log = logging.getLogger(__name__)

HOP = 512
# The vocal stem isn't sent: the backing should have no singing in it.
INSTRUMENT_STEMS = ("drums", "bass", "guitar", "piano", "other")
_VOCAL_WORDS = re.compile(r"\b(vocals?|voices?|singer|singing|sung|choir|lyrics?|rap)\b", re.IGNORECASE)


def instrumental(stems: dict[str, np.ndarray]) -> np.ndarray:
    return sum(stems[s] for s in INSTRUMENT_STEMS if s in stems)


def clean_caption(caption: str) -> str:
    """Keep the style and instruments from a description, drop anything about singing
    (the backing track is instrumental)."""
    sentences = re.split(r"(?<=[.;])\s+|,\s*", caption.strip())
    kept = [s.strip(" .;") for s in sentences if s.strip(" .;") and not _VOCAL_WORDS.search(s)]
    text = ", ".join(kept)
    return f"Instrumental. {text}" if text else "Instrumental worship band backing track"


def analyse(song_dir: Path, stems: dict[str, np.ndarray], mix: np.ndarray, duration: float, grid: Grid,
            chart: Chart | None, engine: AceStep | None) -> structure.Layout:
    """Tempo, key, time signature, sections and instruments: from the chart where it says,
    from ACE-Step's listening where it can, and from the audio otherwise."""
    sr = audio.SAMPLE_RATE
    heard: dict = {}
    if engine is not None:
        try:
            heard = engine.analyse(song_dir / "instrumental.flac")
        except AceStepError as exc:
            log.warning("ACE-Step couldn't analyse the song (%s); using our own analysis", exc)

    harmony = stems["guitar"] + stems["piano"] + stems["other"] + 0.5 * stems["bass"]
    tempo = (chart.tempo if chart and chart.tempo else None) or _number(heard.get("bpm")) or 60.0 / grid.beat_length
    key = (structure.key_name(chart.key) if chart and chart.key else None) or heard.get("keyscale") \
        or structure.detect_key(harmony, sr)
    time_sig = (chart.time if chart and chart.time else None) or heard.get("timesignature") \
        or ("6/8" if grid.sub == 3 else "4/4")
    if isinstance(time_sig, (int, float)) or (isinstance(time_sig, str) and time_sig.isdigit()):
        time_sig = f"{int(time_sig)}/{8 if int(time_sig) in (6, 9, 12) else 4}"
    names = structure.chart_section_names(chart) or structure.section_names_from_lyrics(heard.get("lyrics") or "") \
        or None
    sections = structure.find_sections(harmony, stems.get("vocals", np.zeros(0)), sr, duration, grid, names)
    caption = heard.get("prompt") or heard.get("caption") or structure.describe_instruments(stems, audio.rms(mix))
    return structure.Layout(float(tempo), str(key), str(time_sig), sections, clean_caption(caption))


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def generate(song_dir: Path, engine: AceStep, layout: structure.Layout, caption: str, duration: float,
             strength: float, seed: int | None, on_wait: Callable[[float], None]) -> tuple[np.ndarray, dict]:
    """A new backing recording, lined up with the original. Returns (stereo audio, details)."""
    raw = song_dir / "backing-raw.wav"
    result = engine.cover(song_dir / "instrumental.flac", raw, caption=caption, bpm=layout.tempo, key=layout.key,
                          time_signature=layout.time, duration=duration, strength=strength, seed=seed,
                          on_wait=on_wait)
    generated = _read_any(raw, song_dir)
    original = audio.read(song_dir / "instrumental.flac")
    shift, drift = align(original.mean(axis=0), generated.mean(axis=0), audio.SAMPLE_RATE,
                         max_shift=min(0.5, 0.45 * 60.0 / layout.tempo))
    generated = _shift(generated, shift, original.shape[1])
    details = {"seed": result.get("seed_value") or seed, "shift": round(shift, 3), "drift": round(drift, 3)}
    raw.unlink(missing_ok=True)
    return generated, details


def _read_any(path: Path, song_dir: Path) -> np.ndarray:
    """ACE-Step's output, as 44.1 kHz stereo (it may come at another rate or format)."""
    converted = song_dir / "backing-44k.wav"
    audio.decode(path, converted)
    data = audio.read(converted)
    converted.unlink(missing_ok=True)
    return data


def align(original: np.ndarray, generated: np.ndarray, sr: int, max_shift: float = 0.3) -> tuple[float, float]:
    """How many seconds later the generated recording runs than the original (positive:
    it's late), and how much that changes from the first to the last minute (drift).

    Keep `max_shift` under half a beat: in steady music, being a whole beat off can line
    up almost as well as being right, and the band only ever runs slightly off."""
    import librosa

    def envelope(y):
        return librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)

    a, b = envelope(original), envelope(generated)
    frame = HOP / sr
    lag_max = int(max_shift / frame)

    def best_lag(x, y):
        n = min(len(x), len(y))
        x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
        lags = np.arange(-lag_max, lag_max + 1)
        scores = np.array([np.dot(x[max(0, -k):n - max(0, k)], y[max(0, k):n - max(0, -k)]) for k in lags])
        # Of the lags that line up about as well as the best, take the smallest.
        good = lags[scores >= scores.max() - 0.02 * abs(scores.max())]
        return float(good[np.argmin(np.abs(good))]) * frame

    window = int(60 / frame)
    start = best_lag(a[:window], b[:window])
    end = best_lag(a[-window:], b[-window:]) if min(len(a), len(b)) > 2 * window else start
    return start, end - start


def _shift(data: np.ndarray, seconds: float, length: int) -> np.ndarray:
    """Move audio earlier (positive seconds) or later, and fit it to `length` samples."""
    samples = int(round(seconds * audio.SAMPLE_RATE))
    if samples > 0:
        data = data[:, samples:]
    elif samples < 0:
        data = np.pad(data, ((0, 0), (-samples, 0)))
    if data.shape[1] < length:
        data = np.pad(data, ((0, 0), (0, length - data.shape[1])))
    return data[:, :length]
