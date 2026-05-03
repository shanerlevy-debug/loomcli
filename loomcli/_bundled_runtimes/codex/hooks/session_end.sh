#!/usr/bin/env bash
# Powerloom hook: session_end (Codex CLI runtime).
#
# Sprint 7 PR2 of agent-runtime milestone (5d3299f4), thread b060f366.
#
# Companion to session_start.sh. Fires when an agent session closes.
# Downstream handler (Sprint 7 PR3, e855230d) flushes pending tracker
# updates, emits a session.ended work-chain event, and appends a
# session-summary reply to plucked threads.
#
# See session_start.sh header for env-var configuration. Same
# best-effort posture (always exit 0 on failure).

set -u

POWERLOOM_BASE="${POWERLOOM_API_BASE_URL:-https://api.powerloom.org}"
POWERLOOM_TOKEN="${POWERLOOM_AGENT_TOKEN:-}"
POWERLOOM_AGENT="${POWERLOOM_AGENT_ID:-${CODEX_AGENT_ID:-}}"

if [ -z "$POWERLOOM_TOKEN" ]; then
  echo "powerloom-hook: POWERLOOM_AGENT_TOKEN not set; skipping session_end" >&2
  exit 0
fi

CODEX_SESSION="${CODEX_SESSION_ID:-unknown}"
CWD="${PWD:-unknown}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Codex sets these in the SessionEnd hook env per its contract.
EXIT_CODE="${CODEX_EXIT_CODE:-0}"
DURATION="${CODEX_SESSION_DURATION:-0}"

curl -sS \
  --max-time 5 \
  -X POST "${POWERLOOM_BASE}/agent-runtime/hooks/session_end" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${POWERLOOM_TOKEN}" \
  --data @- <<JSON >/dev/null 2>&1
{
  "session_id": "${CODEX_SESSION}",
  "agent_id": "${POWERLOOM_AGENT}",
  "runtime": "codex",
  "fired_at": "${NOW}",
  "payload": {
    "cwd": "${CWD}",
    "exit_code": ${EXIT_CODE},
    "duration_seconds": ${DURATION}
  }
}
JSON

exit 0
