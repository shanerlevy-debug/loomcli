"""Shared command metadata for CLI help, plugins, and mobile clients."""
from __future__ import annotations

from typing import Any


COMMANDS: list[dict[str, Any]] = [
    {
        "command": "weave ask",
        "category": "agent",
        "summary": "Ask a Powerloom agent and stream the answer.",
        "status": "available",
        "args": ["agent", "prompt"],
        "options": ["--ou", "--title", "--raw-events", "--json"],
    },
    {
        "command": "weave chat",
        "category": "agent",
        "summary": "Start a terminal chat with a Powerloom agent.",
        "status": "available",
        "args": ["agent", "initial_prompt"],
        "options": ["--ou"],
    },
    {
        "command": "weave agent status",
        "category": "observability",
        "summary": "Show runtime/model, sync state, and recent work.",
        "status": "available",
        "args": ["agent"],
        "options": ["--ou", "--output"],
    },
    {
        "command": "weave agent config",
        "category": "configuration",
        "summary": "Show provider/runtime and model configuration.",
        "status": "available",
        "args": ["agent"],
        "options": ["--ou", "--output"],
    },
    {
        "command": "weave agent set-model",
        "category": "configuration",
        "summary": "Update an agent model through the Agent PATCH API.",
        "status": "available",
        "args": ["agent"],
        "options": ["--model", "--ou", "--output"],
    },
    {
        "command": "weave agent sessions",
        "category": "observability",
        "summary": "List recent sessions for one agent.",
        "status": "available",
        "args": ["agent"],
        "options": ["--ou", "--limit", "--output"],
    },
    {
        "command": "weave agent watch",
        "category": "observability",
        "summary": "Poll an agent status snapshot.",
        "status": "available",
        "args": ["agent"],
        "options": ["--ou", "--interval", "--once"],
    },
    {
        "command": "weave session events",
        "category": "observability",
        "summary": "Print durable events for a session.",
        "status": "available",
        "args": ["session_id"],
        "options": ["--after-seq", "--limit", "--output"],
    },
    {
        "command": "weave session tail",
        "category": "observability",
        "summary": "Poll durable session events.",
        "status": "available",
        "args": ["session_id"],
        "options": ["--after-seq", "--interval", "--limit", "--raw-events", "--once"],
    },
    {
        "command": "weave agent-session status",
        "category": "coordination",
        "summary": "Show one coordination session and assigned workflow tasks.",
        "status": "available",
        "args": ["session_id"],
        "options": ["--json"],
    },
    {
        "command": "weave agent-session watch",
        "category": "coordination",
        "summary": "Poll one coordination session and assigned workflow tasks.",
        "status": "available",
        "args": ["session_id"],
        "options": ["--interval", "--once"],
    },
    {
        "command": "weave thread my-work",
        "category": "coordination",
        "summary": "Show tracker threads assigned to, plucked by, or created by me.",
        "status": "available",
        "args": [],
        "options": ["--status", "--limit", "--watch", "--interval", "--once", "--json"],
    },
    {
        "command": "weave doctor",
        "category": "diagnostics",
        "summary": "Check local auth, server capabilities, and plugin prerequisites.",
        "status": "available",
        "args": [],
        "options": ["--json"],
    },
    {
        "command": "weave plugin doctor",
        "category": "diagnostics",
        "summary": "Check local plugin files and client binaries.",
        "status": "available",
        "args": ["client"],
        "options": ["--json"],
    },
    {
        "command": "weave plugin instructions",
        "category": "setup",
        "summary": "Print setup instructions for a client plugin.",
        "status": "available",
        "args": ["client"],
        "options": [],
    },
    {
        "command": "weave plugin install",
        "category": "setup",
        "summary": "Print or execute an install command for a client plugin.",
        "status": "available",
        "args": ["client"],
        "options": ["--execute"],
    },
    {
        "command": "weave profile show",
        "category": "configuration",
        "summary": "Show local CLI defaults.",
        "status": "available",
        "args": [],
        "options": ["--profile", "--json"],
    },
    {
        "command": "weave profile set",
        "category": "configuration",
        "summary": "Set local defaults for org, OU, agent, runtime, model, output, and API URL.",
        "status": "available",
        "args": [],
        "options": [
            "--profile",
            "--api-url",
            "--default-org",
            "--default-ou",
            "--default-agent",
            "--default-runtime",
            "--default-model",
            "--output",
        ],
    },
    {
        "command": "weave approval wait",
        "category": "approval",
        "summary": "Poll one approval request until it leaves pending.",
        "status": "available",
        "args": ["request_id"],
        "options": ["--interval", "--timeout", "--json"],
    },
    {
        "command": "weave apply",
        "category": "manifest",
        "summary": "Apply manifest resources.",
        "status": "available",
        "args": ["paths"],
        "options": ["--auto-approve"],
    },
]


def list_commands(prefix: str | None = None) -> list[dict[str, Any]]:
    rows = COMMANDS
    if prefix:
        rows = [row for row in rows if row["command"].startswith(prefix)]
    return sorted(rows, key=lambda row: row["command"])
