"""Agent observability commands.

These commands inspect live control-plane state. They deliberately do
not mutate Agent manifests or change provider/model configuration.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.commands.agent_cmd import (
    AgentResolutionError,
    _agent_label,
    _require_config,
    _resolve_agent,
)


app = typer.Typer(no_args_is_help=True, help="Inspect agents and their live work.")
_console = Console()

_ACTIVE_SESSION_STATUSES = {"pending", "running", "idle_end_turn"}


def status_command(
    agent: Annotated[
        str,
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ],
    ou: Annotated[
        str | None,
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
    output: Annotated[
        Literal["table", "json"],
        typer.Option("-o", "--output", help="Output format."),
    ] = "table",
) -> None:
    """Show an agent snapshot: runtime/model, sync state, and sessions."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        target = _resolve_agent(client, agent, ou=ou)
        snapshot = _agent_snapshot(client, target.id, target.row)
    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    if output == "json":
        _console.print_json(json.dumps(snapshot, default=str))
        return
    _print_status(snapshot)


def sessions_command(
    agent: Annotated[
        str,
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ],
    ou: Annotated[
        str | None,
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100, help="Maximum rows to print."),
    ] = 10,
    output: Annotated[
        Literal["table", "json"],
        typer.Option("-o", "--output", help="Output format."),
    ] = "table",
) -> None:
    """List recent sessions for one agent."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        target = _resolve_agent(client, agent, ou=ou)
        rows = _list_agent_sessions(client, target.id)[:limit]
    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    if output == "json":
        _console.print_json(json.dumps(rows, default=str))
        return
    _print_sessions(rows, title=f"{agent} sessions")


def watch_command(
    agent: Annotated[
        str,
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ],
    ou: Annotated[
        str | None,
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", min=1.0, help="Polling interval in seconds."),
    ] = 3.0,
    once: Annotated[
        bool,
        typer.Option("--once", help="Print one snapshot and exit."),
    ] = False,
) -> None:
    """Poll an agent status snapshot until interrupted."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        target = _resolve_agent(client, agent, ou=ou)
        while True:
            snapshot = _agent_snapshot(client, target.id, target.row)
            _console.print(_watch_line(snapshot))
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo()
    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()


def _agent_snapshot(
    client: PowerloomClient,
    agent_id: str,
    row_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agent = row_hint or {}
    try:
        agent = client.get(f"/agents/{agent_id}")
    except PowerloomApiError as e:
        if e.status_code == 404 and row_hint:
            agent = row_hint
        else:
            raise

    try:
        sync_status = client.get(f"/agents/{agent_id}/sync-status")
    except PowerloomApiError as e:
        if e.status_code == 404:
            sync_status = None
        else:
            raise

    sessions = _list_agent_sessions(client, agent_id)
    active = [
        s for s in sessions
        if str(s.get("status", "")).lower() in _ACTIVE_SESSION_STATUSES
    ]
    latest = sessions[0] if sessions else None
    return {
        "agent": agent,
        "label": _agent_label(agent),
        "sync_status": sync_status,
        "active_sessions": active,
        "latest_session": latest,
        "recent_sessions": sessions[:10],
    }


def _list_agent_sessions(
    client: PowerloomClient, agent_id: str
) -> list[dict[str, Any]]:
    rows = client.get("/sessions", agent_id=agent_id)
    if not isinstance(rows, list):
        return []
    return sorted(
        [r for r in rows if isinstance(r, dict)],
        key=lambda r: str(r.get("started_at", "")),
        reverse=True,
    )


def _print_status(snapshot: dict[str, Any]) -> None:
    agent = snapshot["agent"]
    latest = snapshot["latest_session"] or {}
    sync = snapshot["sync_status"] or {}

    table = Table(title="Agent status", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Agent", snapshot["label"])
    table.add_row("ID", str(agent.get("id", "")))
    table.add_row("Runtime", str(agent.get("runtime_type", "")))
    table.add_row("Model", str(agent.get("model", "")))
    table.add_row("Sync", _sync_label(sync))
    table.add_row("Active sessions", str(len(snapshot["active_sessions"])))
    table.add_row("Latest session", _session_label(latest))
    if latest.get("last_error"):
        table.add_row("Last error", str(latest["last_error"]))
    _console.print(table)


def _print_sessions(rows: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title, show_header=True)
    for col in ("id", "status", "mode", "title", "events", "tools", "started_at"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("id", "")),
            str(row.get("status", "")),
            str(row.get("mode", "")),
            str(row.get("title") or ""),
            str(row.get("event_count", "")),
            str(row.get("mcp_tool_use_count", "")),
            str(row.get("started_at", "")),
        )
    _console.print(table)


def _watch_line(snapshot: dict[str, Any]) -> str:
    latest = snapshot["latest_session"] or {}
    active_count = len(snapshot["active_sessions"])
    latest_label = _session_label(latest)
    return (
        f"{snapshot['label']} | active={active_count} | latest={latest_label} | "
        f"sync={_sync_label(snapshot['sync_status'] or {})}"
    )


def _session_label(row: dict[str, Any]) -> str:
    if not row:
        return "none"
    title = row.get("title") or row.get("prompt") or row.get("id")
    return (
        f"{row.get('status', 'unknown')} "
        f"events={row.get('event_count', 0)} "
        f"{str(title)[:80]}"
    )


def _sync_label(sync: dict[str, Any]) -> str:
    if not sync:
        return "unknown"
    for key in ("status", "sync_status", "state"):
        if sync.get(key):
            return str(sync[key])
    if sync.get("runtime_resource_id"):
        return "synced"
    return "unknown"


app.command("status", help="Show an agent status snapshot.")(status_command)
app.command("sessions", help="List recent sessions for an agent.")(sessions_command)
app.command("watch", help="Poll an agent status snapshot.")(watch_command)
