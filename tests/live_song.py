"""A made-up song that behaves like a real live worship recording, to test against.

Compared to `synthetic.py` it is in 6/8 at 75 BPM (like many worship songs), is played
with human timing and dynamics, has two guitars and two keyboard parts, a low (male)
melody, a percussion loop, and its "separated" stems leak into each other the way
Demucs output does.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pretty_midi
from worship_tracks import audio

BPM = 75  # dotted quarters, as SongSelect writes 6/8 tempos
BEAT = 60 / BPM
EIGHTH = BEAT / 3
BAR = 2 * BEAT  # a bar of 6/8 is two dotted-quarter beats

# One chord per bar.
PROGRESSION = [
    ("C", 36, [60, 64, 67]),
    ("Am7", 33, [60, 64, 67, 69]),
    ("F", 29, [60, 65, 69]),
    ("G", 31, [59, 62, 67]),
]
CHART = "Key: C Tempo: 75 Time: 6/8\nC  Am7  F  G\nC/E  F2  Gsus4  Csus4\nAm7  G  F  C"
MELODY = [(0, 3, 60), (3, 2, 62), (5, 1, 64), (6, 3, 64), (9, 3, 62),
          (12, 2, 60), (14, 1, 57), (15, 3, 60), (18, 6, 55)]  # (eighth, length, pitch)


@dataclass
class Truth:
    chords: list[tuple[float, float, str]] = field(default_factory=list)
    drums: list[pretty_midi.Note] = field(default_factory=list)
    strums: list[float] = field(default_factory=list)  # acoustic guitar strum times
    keys_hits: list[float] = field(default_factory=list)
    bass: list[pretty_midi.Note] = field(default_factory=list)
    melody: list[pretty_midi.Note] = field(default_factory=list)


def _parts(bars: int, rng: np.random.Generator):
    parts = {name: [] for name in ("drums", "loop", "bass", "acoustic", "electric", "piano", "organ", "vocals")}
    truth = Truth()

    def add(part, pitch, start, length, velocity, jitter=0.012):
        start = max(0.0, start + rng.normal(0, jitter))
        velocity = int(np.clip(velocity + rng.normal(0, 8), 20, 127))
        note = pretty_midi.Note(velocity, pitch, start, start + length)
        parts[part].append(note)
        return note

    for bar in range(bars):
        name, root, chord = PROGRESSION[bar % 4]
        t0 = bar * BAR
        truth.chords.append((t0, t0 + BAR, name))
        e = lambda i: t0 + i * EIGHTH
        # Drums: 6/8 groove, kick on 1 (and a pickup), snare on 4, eighth hats, crash every 4 bars,
        # a tom fill at the end of every 8 bars.
        fill = bar % 8 == 7
        for i in range(6):
            if fill and i >= 3:
                truth.drums.append(add("drums", (50, 47, 45)[i - 3], e(i), 0.2, 105))
                continue
            truth.drums.append(add("drums", 42, e(i), 0.1, 85 if i in (0, 3) else 60))
        truth.drums.append(add("drums", 36, e(0), 0.2, 112))
        if bar % 2 == 1:
            truth.drums.append(add("drums", 36, e(5), 0.2, 90))
        if not fill:
            truth.drums.append(add("drums", 38, e(3), 0.2, 110))
        if bar % 4 == 0:
            truth.drums.append(add("drums", 49, e(0), 1.5, 105))
        # A shaker loop on every eighth (perfectly in time, it's a loop).
        for i in range(6):
            add("loop", 70, e(i), 0.1, 70, jitter=0)
        # Bass: root on each dotted-quarter beat, a passing note into the next bar.
        truth.bass.append(add("bass", root, e(0), 2.8 * EIGHTH, 100))
        truth.bass.append(add("bass", root, e(3), 1.8 * EIGHTH, 90))
        next_root = PROGRESSION[(bar + 1) % 4][1]
        truth.bass.append(add("bass", root + (2 if next_root > root else -1), e(5), 0.9 * EIGHTH, 80))
        # Acoustic guitar: strummed D . D U D U
        voicing = [40 + (root - 28) % 12] + [p - 12 for p in chord] + [chord[0], chord[1]]
        for i in (0, 2, 3, 4, 5):
            up = i in (4,)
            strings = voicing[2:] if up else voicing
            start = e(i)
            truth.strums.append(start)
            for k, p in enumerate(reversed(strings) if up else strings):
                add("acoustic", p, start + k * 0.008, EIGHTH * 0.95, 70 if up else 95, jitter=0.002)
        # Electric guitar: ringing notes with a dotted-eighth delay echo.
        for i, p in ((0, chord[-1] + 12), (3, chord[1] + 12)):
            note = add("electric", p, e(i), BEAT, 80)
            for echo in (1, 2):
                add("electric", p, note.start + echo * 1.5 * EIGHTH, BEAT, 80 - 25 * echo, jitter=0)
        # Piano: chord on 1 and 4, with the bass note.
        for i in (0, 3):
            truth.keys_hits.append(e(i))
            add("piano", root + 12, e(i), BEAT * 0.95, 80)
            for p in chord:
                add("piano", p + 12, e(i), BEAT * 0.95, 75)
        # Organ: chord held all bar.
        for p in chord:
            add("organ", p, t0, BAR, 55, jitter=0)
        # Melody (male voice), every 4 bars.
        if bar % 4 == 0:
            for i, length, p in MELODY:
                if bar * 6 + i < bars * 6:
                    truth.melody.append(add("vocals", p, e(i), length * EIGHTH * 0.9, 100))
    return parts, truth


PROGRAMS = {"drums": (0, True), "loop": (0, True), "bass": (33, False), "acoustic": (25, False),
            "electric": (27, False), "piano": (0, False), "organ": (16, False), "vocals": (53, False)}
# Which Demucs stem each part would end up in.
STEM_OF = {"drums": "drums", "loop": "drums", "bass": "bass", "acoustic": "guitar", "electric": "guitar",
           "piano": "piano", "organ": "other", "vocals": "vocals"}


def make_live_song(dest_dir: Path, soundfont: Path, bars: int = 16, bleed: float = 0.2, seed: int = 1):
    """Writes `song.wav`. Returns (path, stems as Demucs might return them, Truth)."""
    rng = np.random.default_rng(seed)
    dest_dir.mkdir(parents=True, exist_ok=True)
    parts, truth = _parts(bars, rng)
    length = int((bars * BAR + 2) * audio.SAMPLE_RATE)
    clean: dict[str, np.ndarray] = {}
    for name, (program, is_drum) in PROGRAMS.items():
        midi = pretty_midi.PrettyMIDI(initial_tempo=BPM * 3)
        inst = pretty_midi.Instrument(program=program, is_drum=is_drum)
        inst.notes = parts[name]
        midi.instruments.append(inst)
        midi.write(str(dest_dir / f"{name}.mid"))
        audio.render_midi(dest_dir / f"{name}.mid", soundfont, dest_dir / f"{name}.wav")
        rendered = audio.read(dest_dir / f"{name}.wav")
        rendered = np.pad(rendered, ((0, 0), (0, max(0, length - rendered.shape[1]))))[:, :length]
        stem = STEM_OF[name]
        clean[stem] = clean.get(stem, 0) + rendered * 0.4
    mix = sum(clean.values())
    scale = min(1.0, 0.95 / np.abs(mix).max())
    mix *= scale
    # Imperfect separation: every stem carries some of the rest of the band.
    stems = {name: (s * scale) * (1 - bleed) + bleed * (mix - s * scale) * 0.5 for name, s in clean.items()}
    song = dest_dir / "song.wav"
    audio.write(song, mix)
    return song, stems, truth
