"""Checks the AI band works on this Mac: waits for ACE-Step to start (its first start
downloads its models), has it play a short test song, and checks the result lines up.

    uv run python scripts/check_ai_band.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests")]

from synthetic import make_song
from worship_tracks import audio, backing
from worship_tracks.acestep import AceStep


def main() -> int:
    engine = AceStep()
    print(f"Waiting for ACE-Step at {engine.url} (the first start downloads about 10 GB)...")
    started = time.time()
    while not engine.available():
        if time.time() - started > 90 * 60:
            print("ACE-Step didn't start. See ~/WorshipTracks/logs/acestep.log")
            return 1
        time.sleep(10)
        print(f"  still starting ({int(time.time() - started) // 60} min)", flush=True)

    soundfont = Path(os.environ.get("WT_SOUNDFONT", Path.home() / "WorshipTracks/soundfonts/GeneralUser-GS.sf2"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        song, stems, _ = make_song(tmp / "song", soundfont, bars=12)
        source = tmp / "instrumental.flac"
        audio.write(source, backing.instrumental(stems))
        duration = sf_duration(source)
        print(f"Asking the AI band to play a {duration:.0f}-second test song...")
        t0 = time.time()
        out = tmp / "band.wav"
        engine.cover(source, out, caption="Instrumental. Modern worship band, acoustic guitar, piano, bass, live drums",
                     bpm=96, key="G Major", time_signature="4/4", duration=duration,
                     on_wait=lambda s: print(f"  playing ({int(s)} s)", flush=True) if int(s) % 20 < 2 else None)
        took = time.time() - t0
        converted = tmp / "band44.wav"
        audio.decode(out, converted)
        band = audio.read(converted)
        shift, drift = backing.align(audio.read(source).mean(axis=0), band.mean(axis=0), audio.SAMPLE_RATE,
                                     max_shift=0.45 * 60 / 96)
        print(f"Done in {took:.0f} s ({took / duration:.1f} s per second of music).")
        print(f"Length {band.shape[1] / audio.SAMPLE_RATE:.1f} s (asked for {duration:.1f}); "
              f"timing offset {shift * 1000:+.0f} ms.")
        keep = ROOT / "ai-band-check.wav"
        audio.write(keep, band)
        print(f"Listen to it: {keep}")
    print("The AI band works.")
    return 0


def sf_duration(path: Path) -> float:
    import soundfile as sf

    return sf.info(str(path)).duration


if __name__ == "__main__":
    sys.exit(main())
