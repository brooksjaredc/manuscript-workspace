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

if [[ -z "${MANUSCRIPT_ROOT:-}" ]]; then
  echo "$(timestamp) ERROR: MANUSCRIPT_ROOT is required in $ENV_FILE." >&2
  exit 2
fi

if [[ ! -d "$REPO_ROOT/.venv" ]]; then
  echo "$(timestamp) ERROR: Missing $REPO_ROOT/.venv. Run python3 -m venv .venv and install dependencies." >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

echo "$(timestamp) Starting Manuscript Workspace on http://$MANUSCRIPT_HOST:$MANUSCRIPT_PORT"
echo "$(timestamp) Manuscript root: $MANUSCRIPT_ROOT"

exec python -m manuscript_workspace \
  --root "$MANUSCRIPT_ROOT" \
  --host "$MANUSCRIPT_HOST" \
  --port "$MANUSCRIPT_PORT"
