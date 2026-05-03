#!/usr/bin/env bash
# Powerloom hook: session_start (Gemini / Antigravity runtime).
#
# Sprint 7 PR2 of agent-runtime milestone (5d3299f4), thread b060f366.
#
# Gemini Antigravity invokes hooks via its `agents.toml` lifecycle
# events (per docs/antigravitykb.md). Wire this script in by adding
# to your project's `~/.gemini/agents.toml`:
#
#   [hooks]
#   session_start = "~/.gemini/hooks/powerloom_session_start.sh"
#
# Configure via env vars (set in your shell rc or Antigravity's
# environment block):
#   POWERLOOM_API_BASE_URL=https://api.powerloom.org
#   POWERLOOM_AGENT_TOKEN=<agent's session token>
#   POWERLOOM_AGENT_ID=<this agent's UUID>
#
# Gemini sets GEMINI_SESSION_ID + GEMINI_AGENT_ID at hook fire.
#
# Best-effort: a hook failure must NOT block session start. We log
# to stderr and exit 0 even on POST failure.

set -u

POWERLOOM_BASE="${POWERLOOM_API_BASE_URL:-https://api.powerloom.org}"
POWERLOOM_TOKEN="${POWERLOOM_AGENT_TOKEN:-}"
POWERLOOM_AGENT="${POWERLOOM_AGENT_ID:-${GEMINI_AGENT_ID:-}}"

if [ -z "$POWERLOOM_TOKEN" ]; then
  echo "powerloom-hook: POWERLOOM_AGENT_TOKEN not set; skipping session_start" >&2
  exit 0
fi

GEMINI_SESSION="${GEMINI_SESSION_ID:-unknown}"
CWD="${PWD:-unknown}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

curl -sS \
  --max-time 5 \
  -X POST "${POWERLOOM_BASE}/agent-runtime/hooks/session_start" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${POWERLOOM_TOKEN}" \
  --data @- <<JSON >/dev/null 2>&1
{
  "session_id": "${GEMINI_SESSION}",
  "agent_id": "${POWERLOOM_AGENT}",
  "runtime": "gemini",
  "fired_at": "${NOW}",
  "payload": {
    "cwd": "${CWD}"
  }
}
JSON

exit 0
