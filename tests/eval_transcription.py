"""Scores how well each instrument is transcribed, on the synthetic song.

    uv run python tests/eval_transcription.py [path/to/soundfont.sf2]

Prints note-level precision/recall/F1 (a note counts if pitch and start, within 50 ms
or 150 ms for pads, match).
"""

import sys
import tempfile
from pathlib import Path

import mir_eval
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from synthetic import make_song
from worship_tracks import audio
from worship_tracks.instruments import TRACKS
from worship_tracks.transcribe import transcribe_drums, transcribe_pitched


def score(truth, found, tolerance=0.05) -> tuple[float, float, float]:
    if not truth or not found:
        return 0.0, 0.0, 0.0
    ref_i = np.array([[n.start, n.end] for n in truth])
    ref_p = np.array([440 * 2 ** ((n.pitch - 69) / 12) for n in truth])
    est_i = np.array([[n.start, max(n.end, n.start + 0.01)] for n in found])
    est_p = np.array([440 * 2 ** ((n.pitch - 69) / 12) for n in found])
    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_i, ref_p, est_i, est_p, onset_tolerance=tolerance, offset_ratio=None
    )
    return p, r, f


def main() -> None:
    soundfont = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/sounds/sf2/FluidR3_GM.sf2")
    with tempfile.TemporaryDirectory() as tmp:
        _, stems, truth = make_song(Path(tmp), soundfont, bars=8)
        print(f"{'track':<14}{'notes':>7}{'found':>7}{'precision':>11}{'recall':>8}{'F1':>6}")
        for track in TRACKS:
            stem = stems[track.stem]
            if track.is_drum:
                found = transcribe_drums(stem, audio.SAMPLE_RATE)
            else:
                wav = Path(tmp) / f"{track.id}-stem.wav"
                audio.write(wav, stem)
                found = transcribe_pitched(str(wav), track)
            ref = truth[track.stem]
            # Strings and pads fade in, so their start is fuzzy.
            p, r, f = score(ref, found, 0.15 if track.merge_gap else 0.05)
            print(f"{track.label:<14}{len(ref):>7}{len(found):>7}{p:>11.2f}{r:>8.2f}{f:>6.2f}")
            if track.is_drum:
                for drum, name in ((36, "kick"), (38, "snare"), (42, "hi-hat"), (49, "crash")):
                    rp, rr, rf = score([n for n in ref if n.pitch == drum], [n for n in found if n.pitch == drum])
                    print(f"  {name:<12}{sum(n.pitch == drum for n in ref):>7}"
                          f"{sum(n.pitch == drum for n in found):>7}{rp:>11.2f}{rr:>8.2f}{rf:>6.2f}")


if __name__ == "__main__":
    main()
