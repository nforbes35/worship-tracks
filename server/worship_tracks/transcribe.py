"""Turning isolated instrument audio into notes.

Pitched instruments go through Spotify's Basic Pitch. Drums use onset detection in
three frequency bands (kick, snare, cymbals), since Basic Pitch only hears pitch.
"""

from dataclasses import dataclass

import numpy as np

from .instruments import CLOSED_HAT, CRASH, KICK, SNARE, Track


@dataclass
class Note:
    start: float  # seconds
    end: float
    pitch: int  # MIDI note number
    velocity: int  # 1-127


_basic_pitch_model = None


def _model():
    global _basic_pitch_model
    if _basic_pitch_model is None:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model

        _basic_pitch_model = Model(ICASSP_2022_MODEL_PATH)
    return _basic_pitch_model


def transcribe_pitched(wav_path: str, track: Track) -> list[Note]:
    from basic_pitch.inference import predict

    _, _, events = predict(
        wav_path,
        _model(),
        onset_threshold=track.onset_threshold,
        frame_threshold=track.frame_threshold,
        minimum_note_length=track.min_note_ms,
        minimum_frequency=track.min_hz,
        maximum_frequency=track.max_hz,
        melodia_trick=True,
    )
    notes = [
        Note(float(start), float(end), int(pitch), _velocity(float(amplitude)))
        for start, end, pitch, amplitude, _bends in events
    ]
    notes.sort(key=lambda n: (n.start, n.pitch))
    notes = drop_overtones(notes)
    if track.merge_gap:
        notes = merge_repeats(notes, track.merge_gap)
    if track.monophonic:
        notes = make_monophonic(notes)
    return notes


def _velocity(amplitude: float) -> int:
    # Basic Pitch amplitudes mostly fall between 0.2 and 0.9.
    return int(np.clip(30 + amplitude * 110, 20, 120))


def drop_overtones(notes: list[Note]) -> list[Note]:
    """Remove the ghost notes Basic Pitch hears an octave or an octave-and-a-fifth above
    a real note: they start with it, sound while it does, and are clearly quieter."""
    kept = []
    for note in notes:
        overtone = any(
            note.pitch - base.pitch in (12, 19, 24)
            and abs(note.start - base.start) < 0.06
            and note.velocity < base.velocity * 0.8
            for base in notes
            if base is not note and base.start < note.start + 0.06 and base.end > note.start
        )
        if not overtone:
            kept.append(note)
    return kept


def merge_repeats(notes: list[Note], gap: float) -> list[Note]:
    """Join a held note that the transcription broke into pieces."""
    open_by_pitch: dict[int, Note] = {}
    result: list[Note] = []
    for note in sorted(notes, key=lambda n: n.start):
        prev = open_by_pitch.get(note.pitch)
        if prev is not None and note.start - prev.end < gap:
            prev.end = max(prev.end, note.end)
            prev.velocity = max(prev.velocity, note.velocity)
            continue
        open_by_pitch[note.pitch] = note
        result.append(note)
    return sorted(result, key=lambda n: (n.start, n.pitch))


def make_monophonic(notes: list[Note]) -> list[Note]:
    """Keep one note at a time: where notes overlap, the louder one wins and the
    earlier note is cut off where the next one starts."""
    result: list[Note] = []
    for note in sorted(notes, key=lambda n: n.start):
        if result and note.start < result[-1].end:
            prev = result[-1]
            if note.start - prev.start < 0.05:
                # Started together (often an octave or harmonic): keep the louder.
                if note.velocity > prev.velocity:
                    result[-1] = note
                continue
            if abs(note.pitch - prev.pitch) in (12, 19, 24) and note.velocity < prev.velocity * 0.8:
                continue  # an overtone of the note still ringing
            prev.end = note.start
        result.append(note)
    result = [n for n in result if n.end - n.start >= 0.04]
    # A short blip right before the same pitch is played again is the old note's tail.
    return [
        n for i, n in enumerate(result)
        if not (i + 1 < len(result) and n.end - n.start < 0.12
                and result[i + 1].pitch == n.pitch and result[i + 1].start - n.end < 0.1)
    ]


# Drum bands in Hz.
_BANDS = {"kick": (30, 150), "snare": (180, 5000), "cymbal": (6000, 16000)}
_HOP = 441  # 10 ms at 44.1 kHz


def transcribe_drums(audio: np.ndarray, sr: int) -> list[Note]:
    """Find kick, snare and cymbal hits in an isolated drum track."""
    import librosa

    if audio.ndim > 1:
        audio = audio.mean(axis=0)
    spec = np.abs(librosa.stft(audio, n_fft=2048, hop_length=_HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    frame_s = _HOP / sr

    energy = {}
    level = {}
    flux = {}
    for name, (lo, hi) in _BANDS.items():
        band = spec[(freqs >= lo) & (freqs < hi)].sum(axis=0)
        energy[name] = band
        # Loudness relative to this drum's loud hits (ignoring the odd outlier).
        level[name] = band / (np.percentile(band, 99.5) + 1e-12)
        log_band = np.log1p(band / (band.max() + 1e-12) * 1000)
        flux[name] = np.maximum(0.0, np.diff(log_band, prepend=log_band[0]))

    notes: list[Note] = []
    for name in _BANDS:
        f = flux[name]
        if f.max() <= 0:
            continue
        f = f / f.max()
        wait = 4 if name == "cymbal" else 5  # frames between hits of the same drum
        peaks = librosa.util.peak_pick(
            f, pre_max=3, post_max=3, pre_avg=30, post_avg=30, delta=0.12, wait=wait
        )
        for p in peaks:
            hit = level[name][p : p + 5].max()
            if hit < _MIN_LEVEL[name] or _is_bleed(name, p, hit, level):
                continue
            pitch = {"kick": KICK, "snare": SNARE}.get(name) or _cymbal_kind(energy["cymbal"], p, frame_s)
            velocity = int(np.clip(40 + np.sqrt(min(hit, 1.0)) * 85, 30, 125))
            start = p * frame_s
            notes.append(Note(start, start + 0.1, pitch, velocity))
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


# Quietest hit (relative to the loud ones) still counted as a hit on that drum.
_MIN_LEVEL = {"kick": 0.15, "snare": 0.15, "cymbal": 0.06}


def _is_bleed(name: str, frame: int, hit: float, level: dict[str, np.ndarray]) -> bool:
    """A snare has some low thump and a kick has a mid-range click. A weak jump in one
    band at the moment another band is hit hard is that other drum, not this one."""
    other = {"kick": "snare", "snare": "kick"}.get(name)
    if other is None:
        return False
    return hit < 0.4 and level[other][frame : frame + 5].max() > 0.6


def _cymbal_kind(energy: np.ndarray, frame: int, frame_s: float) -> int:
    """Crashes ring for a long time; hi-hats die away almost immediately."""
    later = frame + int(0.3 / frame_s)
    if later >= len(energy):
        return CLOSED_HAT
    peak = energy[frame : frame + 3].max() + 1e-12
    return CRASH if energy[later] / peak > 0.35 else CLOSED_HAT
