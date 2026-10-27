"""Song in, play-along tracks out.

1. Decode the upload and separate it into stems with Demucs.
2. Work out the song's layout: tempo, key, time signature, sections, instruments.
3. Make the tracks, either:
   - AI band (when ACE-Step is running): a new recording of the song by ACE-Step,
     following its structure, chords and timing, then split into instruments; or
   - note by note: transcribe each stem and play the notes on SoundFont instruments.
"""

import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pretty_midi

from . import arrange, audio, backing
from .acestep import AceStep
from .chords import Chart
from .instruments import TRACKS, Track
from .rhythm import Grid, lock_to_grid, make_grid
from .transcribe import Note, make_monophonic, transcribe_drums, transcribe_pitched

# Some Demucs operations have no Apple GPU version yet; run those on the CPU.
# This must be set before PyTorch is first imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

log = logging.getLogger(__name__)

DEMUCS_MODEL = "htdemucs_6s"
# A stem quieter than this, relative to the full mix, counts as "instrument not in the song".
SILENT_STEM_RATIO = 0.04
# Bass parts stay below about E3; notes above are usually the singer leaking into the bass.
BASS_TOP = 52

Progress = Callable[[str, float], None]
Separate = Callable[[np.ndarray, Callable[[float], None]], dict[str, np.ndarray]]

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
    separate_fn: Separate = separate,
    chart: Chart | None = None,
    engine: AceStep | None = None,
    backing_settings: dict | None = None,
) -> dict:
    """Builds `song_dir/tracks/<id>.m4a` (the play-along tracks) and `song_dir/original/<id>.m4a`
    (the separated stems, for comparison). Returns the metadata the player needs.

    With `engine` (ACE-Step), the tracks come from a new AI band recording of the song;
    without it, each part is transcribed and played back note by note."""
    audio.require_tools()
    on_progress("Reading the song", 0.01)
    stems, mix, duration = _separate_song(song_dir, source, separate_fn,
                                          lambda f: on_progress("Listening for each instrument", 0.02 + 0.3 * f))
    mono = {name: stem.mean(axis=0) for name, stem in stems.items()}
    return finish_song(song_dir, mono, mix.mean(axis=0), duration, soundfont, chart, engine, backing_settings,
                       separate_fn, lambda step, f: on_progress(step, 0.32 + 0.68 * f))


def rebuild_tracks(
    song_dir: Path,
    soundfont: Path,
    chart: Chart | None,
    on_progress: Progress,
    engine: AceStep | None = None,
    backing_settings: dict | None = None,
    separate_fn: Separate = separate,
    make_tracks: bool = True,
) -> dict:
    """Make the tracks again (a new chord chart, a new AI band take) from the stems already
    saved, without separating the song again. With `make_tracks` False, only the analysis
    (layout and chords) is redone and the existing tracks are kept."""
    audio.require_tools()
    if engine is not None and make_tracks and not (song_dir / "instrumental.flac").exists():
        # Songs made before the AI band existed: separate once more to get a clean instrumental.
        on_progress("Listening for each instrument", 0.02)
        source = next(song_dir.glob("source.*"))
        _separate_song(song_dir, source, separate_fn, lambda f: on_progress("Listening for each instrument", 0.02 + 0.3 * f))
    with tempfile.TemporaryDirectory(prefix="worship-tracks-") as tmp_name:
        tmp = Path(tmp_name)
        on_progress("Reading the song", 0.33)
        duration = audio.decode(song_dir / "mix.m4a", tmp / "mix.wav")
        mix = audio.read(tmp / "mix.wav")[0]
        stems = {}
        for track in TRACKS:
            audio.decode(song_dir / "original" / f"{track.id}.m4a", tmp / f"{track.id}.wav")
            stems[track.stem] = audio.read(tmp / f"{track.id}.wav")[0]
    return finish_song(song_dir, stems, mix, duration, soundfont, chart, engine, backing_settings, separate_fn,
                       lambda step, f: on_progress(step, 0.35 + 0.65 * f), make_tracks=make_tracks)


def _separate_song(song_dir: Path, source: Path, separate_fn: Separate, on_progress: Callable[[float], None]):
    """Separate the upload into stems; saves the mix, each stem, and the instrumental
    (everything but vocals) the AI band plays from. Returns (stereo stems, stereo mix, seconds)."""
    original_dir = song_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="worship-tracks-") as tmp_name:
        tmp = Path(tmp_name)
        mix_wav = tmp / "mix.wav"
        duration = audio.decode(source, mix_wav)
        mix = audio.read(mix_wav)
        audio.encode_m4a(mix_wav, song_dir / "mix.m4a", duration)
        stems = separate_fn(mix, on_progress)
        for track in TRACKS:
            stem_wav = tmp / f"{track.stem}.wav"
            audio.write(stem_wav, stems[track.stem])
            audio.encode_m4a(stem_wav, original_dir / f"{track.id}.m4a", duration)
        audio.write(song_dir / "instrumental.flac", backing.instrumental(stems))
    return stems, mix, duration


def finish_song(
    song_dir: Path,
    stems: dict[str, np.ndarray],
    mix: np.ndarray,
    duration: float,
    soundfont: Path,
    chart: Chart | None,
    engine: AceStep | None,
    backing_settings: dict | None,
    separate_fn: Separate,
    on_progress: Progress,
    make_tracks: bool = True,
) -> dict:
    """Analyse the song's layout, then make the tracks: an AI band take when ACE-Step is
    available, otherwise note-by-note. With `make_tracks` False, just the analysis."""
    sr = audio.SAMPLE_RATE
    on_progress("Finding the beat", 0.0)
    grid = make_grid(mix, sr, duration, chart.tempo if chart else None, chart.time if chart else None)
    spans = None
    if chart and chart.vocabulary:
        on_progress("Following the chord chart", 0.02)
        harmony = stems["guitar"] + stems["piano"] + stems["other"]
        spans = arrange.recognise_chords(harmony, stems["bass"], sr, duration, chart, grid)
    on_progress("Working out the song's layout", 0.04)
    layout = backing.analyse(song_dir, stems, mix, duration, grid, chart,
                             engine if (song_dir / "instrumental.flac").exists() else None)
    result = {
        "duration": duration,
        "chords": [s.to_dict() for s in spans] if spans else [],
        "layout": layout.to_dict(),
    }

    if not make_tracks:
        on_progress("Done", 1.0)
        return result
    if engine is None:
        result.update(build_tracks(song_dir, stems, mix, duration, soundfont, chart,
                                   lambda step, f: on_progress(step, 0.1 + 0.9 * f), grid=grid, spans=spans))
        result["engine"] = "notes"
        return result

    settings = backing_settings or {}
    caption = (settings.get("caption") or "").strip() or layout.instruments
    strength = float(settings.get("strength") or 1.0)
    started = 0.1

    def waiting(seconds: float) -> None:
        # ACE-Step doesn't report progress; assume about two minutes and creep along after.
        on_progress(f"The AI band is playing the song ({int(seconds) // 60}:{int(seconds) % 60:02d})",
                    started + 0.6 * min(0.95, seconds / (seconds + 120)))

    generated, details = backing.generate(song_dir, engine, layout, caption, duration, strength,
                                          settings.get("seed"), waiting)
    result.update(_band_tracks(song_dir, generated, stems, mix, duration, separate_fn,
                               lambda step, f: on_progress(step, 0.72 + 0.28 * f)))
    result["engine"] = "ai-band"
    result["backing"] = {**details, "caption": caption, "strength": strength}
    return result


def _band_tracks(song_dir: Path, generated: np.ndarray, stems: dict[str, np.ndarray], mix: np.ndarray,
                 duration: float, separate_fn: Separate, on_progress: Progress) -> dict:
    """Split the AI band's recording into the player's tracks. The vocal track is the
    original singer, as a guide."""
    tracks_dir = song_dir / "tracks"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    on_progress("Splitting the band into instruments", 0.0)
    band = separate_fn(generated, lambda f: on_progress("Splitting the band into instruments", 0.9 * f))
    band_rms = audio.rms(generated.mean(axis=0))
    results = []
    with tempfile.TemporaryDirectory(prefix="worship-tracks-") as tmp_name:
        tmp = Path(tmp_name)
        audio.write(tmp / "backing.wav", generated)
        audio.encode_m4a(tmp / "backing.wav", song_dir / "backing.m4a", duration)
        for track in TRACKS:
            if track.id == "melody":
                present = audio.rms(stems["vocals"]) > audio.rms(mix) * SILENT_STEM_RATIO
                if present:
                    shutil.copyfile(song_dir / "original" / "melody.m4a", tracks_dir / "melody.m4a")
                results.append({**_track_info(track, notes=0, present=present), "label": "Vocals (original)",
                                "ai": False})
                continue
            stem = band[track.stem]
            present = audio.rms(stem) > band_rms * SILENT_STEM_RATIO
            if present:
                audio.write(tmp / f"{track.id}.wav", stem)
                audio.encode_m4a(tmp / f"{track.id}.wav", tracks_dir / f"{track.id}.m4a", duration)
            elif (tracks_dir / f"{track.id}.m4a").exists():
                (tracks_dir / f"{track.id}.m4a").unlink()
            results.append(_track_info(track, notes=0, present=present))
    on_progress("Done", 1.0)
    return {"tracks": results}


def build_tracks(
    song_dir: Path,
    stems: dict[str, np.ndarray],
    mix: np.ndarray,
    duration: float,
    soundfont: Path,
    chart: Chart | None,
    on_progress: Progress,
    grid: Grid | None = None,
    spans=None,
) -> dict:
    """Transcribe each (mono) stem and play it back. With a chart, chordal parts play
    the chart's chords instead of the notes the AI heard."""
    tracks_dir = song_dir / "tracks"
    midi_dir = song_dir / "midi"
    for d in (tracks_dir, midi_dir):
        d.mkdir(parents=True, exist_ok=True)
    sr = audio.SAMPLE_RATE
    if grid is None:
        on_progress("Finding the beat", 0.0)
        grid = make_grid(mix, sr, duration, chart.tempo if chart else None, chart.time if chart else None)
        if chart and chart.vocabulary:
            on_progress("Following the chord chart", 0.03)
            harmony = stems["guitar"] + stems["piano"] + stems["other"]
            spans = arrange.recognise_chords(harmony, stems["bass"], sr, duration, chart, grid)

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
            notes = _notes_for(track, stem, tmp, grid, spans, chart, duration)
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


def _notes_for(track: Track, stem: np.ndarray, tmp: Path, grid: Grid, spans, chart: Chart | None,
               duration: float) -> list[Note]:
    sr = audio.SAMPLE_RATE
    if track.is_drum:
        return arrange.lock_notes(transcribe_drums(stem, sr), grid, even_out=True)
    if spans and track.id in ("guitar", "keys", "bass"):
        # (lowest Hz, highest Hz, how strong a hit must be next to the playing around it,
        #  how much of the player's usual playing a place in the bar needs to keep its hits)
        low, high, keep, pattern = {"guitar": (80, 3000, 0.35, 0.5), "keys": (60, 3000, 0.45, 0.6),
                                    "bass": (30, 300, 0.55, 0.6)}[track.id]
        hits = lock_to_grid(*arrange.onsets(stem, sr, low, high), grid, keep=keep, pattern=pattern)
        if track.id == "guitar":
            return arrange.chord_hits(spans, hits, grid, arrange.guitar_voicing, duration, strum=0.012,
                                      up_strums=True)
        if track.id == "keys":
            return arrange.chord_hits(spans, hits, grid, _piano_voicing(), duration)
        return arrange.bass_line(spans, hits, grid, duration)
    if spans and track.id == "pads":
        return arrange.pad_notes(spans, stem, sr)

    stem_wav = tmp / f"{track.id}-stem.wav"
    audio.write(stem_wav, stem)
    notes = transcribe_pitched(str(stem_wav), track)
    if track.id == "bass":
        notes = [n for n in notes if n.pitch <= BASS_TOP]  # higher is the singer leaking in
    if track.id in ("guitar", "keys", "pads", "bass"):
        notes = arrange.lock_notes(notes, grid)
        if track.monophonic:
            notes = make_monophonic(notes)
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
