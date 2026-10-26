"""Chart-guided parts.

With a chord chart, the AI no longer has to hear every note. It only decides which of
the chart's chords is playing on each beat, then plays the right notes of that chord in
the rhythm it hears each instrument play in the recording.
"""

import itertools
from dataclasses import dataclass

import numpy as np

from .chords import Chart, Chord, scale_pcs
from .rhythm import Grid, Hit
from .transcribe import Note

HOP = 512


@dataclass
class ChordSpan:
    start: float
    end: float
    chord: Chord | None  # None: no chord (silence, drums only)

    def to_dict(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "chord": self.chord.name if self.chord else None}


# ---------- finding the chords ----------

def recognise_chords(harmony: np.ndarray, bass: np.ndarray, sr: int, duration: float,
                     chart: Chart, beat_grid: Grid) -> list[ChordSpan]:
    """Which of the chart's chords is playing on each beat (a Viterbi search over beats)."""
    import librosa

    vocabulary = chart.vocabulary
    inside = beat_grid.beats[(beat_grid.beats > 0.05) & (beat_grid.beats < duration - 0.05)]
    grid = np.concatenate([[0.0], inside, [duration]])
    frames = librosa.time_to_frames(grid, sr=sr, hop_length=HOP)

    def synced_chroma(y: np.ndarray) -> np.ndarray:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP, norm=None)
        n = chroma.shape[1]
        cols = [chroma[:, a:b].mean(axis=1) if b > a else chroma[:, min(a, n - 1)]
                for a, b in zip(np.minimum(frames[:-1], n - 1), np.minimum(frames[1:], n))]
        return np.array(cols).T  # (12, beats)

    treble = synced_chroma(harmony)
    low = synced_chroma(bass) if np.abs(bass).max() > 1e-4 else np.zeros_like(treble)
    energy = np.array([np.sqrt(np.mean(harmony[int(a * sr):max(int(a * sr) + 1, int(b * sr))] ** 2))
                       for a, b in itertools.pairwise(grid)])
    silent = energy < max(1e-4, np.percentile(energy, 90) * 0.06)

    n_states = len(vocabulary) + 1  # last state: no chord
    beats = treble.shape[1]
    log_emit = np.zeros((beats, n_states))
    templates = np.array([[1.0 if pc in c.tones else 0.0 for pc in range(12)] for c in vocabulary])
    templates /= np.linalg.norm(templates, axis=1, keepdims=True)
    t_norm = treble / (np.linalg.norm(treble, axis=0, keepdims=True) + 1e-9)
    low_norm = low / (low.max(axis=0, keepdims=True) + 1e-9)
    for i, chord in enumerate(vocabulary):
        fit = templates[i] @ t_norm  # cosine similarity, 0..1
        bass_fit = low_norm[chord.bass]  # is the chord's bass note the strongest in the bass?
        log_emit[:, i] = 9.0 * fit + 1.5 * bass_fit
    log_emit[:, -1] = np.where(silent, 20.0, 0.0)
    log_emit[silent, :-1] -= 10.0

    # Transitions: chords usually last several beats; changes that the chart contains
    # (e.g. G -> D) are more likely than ones it doesn't.
    index = {c.name: i for i, c in enumerate(vocabulary)}
    seq = [index[name] for name in chart.chords if name in index]
    follows = np.ones((n_states, n_states))
    for a, b in itertools.pairwise(seq):
        if a != b:
            follows[a, b] += 3.0
    # Charts often write variants of one chord (C, Csus4, C2sus4). Hopping between those on
    # a held chord would re-strum it, so make those changes less likely than real ones.
    for a, ca in enumerate(vocabulary):
        for b, cb in enumerate(vocabulary):
            if a != b and ca.root == cb.root and ca.bass == cb.bass:
                follows[a, b] *= 0.3
    log_trans = np.log(np.full((n_states, n_states), 0.12 / n_states) * follows)
    np.fill_diagonal(log_trans, np.log(0.85))

    # Viterbi
    score = log_emit[0].copy()
    back = np.zeros((beats, n_states), dtype=int)
    for t in range(1, beats):
        options = score[:, None] + log_trans
        back[t] = options.argmax(axis=0)
        score = options.max(axis=0) + log_emit[t]
    path = [int(score.argmax())]
    for t in range(beats - 1, 0, -1):
        path.append(back[t, path[-1]])
    path.reverse()

    spans: list[ChordSpan] = []
    for t, state in enumerate(path):
        chord = vocabulary[state] if state < len(vocabulary) else None
        if spans and spans[-1].chord == chord:
            spans[-1].end = float(grid[t + 1])
        else:
            spans.append(ChordSpan(float(grid[t]), float(grid[t + 1]), chord))
    return spans


def chord_at(spans: list[ChordSpan], time: float) -> Chord | None:
    for span in spans:
        if span.start <= time < span.end:
            return span.chord
    return None


# ---------- playing the chords ----------

def onsets(y: np.ndarray, sr: int, fmin: float = 80.0, fmax: float = 4000.0,
           sensitivity: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """When an instrument plays (strums, hits), and how hard (0..1).

    Listens only to the instrument's own range, after removing drum-like noise, so drums,
    cymbals and other instruments leaking into the separated instrument don't read as hits."""
    import librosa

    if np.abs(y).max() < 1e-4:
        return np.array([]), np.array([])
    harmonic = librosa.effects.harmonic(y, margin=2.0)
    # Plain (not decibel) loudness: a quiet copy of another instrument leaking in then
    # makes a small bump instead of looking just as sharp as a real hit.
    mel = librosa.feature.melspectrogram(y=harmonic, sr=sr, hop_length=HOP, fmin=fmin, fmax=fmax,
                                         n_mels=int(np.clip((fmax - fmin) / 40, 8, 64)), power=1.0)
    env = librosa.onset.onset_strength(S=mel, sr=sr, hop_length=HOP)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, delta=sensitivity, wait=3)
    if not len(frames):
        return np.array([]), np.array([])
    strength = env[frames] / (np.percentile(env[frames], 95) + 1e-9)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    loud = rms[np.minimum(frames, len(rms) - 1)] > np.percentile(rms, 95) * 0.08
    return librosa.frames_to_time(frames[loud], sr=sr, hop_length=HOP), np.clip(strength[loud], 0, 1)


def _velocity(strength: float, low: int = 50, high: int = 110) -> int:
    return int(low + (high - low) * strength)


def _voicing_near(chord: Chord, center: int, count: int) -> list[int]:
    """`count` chord tones packed as closely as possible around `center`."""
    tones = sorted(chord.tones)
    candidates = sorted((p for p in range(center - 12, center + 13) if p % 12 in tones), key=lambda p: abs(p - center))
    return sorted(candidates[:count])


def guitar_voicing(chord: Chord) -> list[int]:
    """Roughly how a guitarist plays it: bass note low, chord tones stacked above."""
    bass = 40 + (chord.bass - 4) % 12  # E2..D#3
    notes = [bass]
    p = bass + 1
    while len(notes) < 6 and p <= 67:
        if p % 12 in chord.tones and p - notes[-1] >= 3:
            notes.append(p)
        p += 1
    return notes


def chord_hits(spans, hits: list[Hit], grid: Grid, voicing, end_of_song: float, strum: float = 0.0,
               up_strums: bool = False) -> list[Note]:
    """Play the current chord on each hit (already locked to the grid). Adds a hit where
    the chord changes if the player is playing but no hit was found there. With
    `up_strums`, hits between beats are lighter up-strums on the top strings."""
    by_slot = {h.slot: h for h in hits}
    slots = grid.slots
    for previous, span in itertools.pairwise(spans):
        if span.chord is None or not hits:
            continue
        if previous.chord and (previous.chord.root, previous.chord.bass) == (span.chord.root, span.chord.bass):
            continue  # just a variant (C to Csus4): no new strum needed
        slot = int(grid.snap([span.start])[0])
        playing = any(span.start - 2 * grid.beat_length < h.time < span.start for h in hits)
        if slot not in by_slot and playing:
            by_slot[slot] = Hit(slot, float(slots[slot]), 0.6)
    ordered = [by_slot[s] for s in sorted(by_slot)]

    notes: list[Note] = []
    for i, hit in enumerate(ordered):
        span = next((sp for sp in spans if sp.start <= hit.time + 0.03 < sp.end), None)
        if span is None or span.chord is None:
            continue
        end = min(ordered[i + 1].time if i + 1 < len(ordered) else end_of_song, span.end + 0.05,
                  hit.time + 4 * grid.beat_length)
        pitches = voicing(span.chord)
        velocity = _velocity(hit.strength)
        if up_strums and not grid.on_beat(hit.slot) and hit.slot % 2 == 1:
            pitches = list(reversed(pitches[-4:]))
            velocity = int(velocity * 0.8)
        for j, pitch in enumerate(pitches):
            start = hit.time + j * strum
            if end - start > 0.05:
                notes.append(Note(start, end, pitch, velocity))
    return notes


def pad_notes(spans, y: np.ndarray, sr: int) -> list[Note]:
    """Hold each chord for as long as it lasts, wherever the pads/other stem is sounding."""
    if np.abs(y).max() < 1e-4:
        return []
    level = np.sqrt(np.mean(y ** 2))
    notes = []
    previous: list[int] = []
    for span in spans:
        if span.chord is None:
            continue
        seg = y[int(span.start * sr):int(span.end * sr)]
        if seg.size == 0 or np.sqrt(np.mean(seg ** 2)) < level * 0.25:
            continue
        center = int(np.mean(previous)) if previous else 60
        voicing = _voicing_near(span.chord, center, 4)
        previous = voicing
        notes += [Note(span.start, span.end, p, 70) for p in voicing]
    return notes


def bass_line(spans, hits: list[Hit], grid: Grid, end_of_song: float) -> list[Note]:
    """The chord's bass note, in the rhythm the bass player plays, kept low (E1 to G2) and
    moving as little as possible between chords."""
    notes: list[Note] = []
    previous = 36
    with_changes = chord_hits(spans, hits, grid, lambda chord: [chord.bass], end_of_song)
    for note in with_changes:
        pitch = min((p for p in range(28, 44) if p % 12 == note.pitch % 12), key=lambda p: abs(p - previous))
        previous = pitch
        notes.append(Note(note.start, note.end, pitch, note.velocity))
    return notes


def lock_notes(notes: list[Note], grid: Grid, even_out: bool = False) -> list[Note]:
    """Snap notes to the grid, keeping one of each pitch per slot (the loudest). With
    `even_out`, loudness is pulled toward that pitch's usual level in the bar around it,
    so a groove doesn't lurch from hit to hit."""
    if not notes:
        return notes
    slots = grid.slots
    best: dict[tuple[int, int], Note] = {}
    for note, slot in zip(notes, grid.snap([n.start for n in notes])):
        key = (note.pitch, int(slot))
        if key not in best or note.velocity > best[key].velocity:
            start = float(slots[slot])
            best[key] = Note(start, max(start + 0.05, note.end), note.pitch, note.velocity)
    locked = sorted(best.values(), key=lambda n: (n.start, n.pitch))
    if even_out:
        window = 2 * grid.beat_length * max(2, 6 // grid.sub)
        originals = [n.velocity for n in locked]
        for note in locked:
            nearby = [v for other, v in zip(locked, originals)
                      if other.pitch == note.pitch and abs(other.start - note.start) <= window]
            note.velocity = int(round(0.4 * note.velocity + 0.6 * float(np.median(nearby))))
    return locked


def snap_to_key(notes: list[Note], key: str | None) -> list[Note]:
    scale = scale_pcs(key)
    if not scale:
        return notes
    for note in notes:
        if note.pitch % 12 not in scale:
            # Up or down a semitone always lands in the scale for a note outside it.
            note.pitch += 1 if (note.pitch - 1) % 12 not in scale else -1
    return notes
