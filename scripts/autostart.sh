#!/bin/bash
# Makes the app (and the AI band, if installed) start automatically when you log in,
# and restart if they ever stop.
#   scripts/autostart.sh install | uninstall | restart | status | logs [band]
set -euo pipefail
cd "$(dirname "$0")/.."

LOGS="$HOME/WorshipTracks/logs"
DOMAIN="gui/$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
# label, command, log file
SERVICES=("com.worshiptracks.server|$PWD/scripts/start.sh|server.log")
# shellcheck disable=SC1091
[[ -f "$HOME/WorshipTracks/config.env" ]] && set -a && source "$HOME/WorshipTracks/config.env" && set +a
if [[ -d "${WT_ACESTEP_DIR:-$HOME/WorshipTracks/ACE-Step-1.5}/.venv" ]]; then
  SERVICES+=("com.worshiptracks.acestep|$PWD/scripts/acestep.sh run|acestep.log")
fi

install_agent() {
  local label="$1" command="$2" log="$3"
  local plist="$AGENTS/$label.plist"
  local args=""
  for word in $command; do args+="<string>$word</string>"; done
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>$args</array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/$log</string>
  <key>StandardErrorPath</key><string>$LOGS/$log</string>
</dict>
</plist>
PLIST
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$plist"
}

case "${1:-}" in
  install)
    mkdir -p "$AGENTS" "$LOGS"
    for service in "${SERVICES[@]}"; do
      IFS="|" read -r label command log <<< "$service"
      install_agent "$label" "$command" "$log"
    done
    echo "Installed. Running now, and will start whenever you log in."
    [[ ${#SERVICES[@]} -gt 1 ]] && echo "The AI band is included (its first start downloads about 10 GB)."
    echo "Logs: $LOGS"
    ;;
  uninstall)
    for label in com.worshiptracks.server com.worshiptracks.acestep; do
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      rm -f "$AGENTS/$label.plist"
    done
    echo "Removed. Nothing will start by itself."
    ;;
  restart)
    for service in "${SERVICES[@]}"; do
      IFS="|" read -r label _ _ <<< "$service"
      launchctl kickstart -k "$DOMAIN/$label" 2>/dev/null || echo "$label isn't installed: run scripts/autostart.sh install"
    done
    echo "Restarted."
    ;;
  status)
    for label in com.worshiptracks.server com.worshiptracks.acestep; do
      echo "$label:"
      launchctl print "$DOMAIN/$label" 2>/dev/null | grep -E "^\s*(state|pid) =" || echo "  not installed"
    done
    ;;
  logs)
    if [[ "${2:-}" == "band" ]]; then tail -n 50 -f "$LOGS/acestep.log"; else tail -n 50 -f "$LOGS/server.log"; fi
    ;;
  *)
    echo "Usage: scripts/autostart.sh install | uninstall | restart | status | logs [band]" >&2
    exit 1
    ;;
esac
