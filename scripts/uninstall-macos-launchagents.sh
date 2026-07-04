#!/usr/bin/env bash
set -euo pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
SERVER_LABEL="com.jcbrooks.manuscript-workspace.server"
NGROK_LABEL="com.jcbrooks.manuscript-workspace.ngrok"
SERVER_PLIST="$LAUNCH_AGENTS_DIR/$SERVER_LABEL.plist"
NGROK_PLIST="$LAUNCH_AGENTS_DIR/$NGROK_LABEL.plist"
GUI_DOMAIN="gui/$(id -u)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This uninstaller is for macOS LaunchAgents only." >&2
  exit 2
fi

launchctl bootout "$GUI_DOMAIN" "$NGROK_PLIST" >/dev/null 2>&1 || true
launchctl bootout "$GUI_DOMAIN" "$SERVER_PLIST" >/dev/null 2>&1 || true

rm -f "$NGROK_PLIST" "$SERVER_PLIST"

echo "Uninstalled:"
echo "  $SERVER_PLIST"
echo "  $NGROK_PLIST"
