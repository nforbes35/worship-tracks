"""Scores the whole pipeline on the live-style test song, with and without a chart.

    uv run python tests/eval_live.py [path/to/soundfont.sf2]

For each track it reports what a listener would notice: hits/strums that don't line up
with what was played (glitches), wrong notes, and bass notes up in the melody's range.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pretty_midi

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_song import CHART, EIGHTH, make_live_song
from worship_tracks import pipeline
from worship_tracks.chords import parse_chart_text, parse_chord


def onset_match(found: list[float], truth: list[float], tol: float = 0.05) -> tuple[float, float]:
    """(precision, recall) of onset times."""
    if not found or not truth:
        return 0.0, 0.0
    truth_arr = np.array(sorted(truth))
    hit = [np.min(np.abs(truth_arr - t)) <= tol for t in found]
    caught = [np.min(np.abs(np.array(found) - t)) <= tol for t in truth_arr]
    return float(np.mean(hit)), float(np.mean(caught))


def starts(notes, min_gap=0.03) -> list[float]:
    """Distinct onset times (a chord's notes count once)."""
    times = sorted(n.start for n in notes)
    out = []
    for t in times:
        if not out or t - out[-1] > min_gap:
            out.append(t)
    return out


def load(song_dir: Path, track: str):
    path = song_dir / "midi" / f"{track}.mid"
    return pretty_midi.PrettyMIDI(str(path)).instruments[0].notes if path.exists() else []


def chord_name_at(chords, t):
    return next((c["chord"] for c in chords if c["start"] <= t < c["end"]), None)


def report(song_dir: Path, result: dict, truth, with_chart: bool) -> None:
    print(f"\n=== {'with chart' if with_chart else 'no chart'} ===")
    if with_chart:
        checks = [(s + 0.1, name) for s, e, name in truth.chords] + [(e - 0.1, name) for s, e, name in truth.chords]
        right = sum(chord_name_at(result["chords"], t) == name for t, name in checks)
        changes = sum(1 for a, b in zip(result["chords"], result["chords"][1:]) if a["chord"] != b["chord"])
        print(f"chords: {right / len(checks):.0%} right, {changes} changes (song has {len(truth.chords) - 1})")

    drums = load(song_dir, "drums")
    true_drums = [n for n in truth.drums]
    p, r = onset_match([n.start for n in drums], [n.start for n in true_drums])
    flams = sum(1 for a, b in zip(sorted(drums, key=lambda n: (n.pitch, n.start)),
                                  sorted(drums, key=lambda n: (n.pitch, n.start))[1:])
                if a.pitch == b.pitch and 0 < b.start - a.start < EIGHTH * 0.6)
    print(f"drums: {len(drums)} hits (played {len(true_drums)}), {p:.0%} on a real hit, "
          f"{r:.0%} of hits caught, {flams} flams/stutters")

    guitar = load(song_dir, "guitar")
    g_starts = starts(guitar, 0.1)
    p, r = onset_match(g_starts, truth.strums)
    line = (f"guitar: {len(g_starts)} strums (played {len(truth.strums)}), {p:.0%} on a real strum, "
            f"{r:.0%} caught")
    if with_chart:
        wrong = sum(1 for n in guitar if (c := chord_name_at(result["chords"], n.start + 0.03))
                    and n.pitch % 12 not in parse_chord(c).tones)
        line += f", {wrong} notes outside the chord"
    print(line)

    keys = load(song_dir, "keys")
    k_starts = starts(keys)
    p, r = onset_match(k_starts, truth.keys_hits)
    print(f"keys: {len(k_starts)} hits (played {len(truth.keys_hits)}), {p:.0%} on a real hit, {r:.0%} caught")

    bass = load(song_dir, "bass")
    high = sum(n.pitch > 52 for n in bass)
    true_pcs = [(n.start, n.pitch % 12) for n in truth.bass]
    right = sum(1 for n in bass if any(abs(n.start - t) < 0.06 and n.pitch % 12 == pc for t, pc in true_pcs))
    p, r = onset_match(starts(bass), [n.start for n in truth.bass])
    print(f"bass: {len(bass)} notes (played {len(truth.bass)}), {right} right note at the right time, "
          f"{high} up in the melody's range, {p:.0%} on a real note")

    pads = load(song_dir, "pads")
    print(f"pads: {len(pads)} notes, {len(starts(pads, 0.1))} chord changes")

    melody = load(song_dir, "melody")
    p, r = onset_match(starts(melody), [n.start for n in truth.melody], 0.08)
    print(f"melody: {len(melody)} notes (sung {len(truth.melody)}), {p:.0%} on a real note, {r:.0%} caught")


def main() -> None:
    soundfont = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/sounds/sf2/FluidR3_GM.sf2")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        song, stems, truth = make_live_song(tmp / "song", soundfont)
        for with_chart in (False, True):
            out = tmp / ("chart" if with_chart else "plain")
            out.mkdir()
            result = pipeline.process_song(
                out, song, soundfont, lambda *_: None, separate_fn=lambda mix, progress: stems,
                chart=parse_chart_text(CHART) if with_chart else None,
            )
            report(out, result, truth, with_chart)


if __name__ == "__main__":
    main()
