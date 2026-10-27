"""The song's layout: where each section (intro, verses, choruses, bridge...) starts and
ends, plus its tempo, key, time signature and roughly which instruments play.

Section boundaries come from where the music's character changes (harmony, sound and
loudness, measured bar by bar). Names come from the chord chart when it lists the
sections in order, otherwise from the song itself: repeated sections with singing are
verses and choruses (the louder one is the chorus), a one-off sung section is the bridge.
"""

import re
from dataclasses import asdict, dataclass

import numpy as np

from .chords import Chart, parse_chord
from .rhythm import Grid

HOP = 512
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Krumhansl-Kessler key profiles.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_TAG_RE = re.compile(r"^\s*\[([A-Za-z][A-Za-z -]*\d*)\]\s*$")


@dataclass
class Section:
    start: float
    end: float
    name: str


@dataclass
class Layout:
    tempo: float
    key: str  # e.g. "C Major", "E Minor"
    time: str  # e.g. "6/8"
    sections: list[Section]
    instruments: str  # a description of the sound, e.g. "live drums, bass, acoustic guitar..."

    def to_dict(self) -> dict:
        return {**asdict(self), "tempo": round(self.tempo, 1)}


def key_name(key: str | None) -> str | None:
    """A chart key ("C", "Em") as ACE-Step writes keys ("C Major", "E Minor")."""
    chord = parse_chord(key) if key else None
    if chord is None:
        return None
    return f"{NOTE_NAMES[chord.root]} {'Minor' if chord.minor else 'Major'}"


def detect_key(harmony: np.ndarray, sr: int) -> str:
    import librosa

    chroma = librosa.feature.chroma_cqt(y=harmony, sr=sr, hop_length=HOP * 4).mean(axis=1)
    best, name = -2.0, "C Major"
    for root in range(12):
        for profile, mode in ((_MAJOR, "Major"), (_MINOR, "Minor")):
            r = float(np.corrcoef(chroma, np.roll(profile, root))[0, 1])
            if r > best:
                best, name = r, f"{NOTE_NAMES[root]} {mode}"
    return name


def describe_instruments(stems: dict[str, np.ndarray], mix_rms: float) -> str:
    """A plain description of what's in the song, from which separated stems have sound."""
    parts = [("drums", "live drums"), ("bass", "electric bass"), ("guitar", "acoustic and electric guitars"),
             ("piano", "piano"), ("other", "synth pads and keys")]
    present = [label for stem, label in parts
               if stem in stems and np.sqrt(np.mean(stems[stem] ** 2)) > mix_rms * 0.04]
    return "Modern worship band, " + ", ".join(present) if present else "Modern worship band"


def section_names_from_lyrics(lyrics: str) -> list[str]:
    """Section tags in order from ACE-Step's analysis, e.g. [Intro] [Verse 1] [Chorus]."""
    return [m[1].strip().title() for line in lyrics.splitlines() if (m := _TAG_RE.match(line))]


def find_sections(harmony: np.ndarray, vocals: np.ndarray, sr: int, duration: float, grid: Grid,
                  names: list[str] | None = None) -> list[Section]:
    import librosa

    lines = bar_lines(harmony, sr, grid)
    inside = lines[(lines > 0.5) & (lines < duration - 0.5)]
    if len(inside) < 3:
        return [Section(0.0, duration, "Song")]
    bars = np.concatenate([[0.0], inside])
    edges = np.append(bars, duration)
    frames = librosa.time_to_frames(edges, sr=sr, hop_length=HOP)

    chroma = librosa.feature.chroma_cqt(y=harmony, sr=sr, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=harmony, sr=sr, hop_length=HOP, n_mfcc=13)
    rms = librosa.feature.rms(y=harmony, hop_length=HOP)[0]
    vocal_rms = librosa.feature.rms(y=vocals, hop_length=HOP)[0] if vocals.size else np.zeros_like(rms)

    def per_bar(feature):
        n = feature.shape[-1]
        return np.array([feature[..., min(a, n - 1):max(min(b, n), min(a, n - 1) + 1)].mean(axis=-1)
                         for a, b in zip(frames[:-1], frames[1:])])

    c = per_bar(chroma)
    m = per_bar(mfcc)
    loud = per_bar(rms)
    sung = per_bar(vocal_rms)
    z = lambda x: (x - x.mean(axis=0)) / (x.std(axis=0) + 1e-9)
    features = np.hstack([z(c), z(m), 2 * z(loud[:, None])]).T

    k = len(names) if names else int(np.clip(round(duration / 22), 3, 14))
    k = min(k, len(bars))
    starts = sorted(set(librosa.segment.agglomerative(features, k).tolist()) | {0})
    # Real sections last at least two bars: fold anything shorter into the one before.
    starts = [s for i, s in enumerate(starts) if i == 0 or s - starts[i - 1] >= 2]
    if len(bars) - starts[-1] < 2 and len(starts) > 1:
        starts.pop()
    bounds = [*starts, len(bars)]
    segments = [(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]

    if names and len(names) == len(segments):
        labels = names
    else:
        labels = _name_sections(segments, features, loud, sung)
    return [Section(float(edges[a]), float(edges[b]), label) for (a, b), label in zip(segments, labels)]


def bar_lines(y: np.ndarray, sr: int, grid: Grid) -> np.ndarray:
    """Where each bar starts: every `per_bar` beats, counting from the beat where chords
    change and the low end (kick, bass) lands hardest, which is usually the first of the bar."""
    import librosa

    per_bar = max(1, grid.per_bar)
    if per_bar == 1:
        return grid.beats
    frames = np.clip(librosa.time_to_frames(grid.beats, sr=sr, hop_length=HOP), 0, None)
    low = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, fmax=250, n_mels=8)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
    n = min(len(low), chroma.shape[1])
    frames = np.minimum(frames, n - 1)
    hits = np.array([low[max(0, f - 2):f + 3].max() for f in frames])
    # How much the harmony after each beat differs from the harmony before it.
    span = max(2, int(np.median(np.diff(frames))))
    change = np.array([np.linalg.norm(chroma[:, f:f + span].mean(axis=1) - chroma[:, max(0, f - span):f].mean(axis=1))
                       if f > 0 else 0.0 for f in frames])
    z = lambda x: (x - x.mean()) / (x.std() + 1e-9)
    score = z(hits) + 2 * z(change)
    offset = max(range(per_bar), key=lambda o: score[o::per_bar].mean())
    return grid.beats[offset::per_bar]


def _name_sections(segments, features, loud, sung) -> list[str]:
    """Group sections that sound alike, then name them like a worship song chart."""
    means = [features[:, a:b].mean(axis=1) for a, b in segments]
    groups: list[int] = []
    for i, mean in enumerate(means):
        match = None
        for j in range(i):
            similarity = np.dot(mean, means[j]) / (np.linalg.norm(mean) * np.linalg.norm(means[j]) + 1e-9)
            if similarity > 0.75:
                match = groups[j]
                break
        groups.append(match if match is not None else max(groups, default=-1) + 1)

    vocal_level = np.percentile(sung, 75) if len(sung) else 0
    has_vocals = [sung[a:b].mean() > vocal_level * 0.35 for a, b in segments]
    energy = [loud[a:b].mean() for a, b in segments]
    counts = {g: groups.count(g) for g in set(groups)}
    repeated = [g for g in counts if counts[g] > 1 and any(v for v, gg in zip(has_vocals, groups) if gg == g)]
    chorus = max(repeated, key=lambda g: np.mean([e for e, gg in zip(energy, groups) if gg == g]), default=None)

    names = []
    for i, (group, vocal) in enumerate(zip(groups, has_vocals)):
        if not vocal:
            name = "Intro" if i == 0 else "Outro" if i == len(groups) - 1 else "Instrumental"
        elif group == chorus:
            name = "Chorus"
        elif group in repeated:
            name = "Verse"
        else:
            name = "Bridge"
        names.append(name)
    # Number repeats: Verse 1, Verse 2...
    totals = {n: names.count(n) for n in set(names)}
    seen: dict[str, int] = {}
    numbered = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        numbered.append(f"{name} {seen[name]}" if totals[name] > 1 and name in ("Verse", "Chorus", "Bridge") else name)
    return numbered


def chart_section_names(chart: Chart | None) -> list[str] | None:
    if not chart or not chart.sections:
        return None
    return [s.title() for s in chart.sections]
