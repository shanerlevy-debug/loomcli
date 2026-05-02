"""MCP server config install for ``weave open``.

Sprint skills-mcp-bootstrap-20260430, thread d240bfd7.

For each entry in ``spec.mcp_config.servers``, write a project-local
``<worktree>/.mcp.json`` so Claude Code (and any compatible runtime)
sees Powerloom MCP tools the moment the agent boots in the worktree.
The spec's ``attach_token`` lives in the server's ``env`` block —
short-lived (1h) but the machine-credential refresh covers the next
launch automatically.

Skip the project-local write entirely when the user already has a
global Powerloom MCP server registered (typically at
``~/.claude/.mcp.json``). The launch-spec attach_token is fresher
than whatever's in the global config, but double-registering the
same server name confuses CC's MCP-server picker. Operators who
want the per-launch fresh token can opt out of the global config
or just delete it before relaunching.

Failure-mode policy: any IO / parse failure is non-fatal. The user
can hand-author ``.mcp.json`` if needed; the agent still boots.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loomcli.schema.launch_spec import LaunchSpec


# Project-local MCP config filename per Claude Code's project-scoped
# server discovery. Lives at the worktree root, NOT under .claude/.
PROJECT_MCP_FILENAME = ".mcp.json"


# Global config locations checked for an existing Powerloom server.
# Order matters: first match short-circuits.
GLOBAL_MCP_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".claude" / ".mcp.json",
    Path.home() / ".mcp.json",
)


# Heuristic for "is this entry a Powerloom MCP server?". Matches names
# starting with "powerloom" (case-insensitive). Tolerates variants
# like ``powerloom-platform`` / ``powerloom_home`` / ``powerloom``.
_POWERLOOM_SERVER_NAME_RE = re.compile(r"^powerloom", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class McpInstallResult:
    written_path: Optional[Path] = None
    """Path to the project-local .mcp.json that we wrote, or None when
    we skipped (already-globally-registered or empty spec)."""

    skipped_reason: Optional[str] = None
    """One of: ``"empty_spec"``, ``"global_powerloom_already_registered"``,
    or None when we wrote a fresh file."""

    error: Optional[str] = None
    """Human-readable failure summary; None on success."""

    server_names: list[str] = field(default_factory=list)
    """Names of MCP servers from the spec that ended up in the file."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def install_mcp_config(spec: LaunchSpec, worktree: Path) -> McpInstallResult:
    """Write ``<worktree>/.mcp.json`` from ``spec.mcp_config.servers``.

    Skips when the spec carries no MCP servers, OR when an existing
    global config already has a Powerloom server entry registered.
    """
    servers = spec.mcp_config.servers
    if not servers:
        return McpInstallResult(skipped_reason="empty_spec")

    if _global_has_powerloom_server():
        return McpInstallResult(
            skipped_reason="global_powerloom_already_registered",
            server_names=[s.name for s in servers],
        )

    # Build the .mcp.json shape CC expects.
    mcp_servers: dict[str, dict] = {}
    for srv in servers:
        entry: dict = {"command": srv.command}
        if srv.args:
            entry["args"] = list(srv.args)
        env = dict(srv.env) if srv.env else {}
        if srv.attach_token:
            # Convention used by the powerloom-platform / powerloom-home
            # MCP plugins to pick up auth without piping it through
            # CLI args (which leak into ps).
            env.setdefault("POWERLOOM_ATTACH_TOKEN", srv.attach_token)
        if env:
            entry["env"] = env
        mcp_servers[srv.name] = entry

    payload = {"mcpServers": mcp_servers}

    target = worktree / PROJECT_MCP_FILENAME
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        return McpInstallResult(
            error=f"could not write {target}: {exc}",
            server_names=[s.name for s in servers],
        )

    # Best-effort 0600 on POSIX so the attach_token isn't world-readable.
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return McpInstallResult(
        written_path=target,
        server_names=[s.name for s in servers],
    )


# ---------------------------------------------------------------------------
# Global-config Powerloom detection
# ---------------------------------------------------------------------------


def _global_has_powerloom_server() -> bool:
    """Return True iff any of the global MCP configs has a Powerloom server."""
    for candidate in GLOBAL_MCP_CANDIDATES:
        if _file_has_powerloom_server(candidate):
            return True
    return False


def _file_has_powerloom_server(path: Path) -> bool:
    """Parse ``path`` and look for a server whose name starts with 'powerloom'."""
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    return any(_POWERLOOM_SERVER_NAME_RE.match(str(name)) for name in servers)
