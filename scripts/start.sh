#!/bin/bash
# Runs the Worship Tracks server in this Terminal window (Ctrl-C to stop).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:$PATH"

# shellcheck disable=SC1091
[[ -f "$HOME/WorshipTracks/config.env" ]] && set -a && source "$HOME/WorshipTracks/config.env" && set +a
export PYTORCH_ENABLE_MPS_FALLBACK=1
PORT="${WT_PORT:-8000}"

host="$(scutil --get LocalHostName 2>/dev/null || hostname -s).local"
ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
echo "Worship Tracks is starting. Open it from any device on this network:"
echo "  http://$host:$PORT"
[[ -n "$ip" ]] && echo "  http://$ip:$PORT"

exec uv run uvicorn worship_tracks.app:create_app --factory --app-dir server \
  --host 0.0.0.0 --port "$PORT" --timeout-graceful-shutdown 5
