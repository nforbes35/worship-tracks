#!/bin/bash
# One-time setup on the Mac that will run Worship Tracks. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This setup is for Apple Silicon Macs (M1 or newer)." >&2
  exit 1
fi

if ! command -v brew >/dev/null; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [[ -x $b ]] && eval "$($b shellenv)"
  done
fi
if ! command -v brew >/dev/null; then
  echo "Homebrew is needed first. Install it by pasting this into Terminal, then run this script again:"
  echo
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi

say "Installing ffmpeg, FluidSynth and uv"
brew install ffmpeg fluid-synth uv

say "Installing the AI libraries (a few minutes the first time)"
uv sync

DATA="$HOME/WorshipTracks"
mkdir -p "$DATA/soundfonts" "$DATA/logs"
if [[ ! -f "$DATA/config.env" ]]; then
  cp scripts/config.example "$DATA/config.env"
  echo "Created $DATA/config.env"
fi
# shellcheck disable=SC1091
source "$DATA/config.env"

if [[ ! -f "$WT_SOUNDFONT" ]]; then
  say "Downloading the instrument sounds (GeneralUser GS, about 30 MB)"
  curl -fL --progress-bar -o "$WT_SOUNDFONT.part" \
    "https://github.com/mrbumpy409/GeneralUser-GS/raw/main/GeneralUser-GS.sf2"
  mv "$WT_SOUNDFONT.part" "$WT_SOUNDFONT"
fi

say "Downloading the AI models (about 300 MB)"
uv run python -c "
import sys; sys.path.insert(0, 'server')
from worship_tracks import pipeline, transcribe
pipeline._get_separator(); transcribe._model()
print('Models ready. Separation will run on:', pipeline._device())
"

say "Checking everything works"
if ! uv run --group dev pytest -q -p no:warnings; then
  echo
  echo "Some checks failed. Copy everything above and send it to Claude to get it fixed." >&2
  exit 1
fi

say "Done"
echo "Start the server now with:   scripts/start.sh"
echo "Or have it start by itself:  scripts/autostart.sh install"
