#!/usr/bin/env bash
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"

pass_count=0
fail_count=0

pass() {
  echo "PASS: $1"
  pass_count=$((pass_count + 1))
}

fail() {
  echo "FAIL: $1"
  fail_count=$((fail_count + 1))
}

if [[ ! -f "$ENV_FILE" ]]; then
  fail "Missing $ENV_FILE. Copy .env.local.example to .env.local and edit it."
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

MANUSCRIPT_HOST="${MANUSCRIPT_HOST:-127.0.0.1}"
MANUSCRIPT_PORT="${MANUSCRIPT_PORT:-8000}"
PUBLIC_URL="${MANUSCRIPT_PUBLIC_URL:-}"
PUBLIC_URL="${PUBLIC_URL%/}"
LOCAL_HEALTH="http://$MANUSCRIPT_HOST:$MANUSCRIPT_PORT/health"

if local_health="$(curl -fsS "$LOCAL_HEALTH" 2>/dev/null)"; then
  pass "Local health endpoint responded: $LOCAL_HEALTH"
  echo "      $local_health"
else
  fail "Local health endpoint failed: $LOCAL_HEALTH"
fi

if [[ -z "$PUBLIC_URL" ]]; then
  fail "MANUSCRIPT_PUBLIC_URL is not set in $ENV_FILE"
else
  if public_health="$(curl -fsS "$PUBLIC_URL/health" 2>/dev/null)"; then
    pass "Public health endpoint responded: $PUBLIC_URL/health"
    echo "      $public_health"
  else
    fail "Public health endpoint failed: $PUBLIC_URL/health"
  fi

  initialize_payload='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"status-check","version":"0.1.0"}}}'
  if mcp_response="$(curl -fsS "$PUBLIC_URL/mcp" \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -d "$initialize_payload" 2>/dev/null)"; then
    if [[ "$mcp_response" == *'"result"'* && "$mcp_response" == *'"serverInfo"'* ]]; then
      pass "Public MCP initialize succeeded: $PUBLIC_URL/mcp"
    else
      fail "Public MCP initialize returned an unexpected response from $PUBLIC_URL/mcp"
      echo "      $mcp_response"
    fi
  else
    fail "Public MCP initialize failed: $PUBLIC_URL/mcp"
  fi

  if actions_schema="$(curl -fsS "$PUBLIC_URL/openapi.json" 2>/dev/null)"; then
    if [[ "$actions_schema" == *'"operationId":"read_document"'* || "$actions_schema" == *'"operationId": "read_document"'* ]]; then
      pass "Public Actions OpenAPI includes read_document: $PUBLIC_URL/openapi.json"
    else
      fail "Public Actions OpenAPI did not include read_document: $PUBLIC_URL/openapi.json"
      echo "      $actions_schema"
    fi
  else
    fail "Public Actions OpenAPI failed: $PUBLIC_URL/openapi.json"
  fi

  if [[ -n "${MANUSCRIPT_ACTIONS_BEARER_TOKEN:-}" ]]; then
    actions_response="$(curl -fsS "$PUBLIC_URL/actions/list_documents" \
      -H "Authorization: Bearer ${MANUSCRIPT_ACTIONS_BEARER_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{}' 2>/dev/null)"
    actions_status=$?
  else
    actions_response="$(curl -fsS "$PUBLIC_URL/actions/list_documents" \
      -H "Content-Type: application/json" \
      -d '{}' 2>/dev/null)"
    actions_status=$?
  fi
  if [[ "$actions_status" -eq 0 ]]; then
    if [[ "$actions_response" == *'"documents"'* ]]; then
      pass "Public Actions list_documents succeeded: $PUBLIC_URL/actions/list_documents"
    else
      fail "Public Actions list_documents returned an unexpected response from $PUBLIC_URL/actions/list_documents"
      echo "      $actions_response"
    fi
  else
    fail "Public Actions list_documents failed: $PUBLIC_URL/actions/list_documents"
  fi
fi

echo
echo "Status checks: $pass_count passed, $fail_count failed"

if [[ "$fail_count" -gt 0 ]]; then
  exit 1
fi
