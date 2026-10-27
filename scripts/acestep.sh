#!/bin/bash
# The AI band: ACE-Step 1.5 (open source, MIT licence), running as a local server the app talks to.
#   scripts/acestep.sh install   download ACE-Step into ~/WorshipTracks/ACE-Step-1.5
#   scripts/acestep.sh run       run its server in this window (scripts/autostart.sh runs it for you)
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

# shellcheck disable=SC1091
[[ -f "$HOME/WorshipTracks/config.env" ]] && set -a && source "$HOME/WorshipTracks/config.env" && set +a
DIR="${WT_ACESTEP_DIR:-$HOME/WorshipTracks/ACE-Step-1.5}"
# The version this app was built and tested against.
VERSION="ca1e85fe9430179831e6bc6be790c332190a3866"
PORT="$(sed -E 's#.*:([0-9]+)/?$#\1#' <<< "${WT_ACESTEP_URL:-http://127.0.0.1:8001}")"

case "${1:-}" in
  install)
    if [[ ! -d "$DIR/.git" ]]; then
      git clone https://github.com/ace-step/ACE-Step-1.5.git "$DIR"
    fi
    git -C "$DIR" fetch --quiet origin
    git -C "$DIR" checkout --quiet "$VERSION"
    (cd "$DIR" && uv sync)
    echo "ACE-Step installed in $DIR"
    ;;
  run)
    if [[ ! -d "$DIR/.venv" ]]; then
      echo "ACE-Step isn't installed. Run: scripts/acestep.sh install" >&2
      exit 1
    fi
    memory_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
    # Its language model listens to songs to describe them. Bigger is better, if there's room.
    if (( memory_gb >= 32 )); then
      export ACESTEP_INIT_LLM=true ACESTEP_LM_MODEL_PATH="${WT_ACESTEP_LM:-acestep-5Hz-lm-1.7B}"
    elif (( memory_gb >= 16 )); then
      export ACESTEP_INIT_LLM=true ACESTEP_LM_MODEL_PATH="${WT_ACESTEP_LM:-acestep-5Hz-lm-0.6B}"
    else
      export ACESTEP_INIT_LLM=false  # too little memory: the app analyses songs itself instead
    fi
    export ACESTEP_LM_BACKEND=mlx  # Apple Silicon acceleration
    export ACESTEP_CONFIG_PATH="${WT_ACESTEP_MODEL:-acestep-v15-turbo}"
    export ACESTEP_USE_FLASH_ATTENTION=false
    export TOKENIZERS_PARALLELISM=false
    echo "Starting ACE-Step on port $PORT (${memory_gb} GB memory, model $ACESTEP_CONFIG_PATH)."
    echo "The first start downloads its models (about 10 GB), which can take a while."
    cd "$DIR"
    exec uv run acestep-api --host 127.0.0.1 --port "$PORT"
    ;;
  *)
    echo "Usage: scripts/acestep.sh install | run" >&2
    exit 1
    ;;
esac
