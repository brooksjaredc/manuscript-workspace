#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/manuscript-workspace"
SERVER_LABEL="com.jcbrooks.manuscript-workspace.server"
NGROK_LABEL="com.jcbrooks.manuscript-workspace.ngrok"
SERVER_PLIST="$LAUNCH_AGENTS_DIR/$SERVER_LABEL.plist"
NGROK_PLIST="$LAUNCH_AGENTS_DIR/$NGROK_LABEL.plist"
GUI_DOMAIN="gui/$(id -u)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This installer is for macOS LaunchAgents only." >&2
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: Missing $ENV_FILE. Copy .env.local.example to .env.local and edit it before installing LaunchAgents." >&2
  exit 2
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

cat >"$SERVER_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$SERVER_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO_ROOT/scripts/run-server.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/server.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/server.err.log</string>
</dict>
</plist>
PLIST

cat >"$NGROK_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$NGROK_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO_ROOT/scripts/run-ngrok.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/ngrok.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/ngrok.err.log</string>
</dict>
</plist>
PLIST

chmod 644 "$SERVER_PLIST" "$NGROK_PLIST"

launchctl bootout "$GUI_DOMAIN" "$SERVER_PLIST" >/dev/null 2>&1 || true
launchctl bootout "$GUI_DOMAIN" "$NGROK_PLIST" >/dev/null 2>&1 || true

launchctl bootstrap "$GUI_DOMAIN" "$SERVER_PLIST"
launchctl bootstrap "$GUI_DOMAIN" "$NGROK_PLIST"
launchctl kickstart -k "$GUI_DOMAIN/$SERVER_LABEL"
launchctl kickstart -k "$GUI_DOMAIN/$NGROK_LABEL"

echo "Installed and started:"
echo "  $SERVER_PLIST"
echo "  $NGROK_PLIST"
echo
echo "Logs:"
echo "  $LOG_DIR/server.out.log"
echo "  $LOG_DIR/server.err.log"
echo "  $LOG_DIR/ngrok.out.log"
echo "  $LOG_DIR/ngrok.err.log"
