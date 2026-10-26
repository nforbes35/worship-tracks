"""Chart-guided parts.

With a chord chart, the AI no longer has to hear every note. It only decides which of
the chart's chords is playing on each beat, then plays the right notes of that chord in
the rhythm it hears each instrument play in the recording.
"""

import itertools
from dataclasses import dataclass

import numpy as np

from .chords import Chart, Chord, scale_pcs
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

def beat_times(mix: np.ndarray, sr: int, duration: float) -> np.ndarray:
    import librosa

    _, beats = librosa.beat.beat_track(y=mix, sr=sr, hop_length=HOP, units="time", trim=False)
    if len(beats) < 4:  # free time or no clear pulse: fall back to half-second steps
        beats = np.arange(0, duration, 0.5)
    # Cover the whole song, from 0 to the end.
    step = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    before = np.arange(beats[0] - step, 0, -step)[::-1] if beats[0] > step * 0.5 else np.array([])
    after = np.arange(beats[-1] + step, duration, step)
    grid = np.concatenate([[0.0], before[before > 0.05], beats, after, [duration]])
    return np.unique(np.round(grid, 4))


def recognise_chords(harmony: np.ndarray, bass: np.ndarray, mix: np.ndarray, sr: int,
                     duration: float, chart: Chart) -> list[ChordSpan]:
    """Which of the chart's chords is playing on each beat (a Viterbi search over beats)."""
    import librosa

    vocabulary = chart.vocabulary
    grid = beat_times(mix, sr, duration)
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

def onsets(y: np.ndarray, sr: int, sensitivity: float = 0.08) -> tuple[np.ndarray, np.ndarray]:
    """When an instrument plays (strums, hits), and how hard (0..1)."""
    import librosa

    if np.abs(y).max() < 1e-4:
        return np.array([]), np.array([])
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, delta=sensitivity, wait=4)
    strength = env[frames] / (np.percentile(env[frames], 95) + 1e-9) if len(frames) else np.array([])
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    loud = rms[np.minimum(frames, len(rms) - 1)] > np.percentile(rms, 95) * 0.08 if len(frames) else []
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


def chord_hits(spans, times, strengths, voicing, end_of_song, strum=0.0, fill_changes=True,
               max_len=2.5) -> list[Note]:
    """Play the current chord at every hit, adding a hit on a chord change if the player
    was playing but the change didn't line up with a detected hit."""
    times = list(times)
    strengths = list(strengths)
    if fill_changes and times:
        for span in spans[1:]:
            if span.chord is None:
                continue
            near = min(abs(t - span.start) for t in times)
            playing = any(span.start - 2.0 < t < span.start for t in times)
            if near > 0.12 and playing:
                times.append(span.start)
                strengths.append(0.6)
    order = np.argsort(times)
    times = [times[i] for i in order]
    strengths = [strengths[i] for i in order]
    notes: list[Note] = []
    for i, (t, s) in enumerate(zip(times, strengths)):
        chord = chord_at(spans, t + 0.03)
        if chord is None:
            continue
        span_end = next(sp.end for sp in spans if sp.start <= t + 0.03 < sp.end)
        end = min(times[i + 1] if i + 1 < len(times) else end_of_song, span_end + 0.05, t + max_len)
        for j, pitch in enumerate(voicing(chord)):
            start = t + j * strum
            if end - start > 0.05:
                notes.append(Note(start, end, pitch, _velocity(s)))
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


def snap_bass(notes: list[Note], spans) -> list[Note]:
    """Move bass notes that aren't in the chord to the nearest chord tone."""
    for note in notes:
        chord = chord_at(spans, note.start + 0.03)
        if chord is None or note.pitch % 12 in chord.tones:
            continue
        options = [note.pitch + d for d in range(-6, 7) if (note.pitch + d) % 12 in chord.tones]
        # Prefer the chord's bass note when it's just as close.
        note.pitch = min(options, key=lambda p: (abs(p - note.pitch), p % 12 != chord.bass))
    return notes


def bass_on_changes(notes: list[Note], spans, bass_y: np.ndarray, sr: int) -> list[Note]:
    """Make sure the bass plays each chord's bass note when the chord changes, if the
    bass player is playing there at all."""
    if np.abs(bass_y).max() < 1e-4:
        return notes
    level = np.sqrt(np.mean(bass_y ** 2))
    added = []
    for span in spans:
        if span.chord is None or any(abs(n.start - span.start) < 0.15 for n in notes):
            continue
        seg = bass_y[int(span.start * sr):int(min(span.end, span.start + 0.5) * sr)]
        if seg.size and np.sqrt(np.mean(seg ** 2)) > level * 0.5:
            pitch = 28 + (span.chord.bass - 4) % 12  # E1..D#2
            added.append(Note(span.start, min(span.end, span.start + 1.5), pitch, 95))
    from .transcribe import make_monophonic

    return make_monophonic(sorted(notes + added, key=lambda n: n.start))


def snap_to_key(notes: list[Note], key: str | None) -> list[Note]:
    scale = scale_pcs(key)
    if not scale:
        return notes
    for note in notes:
        if note.pitch % 12 not in scale:
            # Up or down a semitone always lands in the scale for a note outside it.
            note.pitch += 1 if (note.pitch - 1) % 12 not in scale else -1
    return notes
