#!/usr/bin/env bash
# Powerloom hook: session_end (Gemini / Antigravity runtime).
#
# Sprint 7 PR2 of agent-runtime milestone (5d3299f4), thread b060f366.
#
# Wire in via `~/.gemini/agents.toml`:
#
#   [hooks]
#   session_end = "~/.gemini/hooks/powerloom_session_end.sh"
#
# Same env-var configuration + best-effort posture as session_start.sh.

set -u

POWERLOOM_BASE="${POWERLOOM_API_BASE_URL:-https://api.powerloom.org}"
POWERLOOM_TOKEN="${POWERLOOM_AGENT_TOKEN:-}"
POWERLOOM_AGENT="${POWERLOOM_AGENT_ID:-${GEMINI_AGENT_ID:-}}"

if [ -z "$POWERLOOM_TOKEN" ]; then
  echo "powerloom-hook: POWERLOOM_AGENT_TOKEN not set; skipping session_end" >&2
  exit 0
fi

GEMINI_SESSION="${GEMINI_SESSION_ID:-unknown}"
CWD="${PWD:-unknown}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EXIT_CODE="${GEMINI_EXIT_CODE:-0}"
DURATION="${GEMINI_SESSION_DURATION:-0}"

curl -sS \
  --max-time 5 \
  -X POST "${POWERLOOM_BASE}/agent-runtime/hooks/session_end" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${POWERLOOM_TOKEN}" \
  --data @- <<JSON >/dev/null 2>&1
{
  "session_id": "${GEMINI_SESSION}",
  "agent_id": "${POWERLOOM_AGENT}",
  "runtime": "gemini",
  "fired_at": "${NOW}",
  "payload": {
    "cwd": "${CWD}",
    "exit_code": ${EXIT_CODE},
    "duration_seconds": ${DURATION}
  }
}
JSON

exit 0
