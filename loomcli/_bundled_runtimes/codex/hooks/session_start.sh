#!/usr/bin/env bash
# Powerloom hook: session_start (Codex CLI runtime).
#
# Sprint 7 PR2 of agent-runtime milestone (5d3299f4), thread b060f366.
#
# Codex invokes this script when an agent session opens. We POST a
# JSON envelope to the Powerloom control plane so a SessionStart
# event lands in the work_chain. Downstream listeners (the upcoming
# session_end handler in PR3, project-state-loader, etc.) react to
# `agent_runtime.hook.session_start` events.
#
# Configure your Codex via:
#   POWERLOOM_API_BASE_URL=https://api.powerloom.org
#   POWERLOOM_AGENT_TOKEN=<agent's session token>
#   POWERLOOM_AGENT_ID=<this agent's UUID>
#
# Codex passes session metadata via env vars set on hook invocation
# (CODEX_SESSION_ID, CODEX_AGENT_ID, etc.). See
# https://github.com/openai/codex/docs/hooks.
#
# Best-effort: a hook failure must NOT block session start. We log
# to stderr and exit 0 even on POST failure so the session keeps
# loading.

set -u  # unset vars are errors; -e off so a curl failure exits clean

POWERLOOM_BASE="${POWERLOOM_API_BASE_URL:-https://api.powerloom.org}"
POWERLOOM_TOKEN="${POWERLOOM_AGENT_TOKEN:-}"
POWERLOOM_AGENT="${POWERLOOM_AGENT_ID:-${CODEX_AGENT_ID:-}}"

if [ -z "$POWERLOOM_TOKEN" ]; then
  echo "powerloom-hook: POWERLOOM_AGENT_TOKEN not set; skipping session_start" >&2
  exit 0
fi

CODEX_SESSION="${CODEX_SESSION_ID:-unknown}"
CWD="${PWD:-unknown}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

curl -sS \
  --max-time 5 \
  -X POST "${POWERLOOM_BASE}/agent-runtime/hooks/session_start" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${POWERLOOM_TOKEN}" \
  --data @- <<JSON >/dev/null 2>&1
{
  "session_id": "${CODEX_SESSION}",
  "agent_id": "${POWERLOOM_AGENT}",
  "runtime": "codex",
  "fired_at": "${NOW}",
  "payload": {
    "cwd": "${CWD}"
  }
}
JSON

# Always exit 0 — see best-effort note in header.
exit 0
