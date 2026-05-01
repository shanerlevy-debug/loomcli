"""Powerloom Platform MCP bridge — stdio→HTTP proxy entry point.

Launched by Claude Code via ``python -m powerloom_platform`` per the
plugin manifest's mcpServers config. Speaks MCP protocol over stdio
to CC; relays each request to ``{api_base_url}/mcp`` over HTTPS using
the deployment token from the credential file.

What this enables:

  * ``weave register --token=pat-deploy-...`` from any directory
    pairs the host with a Powerloom deployment.
  * Open Claude Code in any working directory.
  * Platform tools (sessions, threads, projects, agents, skills,
    memory, etc.) are available immediately — no per-project
    ``.mcp.json`` needed.

Graceful no-op modes:

  * No credential file exists → boot cleanly with empty tool list.
    Operator hasn't run ``weave register`` yet; nothing to do.
  * Credential malformed (missing ``deployment_token`` or
    ``api_base_url``) → same as above.
  * Upstream platform unreachable → the upstream connection fails on
    the first ``list_tools`` request; we surface the error in the
    tool-list response so the operator knows to check connectivity,
    but the MCP server itself stays alive (a transient outage
    shouldn't kill the CC session).
  * Token rejected (401) at upstream → expose zero tools (degraded
    state). Re-pair via ``weave register`` to restore.

Filed under tracker ``e1e61ca6``; companion to PR #290's CMA push
gating refactor (which closed tracker ``5dffcb15``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

try:
    from mcp import ClientSession
    from mcp import types as mcp_types
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server import NotificationOptions, Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
except ImportError:
    print(
        "error: mcp SDK not installed. pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)

from .credential import read_deployment_credential


LOG_LEVEL = os.environ.get("POWERLOOM_PLATFORM_LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("powerloom_platform")


# ---------------------------------------------------------------------------
# Server + upstream session state
# ---------------------------------------------------------------------------

app = Server("powerloom-platform")
log.info("powerloom_platform MCP bridge starting")


class _UpstreamState:
    """Holds the long-lived upstream MCP session (if any).

    The bridge maintains a single persistent connection to the
    platform so each tools/list and tools/call request is a fast
    round-trip rather than re-handshaking every time. The connection
    is opened lazily on first request (so missing-credential operators
    pay nothing at boot time) and torn down at exit.
    """

    def __init__(self) -> None:
        self.exit_stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.connection_attempted: bool = False
        self.connection_error: str | None = None


_upstream = _UpstreamState()


def _build_mcp_url(api_base_url: str) -> str:
    """Compose the platform MCP endpoint from the credential's
    ``api_base_url``.

    The credential file stores the API root (e.g. ``https://api.powerloom.org``);
    the MCP server lives at ``/mcp`` on that root. Mirrors the
    project-local ``.mcp.json`` URL convention used in the powerloom
    monorepo.
    """
    return f"{api_base_url.rstrip('/')}/mcp"


async def _ensure_upstream() -> ClientSession | None:
    """Open the upstream MCP session on first use.

    Returns ``None`` when no credential is configured OR the upstream
    rejected the handshake. Subsequent calls reuse ``_upstream.session``
    (success) or short-circuit to ``None`` (failure was final, retrying
    each request would burn token validation calls without helping).

    Connection errors are recorded on ``_upstream.connection_error`` so
    the bridge can surface them cleanly in tool-list responses.
    """
    if _upstream.session is not None:
        return _upstream.session
    if _upstream.connection_attempted:
        # Permanent miss (bad credential, 401, dead upstream); don't
        # keep retrying on every CC request. CC restart re-runs this
        # logic from scratch, so a transient outage clears on the
        # operator's next launch.
        return None

    _upstream.connection_attempted = True

    credential = read_deployment_credential()
    if credential is None:
        log.info(
            "powerloom_platform: no Claude Code deployment credential "
            "found. Bridge running in zero-tools mode. Pair this host "
            "with `weave register --token=...` to activate."
        )
        _upstream.connection_error = "no_credential"
        return None

    url = _build_mcp_url(credential["api_base_url"])
    headers = {"Authorization": f"Bearer {credential['deployment_token']}"}
    log.info("powerloom_platform: connecting upstream to %s", url)

    stack = AsyncExitStack()
    try:
        # streamablehttp_client yields (read_stream, write_stream, close_callback);
        # ClientSession wraps them in the MCP client protocol.
        read_stream, write_stream, _ = await stack.enter_async_context(
            streamablehttp_client(url, headers=headers)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
    except Exception as e:  # noqa: BLE001 — broad on purpose; upstream outages shouldn't kill us
        log.warning(
            "powerloom_platform: upstream handshake failed (%s: %s); "
            "bridge will expose zero tools until next CC restart.",
            type(e).__name__,
            e,
        )
        await stack.aclose()
        _upstream.connection_error = f"{type(e).__name__}: {e}"
        return None

    _upstream.exit_stack = stack
    _upstream.session = session
    log.info("powerloom_platform: upstream session ready")
    return session


# ---------------------------------------------------------------------------
# MCP handlers — proxy tools/list and tools/call to the upstream session
# ---------------------------------------------------------------------------


@app.list_tools()
async def _list_tools() -> list[mcp_types.Tool]:
    """Forward tools/list to the upstream platform.

    On no-credential: return an empty list (operator sees no tools
    rather than an error; matches CC's expectation of "tool servers
    that have no tools yet").

    On upstream failure: return an empty list AND log the error. We
    deliberately don't return a synthetic error-tool because some CC
    UIs render tool descriptions as user-visible text; surfacing
    "BRIDGE FAILED" as a tool name is uglier than just being silent.
    """
    session = await _ensure_upstream()
    if session is None:
        return []
    try:
        result = await session.list_tools()
    except Exception as e:  # noqa: BLE001
        log.warning("powerloom_platform: list_tools upstream error: %s", e)
        return []
    return list(result.tools)


@app.call_tool()
async def _call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[mcp_types.TextContent | mcp_types.ImageContent | mcp_types.EmbeddedResource]:
    """Forward tools/call to the upstream platform.

    Errors are surfaced as TextContent payloads so the calling agent
    sees a structured response rather than an MCP transport error
    (which CC tends to render as a hard failure across the whole
    session). The bridge stays available for subsequent calls.
    """
    session = await _ensure_upstream()
    if session is None:
        reason = _upstream.connection_error or "unknown"
        return [
            mcp_types.TextContent(
                type="text",
                text=json.dumps({
                    "error": "powerloom_platform bridge has no upstream session",
                    "reason": reason,
                    "remedy": (
                        "Run `weave register --token=pat-deploy-...` to pair "
                        "this host with a Powerloom deployment, then restart "
                        "Claude Code to activate the bridge."
                    ),
                }),
            )
        ]
    try:
        result = await session.call_tool(name, arguments or {})
    except Exception as e:  # noqa: BLE001
        log.warning("powerloom_platform: call_tool %s upstream error: %s", name, e)
        return [
            mcp_types.TextContent(
                type="text",
                text=json.dumps({
                    "error": "upstream call failed",
                    "tool": name,
                    "detail": f"{type(e).__name__}: {e}",
                }),
            )
        ]
    return list(result.content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def amain() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="powerloom-platform",
                    server_version="0.1.0-dev",
                    capabilities=app.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        if _upstream.exit_stack is not None:
            await _upstream.exit_stack.aclose()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
