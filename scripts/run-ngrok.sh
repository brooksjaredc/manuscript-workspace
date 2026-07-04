#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"
LOG_DIR="$HOME/Library/Logs/manuscript-workspace"

timestamp() {
  date +"%Y-%m-%dT%H:%M:%S%z"
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$(timestamp) ERROR: Missing $ENV_FILE. Copy .env.local.example to .env.local and edit it." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MANUSCRIPT_HOST="${MANUSCRIPT_HOST:-127.0.0.1}"
MANUSCRIPT_PORT="${MANUSCRIPT_PORT:-8000}"

if [[ -z "${MANUSCRIPT_PUBLIC_URL:-}" ]]; then
  echo "$(timestamp) ERROR: MANUSCRIPT_PUBLIC_URL is required in $ENV_FILE." >&2
  exit 2
fi

if ! command -v ngrok >/dev/null 2>&1; then
  echo "$(timestamp) ERROR: ngrok is not installed or not on PATH. Try: brew install ngrok" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"

LOCAL_HEALTH="http://$MANUSCRIPT_HOST:$MANUSCRIPT_PORT/health"
echo "$(timestamp) Waiting for Manuscript Workspace at $LOCAL_HEALTH"
for attempt in $(seq 1 90); do
  if curl -fsS "$LOCAL_HEALTH" >/dev/null 2>&1; then
    echo "$(timestamp) Local server is healthy."
    break
  fi
  if [[ "$attempt" == "90" ]]; then
    echo "$(timestamp) ERROR: Local server did not become healthy after 180 seconds." >&2
    exit 1
  fi
  sleep 2
done

PUBLIC_URL="${MANUSCRIPT_PUBLIC_URL%/}"
echo "$(timestamp) Starting ngrok: $PUBLIC_URL -> $MANUSCRIPT_HOST:$MANUSCRIPT_PORT"

exec ngrok http --url "$PUBLIC_URL" "$MANUSCRIPT_PORT"
