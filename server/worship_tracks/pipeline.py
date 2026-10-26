"""Song in, play-along tracks out.

1. Decode the upload to WAV.
2. Separate it into stems with Demucs, only so each instrument can be heard on its own.
3. Transcribe each stem into notes.
4. Play the notes back with SoundFont instruments, at the level the instrument had in the mix.
"""

import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pretty_midi

from . import arrange, audio
from .chords import Chart
from .instruments import TRACKS, Track
from .transcribe import Note, transcribe_drums, transcribe_pitched

# Some Demucs operations have no Apple GPU version yet; run those on the CPU.
# This must be set before PyTorch is first imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

log = logging.getLogger(__name__)

DEMUCS_MODEL = "htdemucs_6s"
# A stem quieter than this, relative to the full mix, counts as "instrument not in the song".
SILENT_STEM_RATIO = 0.04

Progress = Callable[[str, float], None]

_separator = None


def _device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _get_separator():
    global _separator
    if _separator is None:
        from demucs.api import Separator

        _separator = Separator(model=DEMUCS_MODEL, device=_device(), progress=False)
    return _separator


def separate(mix: np.ndarray, on_progress: Callable[[float], None]) -> dict[str, np.ndarray]:
    import torch

    separator = _get_separator()

    def callback(info: dict) -> None:
        if info["state"] == "end":
            per_model = 1 / info["models"]
            done = info["model_idx_in_bag"] * per_model + per_model * min(
                1.0, info["segment_offset"] / max(1, info["audio_length"])
            )
            on_progress(done)

    separator.update_parameter(callback=callback)
    wav = torch.from_numpy(mix)
    try:
        _, stems = separator.separate_tensor(wav, audio.SAMPLE_RATE)
    except RuntimeError:
        if separator._device == "cpu":
            raise
        log.exception("Separation failed on %s, retrying on the CPU", separator._device)
        separator.update_parameter(device="cpu")
        _, stems = separator.separate_tensor(wav, audio.SAMPLE_RATE)
    return {name: stem.cpu().numpy() for name, stem in stems.items()}


def notes_to_midi(notes: list[Note], track: Track, path: Path) -> None:
    midi = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=track.program, is_drum=track.is_drum, name=track.label)
    inst.notes = [pretty_midi.Note(n.velocity, n.pitch, n.start, n.end) for n in notes]
    midi.instruments.append(inst)
    midi.write(str(path))


def process_song(
    song_dir: Path,
    source: Path,
    soundfont: Path,
    on_progress: Progress,
    separate_fn: Callable[[np.ndarray, Callable[[float], None]], dict[str, np.ndarray]] = separate,
    chart: Chart | None = None,
) -> dict:
    """Builds `song_dir/tracks/<id>.m4a` (recreated) and `song_dir/original/<id>.m4a`
    (the separated stem, for comparison). Returns the metadata the player needs."""
    audio.require_tools()
    original_dir = song_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="worship-tracks-") as tmp_name:
        tmp = Path(tmp_name)
        on_progress("Reading the song", 0.01)
        mix_wav = tmp / "mix.wav"
        duration = audio.decode(source, mix_wav)
        mix = audio.read(mix_wav)
        audio.encode_m4a(mix_wav, song_dir / "mix.m4a", duration)

        on_progress("Listening for each instrument", 0.03)
        stems = separate_fn(mix, lambda f: on_progress("Listening for each instrument", 0.03 + 0.5 * f))
        for track in TRACKS:
            stem_wav = tmp / f"{track.stem}.wav"
            audio.write(stem_wav, stems[track.stem])
            audio.encode_m4a(stem_wav, original_dir / f"{track.id}.m4a", duration)

    mono = {name: stem.mean(axis=0) for name, stem in stems.items()}
    return build_tracks(song_dir, mono, mix.mean(axis=0), duration, soundfont, chart,
                        lambda step, f: on_progress(step, 0.53 + 0.47 * f))


def rebuild_tracks(song_dir: Path, soundfont: Path, chart: Chart | None, on_progress: Progress) -> dict:
    """Make the tracks again (e.g. with a new chord chart) from the stems already saved,
    without separating the song again."""
    audio.require_tools()
    with tempfile.TemporaryDirectory(prefix="worship-tracks-") as tmp_name:
        tmp = Path(tmp_name)
        on_progress("Reading the song", 0.02)
        duration = audio.decode(song_dir / "mix.m4a", tmp / "mix.wav")
        mix = audio.read(tmp / "mix.wav")[0]
        stems = {}
        for track in TRACKS:
            audio.decode(song_dir / "original" / f"{track.id}.m4a", tmp / f"{track.id}.wav")
            stems[track.stem] = audio.read(tmp / f"{track.id}.wav")[0]
    return build_tracks(song_dir, stems, mix, duration, soundfont, chart,
                        lambda step, f: on_progress(step, 0.05 + 0.95 * f))


def build_tracks(
    song_dir: Path,
    stems: dict[str, np.ndarray],
    mix: np.ndarray,
    duration: float,
    soundfont: Path,
    chart: Chart | None,
    on_progress: Progress,
) -> dict:
    """Transcribe each (mono) stem and play it back. With a chart, chordal parts play
    the chart's chords instead of the notes the AI heard."""
    tracks_dir = song_dir / "tracks"
    midi_dir = song_dir / "midi"
    for d in (tracks_dir, midi_dir):
        d.mkdir(parents=True, exist_ok=True)
    sr = audio.SAMPLE_RATE
    spans = None
    if chart and chart.vocabulary:
        on_progress("Following the chord chart", 0.0)
        harmony = stems["guitar"] + stems["piano"] + stems["other"]
        spans = arrange.recognise_chords(harmony, stems["bass"], mix, sr, duration, chart)

    mix_rms = audio.rms(mix)
    results = []
    step = 0.9 / len(TRACKS)
    with tempfile.TemporaryDirectory(prefix="worship-tracks-") as tmp_name:
        tmp = Path(tmp_name)
        for i, track in enumerate(TRACKS):
            base = 0.1 + i * step
            stem = stems[track.stem]
            if audio.rms(stem) < mix_rms * SILENT_STEM_RATIO:
                results.append(_track_info(track, notes=0, present=False))
                continue

            on_progress(f"Writing down the {track.label.lower()} part", base)
            notes = _notes_for(track, stem, tmp, spans, chart, duration)
            if not notes:
                results.append(_track_info(track, notes=0, present=False))
                continue

            on_progress(f"Playing the {track.label.lower()} part", base + step / 2)
            midi_path = midi_dir / f"{track.id}.mid"
            notes_to_midi(notes, track, midi_path)
            rendered_wav = tmp / f"{track.id}-rendered.wav"
            audio.render_midi(midi_path, soundfont, rendered_wav)
            rendered = audio.match_loudness(audio.read(rendered_wav), stem)
            audio.write(rendered_wav, rendered)
            audio.encode_m4a(rendered_wav, tracks_dir / f"{track.id}.m4a", duration)
            results.append(_track_info(track, notes=len(notes), present=True))

    on_progress("Done", 1.0)
    return {
        "duration": duration,
        "tracks": results,
        "chords": [s.to_dict() for s in spans] if spans else [],
    }


def _notes_for(track: Track, stem: np.ndarray, tmp: Path, spans, chart: Chart | None, duration: float) -> list[Note]:
    sr = audio.SAMPLE_RATE
    if track.is_drum:
        return transcribe_drums(stem, sr)
    if spans and track.id in ("guitar", "keys"):
        times, strengths = arrange.onsets(stem, sr)
        if track.id == "guitar":
            return arrange.chord_hits(spans, times, strengths, arrange.guitar_voicing, duration, strum=0.012)
        return arrange.chord_hits(spans, times, strengths, _piano_voicing(), duration)
    if spans and track.id == "pads":
        return arrange.pad_notes(spans, stem, sr)

    stem_wav = tmp / f"{track.id}-stem.wav"
    audio.write(stem_wav, stem)
    notes = transcribe_pitched(str(stem_wav), track)
    if spans and track.id == "bass":
        notes = arrange.bass_on_changes(arrange.snap_bass(notes, spans), spans, stem, sr)
    if chart and track.id == "melody":
        notes = arrange.snap_to_key(notes, chart.key)
    return notes


def _piano_voicing():
    """Left hand: the bass note. Right hand: the chord close to middle C, moving as little
    as possible from the last chord."""
    previous: list[int] = []

    def voicing(chord):
        nonlocal previous
        center = int(np.mean(previous)) if previous else 64
        right = arrange._voicing_near(chord, center, 3)
        previous = right
        return [36 + (chord.bass - 0) % 12, *right]

    return voicing


def _track_info(track: Track, notes: int, present: bool) -> dict:
    return {"id": track.id, "label": track.label, "pan": track.pan, "notes": notes, "present": present}
