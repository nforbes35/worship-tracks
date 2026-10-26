"""A made-up song whose parts we know exactly, for testing transcription without real recordings.

Each part is rendered on its own with FluidSynth, which gives perfect "stems" to stand in
for Demucs, plus the ground-truth notes to score the transcription against.
"""

from pathlib import Path

import numpy as np
import pretty_midi
from worship_tracks import audio

BPM = 96
BEAT = 60 / BPM
# G  D  Em  C, one bar each.
CHORDS = [(43, [55, 59, 62]), (38, [54, 57, 62]), (40, [55, 59, 64]), (36, [55, 60, 64])]
MELODY = [  # (beat offset within the 4-bar phrase, length in beats, pitch)
    (0, 1, 74), (1, 1, 71), (2, 2, 74), (4, 1, 74), (5, 1, 76), (6, 2, 74),
    (8, 1, 71), (9, 1, 72), (10, 2, 71), (12, 2, 67), (14, 2, 69),
]

# Demucs stem name -> (GM program, is_drum)
PARTS = {
    "drums": (0, True),
    "bass": (33, False),
    "guitar": (25, False),
    "piano": (0, False),
    "other": (48, False),
    "vocals": (73, False),
}


def song_notes(bars: int) -> dict[str, list[pretty_midi.Note]]:
    notes: dict[str, list[pretty_midi.Note]] = {name: [] for name in PARTS}

    def add(part, pitch, start_beat, beats, velocity=96):
        start = start_beat * BEAT
        notes[part].append(pretty_midi.Note(velocity, pitch, start, start + beats * BEAT))

    for bar in range(bars):
        root, chord = CHORDS[bar % 4]
        b0 = bar * 4
        # Drums: kick 1 & 3, snare 2 & 4, eighth-note hats, crash every 4 bars.
        for beat in range(4):
            add("drums", 36 if beat % 2 == 0 else 38, b0 + beat, 0.25, 110)
            add("drums", 42, b0 + beat, 0.25, 80)
            add("drums", 42, b0 + beat + 0.5, 0.25, 70)
        if bar % 4 == 0:
            add("drums", 49, b0, 1, 100)
        # Bass: root on each beat.
        for beat in range(4):
            add("bass", root, b0 + beat, 0.9, 100)
        # Piano: the chord on beats 1 and 3.
        for beat in (0, 2):
            for p in chord:
                add("piano", p + 12, b0 + beat, 1.9, 80)
        # Guitar: quarter-note strums.
        for beat in range(4):
            for p in chord:
                add("guitar", p, b0 + beat, 0.9, 90)
        # Pads: the chord held all bar.
        for p in chord:
            add("other", p, b0, 4, 70)
        # Melody, repeating every 4 bars.
        if bar % 4 == 0:
            for offset, length, pitch in MELODY:
                if b0 + offset < bars * 4:
                    add("vocals", pitch, b0 + offset, length * 0.95, 100)
    return notes


def make_song(dest_dir: Path, soundfont: Path, bars: int = 8) -> tuple[Path, dict[str, np.ndarray], dict]:
    """Writes `song.wav` (the full mix) and returns (path, stems, ground-truth notes)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    truth = song_notes(bars)
    stems: dict[str, np.ndarray] = {}
    length = int((bars * 4 * BEAT + 2) * audio.SAMPLE_RATE)
    for name, (program, is_drum) in PARTS.items():
        midi = pretty_midi.PrettyMIDI(initial_tempo=BPM)
        inst = pretty_midi.Instrument(program=program, is_drum=is_drum)
        inst.notes = truth[name]
        midi.instruments.append(inst)
        midi_path = dest_dir / f"{name}.mid"
        midi.write(str(midi_path))
        wav = dest_dir / f"{name}.wav"
        audio.render_midi(midi_path, soundfont, wav)
        stem = audio.read(wav)
        stem = np.pad(stem, ((0, 0), (0, max(0, length - stem.shape[1]))))[:, :length]
        stems[name] = stem * 0.5
    mix = sum(stems.values())
    peak = np.abs(mix).max()
    if peak > 0.99:
        mix = mix / peak * 0.99
        stems = {k: v / peak * 0.99 for k, v in stems.items()}
    song = dest_dir / "song.wav"
    audio.write(song, mix)
    return song, stems, truth
