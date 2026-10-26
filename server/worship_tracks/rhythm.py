"""The song's beat grid, and locking played hits onto it.

Detected hits wobble: live playing is a little loose, echoes and ringing notes trigger
extra detections, and leakage from other instruments adds more. Snapping everything to
the song's grid (with at most one hit per slot, and ignoring weak ones) is what turns
that into a part that sounds played rather than mashed.
"""

from dataclasses import dataclass

import numpy as np

HOP = 512
COMPOUND = {"6/8", "9/8", "12/8"}
BEATS_PER_BAR = {"2/4": 2, "3/4": 3, "4/4": 4, "6/8": 2, "9/8": 3, "12/8": 4}


@dataclass
class Grid:
    beats: np.ndarray  # beat times, from before the start to past the end
    sub: int  # slots per beat: 3 for 6/8 (eighths), 4 for 4/4 (sixteenths), 2 for eighths
    per_bar: int = 4  # beats per bar

    @property
    def slots(self) -> np.ndarray:
        steps = np.arange(self.sub) / self.sub
        starts = self.beats[:-1, None] + np.diff(self.beats)[:, None] * steps[None, :]
        return np.append(starts.ravel(), self.beats[-1])

    @property
    def beat_length(self) -> float:
        return float(np.median(np.diff(self.beats)))

    def on_beat(self, slot: int) -> bool:
        return slot % self.sub == 0

    def snap(self, times) -> np.ndarray:
        """Index of the nearest slot for each time."""
        slots = self.slots
        idx = np.clip(np.searchsorted(slots, times), 1, len(slots) - 1)
        left = slots[idx - 1]
        return np.where(np.abs(np.asarray(times) - left) <= np.abs(slots[idx] - np.asarray(times)), idx - 1, idx)


def make_grid(mix: np.ndarray, sr: int, duration: float, tempo: float | None = None,
              time: str | None = None) -> Grid:
    import librosa

    env = librosa.onset.onset_strength(y=mix, sr=sr, hop_length=HOP)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP, trim=False,
                                        start_bpm=tempo or 120.0)
    tracked = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    period = 60.0 / tempo if tempo else float(np.median(np.diff(tracked))) if len(tracked) > 4 else 0.5
    if time:
        sub = 3 if time in COMPOUND else 4
    else:
        sub = _guess_subdivision(tracked, env, sr) if len(tracked) > 4 else 4

    steady = _fit_steady(env, sr, period, sub, duration)
    if steady is not None:
        beats = _beat_phase(steady, sub, mix, sr)
    elif len(tracked) >= 4:  # the tempo moves (no click): follow the tracked beats
        beats = tracked
    else:  # no clear pulse at all
        beats = np.arange(0, duration + 0.5, 0.5)
    period = float(np.median(np.diff(beats)))
    # Extend to cover the whole song.
    before = beats[0] - period * np.arange(int(beats[0] / period) + 1, 0, -1)
    after = beats[-1] + period * np.arange(1, int((duration - beats[-1]) / period) + 2)
    return Grid(np.concatenate([before, beats, after]), sub, BEATS_PER_BAR.get(time or "", 4))


def _fit_steady(env: np.ndarray, sr: int, period: float, sub: int, duration: float) -> np.ndarray | None:
    """Songs played to a click keep one tempo. Try tempos within 3% of `period` and every
    starting point, and keep the steady grid that lines up best with what's played.
    Returns its beats, or None if the playing doesn't follow a steady grid."""
    frame_s = HOP / sr
    n = len(env)
    env = env / (env.max() + 1e-9)
    weights = np.where(np.arange(sub) == 0, 1.0, 0.5)  # beats count more than the slots between
    best = (-1.0, period, 0.0)
    for p in np.linspace(period * 0.97, period * 1.03, 121):
        count = int(duration / p)
        if count < 8:
            return None
        offsets = (np.arange(count)[:, None] * p + np.arange(sub)[None, :] * p / sub).ravel()
        w = np.tile(weights, count)
        phases = np.arange(0, p, frame_s)
        idx = np.clip(np.round((phases[:, None] + offsets[None, :]) / frame_s).astype(int), 0, n - 1)
        scores = (env[idx] * w).sum(axis=1) / w.sum()
        i = int(scores.argmax())
        if scores[i] > best[0]:
            best = (float(scores[i]), float(p), float(phases[i]))
    _, p, phase = best

    # Accept it only if most clear onsets in the song sit on the grid.
    import librosa

    peaks = librosa.util.peak_pick(env, pre_max=3, post_max=3, pre_avg=10, post_avg=10, delta=0.1, wait=3)
    peaks = peaks[env[peaks] > 0.15]
    if len(peaks) < 8:
        return None
    t = peaks * frame_s
    slot = p / sub
    off = np.abs(((t - phase) / slot) - np.round((t - phase) / slot)) * slot
    if np.mean(off < 0.045) < 0.7:
        return None
    return phase + p * np.arange(int((duration - phase) / p) + 1)


def _beat_phase(beats: np.ndarray, sub: int, mix: np.ndarray, sr: int) -> np.ndarray:
    """A steady grid can line up with the playing yet count beats from the wrong slot.
    Kick drum and bass land on the beat far more than between, so pick the slot offset
    where the low end hits hardest."""
    import librosa

    low = librosa.onset.onset_strength(y=mix, sr=sr, hop_length=HOP, fmax=250, n_mels=8)
    period = float(np.median(np.diff(beats)))
    best, best_score = beats, -1.0
    for k in range(sub):
        shifted = beats + k * period / sub
        frames = np.clip(librosa.time_to_frames(shifted, sr=sr, hop_length=HOP), 0, len(low) - 1)
        score = float(np.mean([low[max(0, f - 2):f + 3].max() for f in frames]))
        if score > best_score:
            best, best_score = shifted, score
    return best


def _guess_subdivision(beats: np.ndarray, env: np.ndarray, sr: int) -> int:
    """Without a time signature: do the notes between beats fall in thirds or quarters?"""
    import librosa

    def strength(fractions):
        times = (beats[:-1, None] + np.diff(beats)[:, None] * np.array(fractions)[None, :]).ravel()
        frames = np.clip(librosa.time_to_frames(times, sr=sr, hop_length=HOP), 0, len(env) - 1)
        return float(np.mean(env[frames]))

    thirds = strength([1 / 3, 2 / 3])
    halves = strength([1 / 2])
    quarters = strength([1 / 4, 3 / 4])
    if thirds > max(halves, quarters) * 1.15:
        return 3
    return 4 if quarters > halves * 0.6 else 2


@dataclass
class Hit:
    slot: int
    time: float
    strength: float  # 0..1


def lock_to_grid(times, strengths, grid: Grid, keep: float = 0.35, pattern: float = 0.0,
                 window_beats: float = 4) -> list[Hit]:
    """Snap hits to the grid, one per slot (the strongest), dropping hits that are weak
    next to the playing around them (echoes, ringing, leakage from other instruments).

    With `pattern`, also drop hits at places in the bar where, over the surrounding bars,
    the player only ever hits weakly compared with where they clearly play: players repeat
    a rhythm, while leakage and noise land wherever other instruments play."""
    if len(times) == 0:
        return []
    slots = grid.slots
    best: dict[int, float] = {}
    for slot, s in zip(grid.snap(times), strengths):
        best[int(slot)] = max(best.get(int(slot), 0.0), float(s))
    order = np.array(sorted(best))
    values = np.array([best[s] for s in order])
    span = int(window_beats * grid.sub)
    hits = []
    for slot, value in zip(order, values):
        nearby = values[(order >= slot - span) & (order <= slot + span)]
        threshold = keep * np.percentile(nearby, 90) * (0.6 if grid.on_beat(slot) else 1.0)
        if value >= threshold:
            hits.append(Hit(int(slot), float(slots[slot]), float(value)))
    if pattern and hits:
        hits = _follow_pattern(hits, grid, pattern)
    return hits


def _follow_pattern(hits: list[Hit], grid: Grid, pattern: float, window_bars: int = 8) -> list[Hit]:
    bar = grid.sub * grid.per_bar
    slots = np.array([h.slot for h in hits])
    strengths = np.array([h.strength for h in hits])
    kept = []
    for hit in hits:
        near = np.abs(slots - hit.slot) <= window_bars * bar
        bars = max(1, len({s // bar for s in slots[near]}))
        position = slots[near] % bar
        # How much playing each place in the bar gets, per bar.
        score = np.bincount(position, weights=strengths[near], minlength=bar) / bars
        if score[hit.slot % bar] >= pattern * score.max() or hit.strength >= 0.9 * strengths[near].max():
            kept.append(hit)
    return kept
