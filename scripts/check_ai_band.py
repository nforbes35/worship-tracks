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


ACE_DIR = Path(os.environ.get("WT_ACESTEP_DIR", Path.home() / "WorshipTracks/ACE-Step-1.5"))
LOG = Path.home() / "WorshipTracks/logs/acestep.log"


def agent_running() -> bool:
    import subprocess

    result = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.worshiptracks.acestep"],
                            capture_output=True, text=True, check=False)
    return result.returncode == 0


def folder_size(path: Path) -> float:
    """Gigabytes in a folder (to show the model download moving)."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e9 if path.exists() else 0.0


def last_log_line() -> str:
    try:
        lines = [line for line in LOG.read_text(errors="replace").splitlines() if line.strip()]
    except FileNotFoundError:
        return ""
    return lines[-1][:150] if lines else ""


def main() -> int:
    engine = AceStep()
    if not engine.available():
        if not (ACE_DIR / ".venv").exists():
            print(f"ACE-Step isn't installed (nothing in {ACE_DIR}).")
            print("Run scripts/setup.sh and check it ends with \"Installing the AI band\" and \"Done\".")
            return 1
        if not agent_running():
            print("ACE-Step is installed but not running. Start it with:  scripts/autostart.sh install")
            return 1
    print(f"Waiting for ACE-Step at {engine.url} (the first start downloads about 10 GB)...")
    started = time.time()
    while not engine.available():
        if time.time() - started > 90 * 60:
            print(f"ACE-Step didn't start. See {LOG}")
            return 1
        time.sleep(15)
        if not agent_running():
            print(f"ACE-Step stopped. The end of {LOG} says why:\n  {last_log_line()}")
            return 1
        print(f"  still starting ({int(time.time() - started) // 60} min, "
              f"{folder_size(ACE_DIR / 'checkpoints'):.1f} GB of models so far)  {last_log_line()[:70]}", flush=True)

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
