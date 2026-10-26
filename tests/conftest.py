import os
import shutil
from pathlib import Path

import pytest
from synthetic import make_song


def _find_soundfont() -> Path | None:
    candidates = [
        os.environ.get("WT_SOUNDFONT"),
        Path.home() / "WorshipTracks" / "soundfonts" / "GeneralUser-GS.sf2",
        "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    ]
    for c in candidates:
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None


@pytest.fixture(scope="session")
def soundfont() -> Path:
    sf2 = _find_soundfont()
    if sf2 is None or not shutil.which("fluidsynth") or not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg, fluidsynth and a SoundFont (run scripts/setup.sh)")
    return sf2


@pytest.fixture(scope="session")
def synthetic_song(soundfont, tmp_path_factory):
    """(song.wav path, perfect stems, ground-truth notes) for a 4-bar song."""
    return make_song(tmp_path_factory.mktemp("synthetic"), soundfont, bars=4)


@pytest.fixture
def fake_separator(synthetic_song):
    """Stands in for Demucs by handing back the song's real stems."""
    _, stems, _ = synthetic_song

    def separate(mix, on_progress):
        on_progress(1.0)
        return stems

    return separate
