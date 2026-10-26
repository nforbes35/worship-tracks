#!/bin/bash
# Makes the server start automatically when you log in, and restart if it ever stops.
#   scripts/autostart.sh install | uninstall | restart | status | logs
set -euo pipefail
cd "$(dirname "$0")/.."

LABEL="com.worshiptracks.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/WorshipTracks/logs"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
  install)
    mkdir -p "$(dirname "$PLIST")" "$LOGS"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PWD/scripts/start.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/server.log</string>
  <key>StandardErrorPath</key><string>$LOGS/server.log</string>
</dict>
</plist>
PLIST
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed. The server is running and will start whenever you log in."
    echo "Logs: $LOGS/server.log"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed. The server will no longer start by itself."
    ;;
  restart)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "Restarted."
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "state =|pid =" || echo "Not installed."
    ;;
  logs)
    tail -n 50 -f "$LOGS/server.log"
    ;;
  *)
    echo "Usage: scripts/autostart.sh install | uninstall | restart | status | logs" >&2
    exit 1
    ;;
esac
