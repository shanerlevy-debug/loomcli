"""Powerloom Home MCP server entry point.

Launched by Claude Code via `python -m powerloom_home` per the plugin
manifest's mcpServers config. Speaks MCP protocol over stdio.

Tools exposed (v0.1):
  - powerloom_list_ous / powerloom_get_ou / powerloom_create_ou
  - powerloom_list_skills / powerloom_get_skill / powerloom_create_skill
  - powerloom_list_agents / powerloom_get_agent / powerloom_create_agent
  - powerloom_apply_manifest   (parses YAML, validates, routes to create_*)
  - powerloom_recent_audit
  - powerloom_whoami

Tool input schemas mirror the Powerloom REST API's Pydantic models
(loomcli/manifest/schema.py) so manifests that apply via `weave apply`
against hosted Powerloom also apply against home via this MCP server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

try:
    from mcp import types as mcp_types
    from mcp.server import Server, NotificationOptions
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
except ImportError:
    print(
        "error: mcp SDK not installed. pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)

from .db import HomeDB


LOG_LEVEL = os.environ.get("POWERLOOM_HOME_LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("powerloom_home")


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

app = Server("powerloom-home")
db = HomeDB()
log.info("powerloom_home MCP server starting")
log.info("DB path: %s", db._path)


# ---------------------------------------------------------------------------
# Tool schemas (JSON Schema for each tool's input shape)
# ---------------------------------------------------------------------------


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    """Wrap a JSON-serializable payload as MCP TextContent."""
    return [
        mcp_types.TextContent(
            type="text",
            text=json.dumps(payload, indent=2, default=str),
        )
    ]


def _err(message: str) -> list[mcp_types.TextContent]:
    return [
        mcp_types.TextContent(
            type="text",
            text=json.dumps({"error": message}),
        )
    ]


TOOLS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="powerloom_whoami",
        description="Identify the current Powerloom home org + user. Useful for "
                    "confirming the MCP server is live + backed by the expected DB.",
        inputSchema={"type": "object", "properties": {}},
    ),
    mcp_types.Tool(
        name="powerloom_list_ous",
        description="List all OUs in the home org. Returns their paths + display names.",
        inputSchema={"type": "object", "properties": {}},
    ),
    mcp_types.Tool(
        name="powerloom_get_ou",
        description="Get one OU by path (e.g. /home/projects).",
        inputSchema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Absolute OU path."},
            },
        },
    ),
    mcp_types.Tool(
        name="powerloom_create_ou",
        description="Create (or get-if-exists) an OU. Idempotent by path.",
        inputSchema={
            "type": "object",
            "required": ["name", "display_name"],
            "properties": {
                "name": {"type": "string", "description": "DNS-safe slug, 1-63 chars."},
                "parent_path": {"type": "string", "description": "Parent OU path, or omit for a root OU."},
                "display_name": {"type": "string"},
            },
        },
    ),
    mcp_types.Tool(
        name="powerloom_list_skills",
        description="List skills, optionally filtered by ou_path.",
        inputSchema={
            "type": "object",
            "properties": {
                "ou_path": {"type": "string", "description": "Optional OU filter."},
            },
        },
    ),
    mcp_types.Tool(
        name="powerloom_get_skill",
        description="Get one skill by ID.",
        inputSchema={
            "type": "object",
            "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}},
        },
    ),
    mcp_types.Tool(
        name="powerloom_create_skill",
        description="Create a new skill. Archive content (for skill_type=archive) "
                    "is uploaded separately via powerloom_upload_skill_archive "
                    "(not yet implemented in v0.1).",
        inputSchema={
            "type": "object",
            "required": ["ou_path", "name", "display_name"],
            "properties": {
                "ou_path": {"type": "string"},
                "name": {"type": "string"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
                "skill_type": {
                    "type": "string",
                    "enum": ["archive", "tool_definition"],
                    "default": "archive",
                },
                "tool_schema": {"type": "object"},
                "system": {"type": "boolean", "default": False},
                "auto_attach_to": {
                    "type": "object",
                    "properties": {
                        "agent_kinds": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["user", "service"]},
                        },
                        "task_kinds": {
                            "type": "array",
                            "items": {"type": "string", "enum": [
                                "routing", "qa", "analogy", "execution", "coordination"
                            ]},
                        },
                        "runtime_types": {"type": "array", "items": {"type": "string"}},
                        "coordinator_role_required": {"type": "boolean"},
                    },
                },
            },
        },
    ),
    mcp_types.Tool(
        name="powerloom_list_agents",
        description="List agents, optionally filtered by ou_path.",
        inputSchema={
            "type": "object",
            "properties": {"ou_path": {"type": "string"}},
        },
    ),
    mcp_types.Tool(
        name="powerloom_get_agent",
        description="Get one agent by ID.",
        inputSchema={
            "type": "object",
            "required": ["agent_id"],
            "properties": {"agent_id": {"type": "string"}},
        },
    ),
    mcp_types.Tool(
        name="powerloom_create_agent",
        description="Create a new agent.",
        inputSchema={
            "type": "object",
            "required": [
                "ou_path", "name", "display_name", "model",
                "system_prompt", "owner_principal_ref",
            ],
            "properties": {
                "ou_path": {"type": "string"},
                "name": {"type": "string"},
                "display_name": {"type": "string"},
                "description": {"type": "string"},
                "model": {"type": "string"},
                "system_prompt": {"type": "string"},
                "owner_principal_ref": {
                    "type": "string",
                    "pattern": "^user:[^\\s]+@[^\\s]+$",
                },
                "agent_kind": {
                    "type": "string",
                    "enum": ["user", "service"],
                    "default": "user",
                },
                "coordinator_role": {"type": "boolean", "default": False},
                "task_kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [
                        "routing", "qa", "analogy", "execution", "coordination"
                    ]},
                },
                "memory_permissions": {"type": "array", "items": {"type": "string"}},
                "reranker_model": {"type": ["string", "null"]},
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Skill names in the same OU.",
                },
            },
        },
    ),
    mcp_types.Tool(
        name="powerloom_recent_audit",
        description="Recent audit-log entries (most recent first).",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
        },
    ),
]


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------


@app.list_tools()
async def _list_tools() -> list[mcp_types.Tool]:
    return TOOLS


@app.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any] | None):
    args = arguments or {}
    try:
        if name == "powerloom_whoami":
            return _ok({
                "mode": "home",
                "org_id": db.get_home_org_id(),
                "db_path": str(db._path),
                "version": "0.1.0-dev",
            })
        if name == "powerloom_list_ous":
            return _ok(db.list_ous())
        if name == "powerloom_get_ou":
            ou_id = db.resolve_ou_path(args["path"])
            if ou_id is None:
                return _err(f"OU path {args['path']!r} not found")
            return _ok(db.get_ou(ou_id))
        if name == "powerloom_create_ou":
            ou = db.create_ou(
                name=args["name"],
                parent_path=args.get("parent_path"),
                display_name=args["display_name"],
            )
            return _ok(ou)
        if name == "powerloom_list_skills":
            return _ok(db.list_skills(ou_path=args.get("ou_path")))
        if name == "powerloom_get_skill":
            skill = db.get_skill(args["skill_id"])
            if skill is None:
                return _err("skill not found")
            return _ok(skill)
        if name == "powerloom_create_skill":
            skill = db.create_skill(
                ou_path=args["ou_path"],
                name=args["name"],
                display_name=args["display_name"],
                description=args.get("description"),
                skill_type=args.get("skill_type", "archive"),
                tool_schema=args.get("tool_schema"),
                system=args.get("system", False),
                auto_attach_to=args.get("auto_attach_to"),
            )
            return _ok(skill)
        if name == "powerloom_list_agents":
            return _ok(db.list_agents(ou_path=args.get("ou_path")))
        if name == "powerloom_get_agent":
            agent = db.get_agent(args["agent_id"])
            if agent is None:
                return _err("agent not found")
            return _ok(agent)
        if name == "powerloom_create_agent":
            agent = db.create_agent(
                ou_path=args["ou_path"],
                name=args["name"],
                display_name=args["display_name"],
                model=args["model"],
                system_prompt=args["system_prompt"],
                owner_principal_ref=args["owner_principal_ref"],
                description=args.get("description"),
                agent_kind=args.get("agent_kind", "user"),
                coordinator_role=args.get("coordinator_role", False),
                task_kinds=args.get("task_kinds"),
                memory_permissions=args.get("memory_permissions"),
                reranker_model=args.get("reranker_model"),
                skills=args.get("skills"),
            )
            return _ok(agent)
        if name == "powerloom_recent_audit":
            return _ok(db.recent_audit(limit=args.get("limit", 50)))
        return _err(f"unknown tool {name!r}")
    except ValueError as e:
        return _err(str(e))
    except Exception as e:  # pragma: no cover — defense
        log.exception("tool %s failed", name)
        return _err(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="powerloom-home",
                server_version="0.1.0-dev",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
