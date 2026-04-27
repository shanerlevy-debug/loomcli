"""Agentic CLI commands: `weave ask`, `weave chat`, and `weave agent ...`.

These commands invoke a Powerloom Agent through the control plane or
manage agent identities (sub-principals).
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import RuntimeConfig, load_runtime_config
from loomcli.manifest.addressing import AddressResolver


app = typer.Typer(no_args_is_help=True, help="Inspect agents and manage identities.")
sub_app = typer.Typer(no_args_is_help=True, help="Manage user sub-principal identities.")
app.add_typer(sub_app, name="sub-principal")

_console = Console()

_TERMINAL_EVENT_TYPES = {
    "session.status_idle",
    "session.status_terminated",
    "powerloom.session_ended",
    "powerloom.session_already_ended",
}

_ACTIVE_SESSION_STATUSES = {"pending", "running", "idle_end_turn"}


@dataclass
class AgentTarget:
    id: str
    label: str
    row: dict[str, Any] | None = None


@dataclass
class AgentInvokeResult:
    session_id: str
    assistant_text: str


class AgentResolutionError(Exception):
    pass


class AgentStreamError(Exception):
    pass


# ---------------------------------------------------------------------------
# Top-level commands (ask, chat)
# ---------------------------------------------------------------------------

def ask_command(
    agent: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Agent UUID, /ou/path/agent-name, or a unique agent name. "
                "Optional if an active coordination session is found for the branch."
            )
        ),
    ] = None,
    prompt: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Prompt to send. Omit to prompt interactively, or pass '-' "
                "to read stdin."
            )
        ),
    ] = None,
    ou: Annotated[
        Optional[str],
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
    title: Annotated[
        Optional[str],
        typer.Option("--title", help="Optional session title."),
    ] = None,
    raw_events: Annotated[
        bool,
        typer.Option(
            "--raw-events",
            help="Print raw WebSocket event frames instead of assistant text.",
        ),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Print invoke response JSON and skip streaming."),
    ] = False,
) -> None:
    """Ask one Powerloom agent a single question and stream the answer."""
    user_prompt = _read_prompt(prompt)
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        resolved_agent = agent
        resolved_ou = ou
        if not resolved_agent:
            from loomcli.commands.agent_session_cmd import get_active_session_for_branch
            active = get_active_session_for_branch(client)
            if active:
                # Use the agent bound to this coordination session
                resolved_agent = active.get("agent_id") or active.get("actor_id")
                # If it's a sub-principal ID, _resolve_agent handles it as a UUID
            if not resolved_agent:
                 _console.print("[red]AGENT argument is required (no active coordination session found for branch).[/red]")
                 raise typer.Exit(1)

        target = _resolve_agent(client, resolved_agent, ou=resolved_ou)
        result = _invoke_agent(
            client,
            cfg,
            target,
            prompt=user_prompt,
            title=title or _title_from_prompt(user_prompt),
            raw_events=raw_events,
            json_out=json_out,
        )
    except (AgentResolutionError, AgentStreamError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    if not json_out and not raw_events and not result.assistant_text.endswith("\n"):
        typer.echo()


def chat_command(
    agent: Annotated[
        Optional[str],
        typer.Argument(
            help="Agent UUID, /ou/path/agent-name, or unique agent name."
        ),
    ] = None,
    initial_prompt: Annotated[
        Optional[str],
        typer.Argument(help="Optional first prompt. Omit for interactive chat."),
    ] = None,
    ou: Annotated[
        Optional[str],
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
) -> None:
    """Start a lightweight terminal chat with a Powerloom agent."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        resolved_agent = agent
        if not resolved_agent:
            from loomcli.commands.agent_session_cmd import get_active_session_for_branch
            active = get_active_session_for_branch(client)
            if active:
                resolved_agent = active.get("agent_id") or active.get("actor_id")
            if not resolved_agent:
                 _console.print("[red]AGENT argument is required (no active coordination session found for branch).[/red]")
                 raise typer.Exit(1)

        target = _resolve_agent(client, resolved_agent, ou=ou)
        _console.print(f"[bold]Powerloom chat[/bold] -> {target.label}")
        _console.print("[dim]Type /exit or /quit to leave.[/dim]")

        parent_session_id: str | None = None
        pending_prompt = initial_prompt
        while True:
            if pending_prompt is None:
                try:
                    user_prompt = Prompt.ask("[bold cyan]you[/bold cyan]").strip()
                except (KeyboardInterrupt, EOFError):
                    typer.echo()
                    break
            else:
                user_prompt = pending_prompt.strip()
                pending_prompt = None

            if not user_prompt:
                continue
            if user_prompt.lower() in {"/exit", "/quit", "exit", "quit"}:
                break

            _console.print("[bold green]agent[/bold green] ", end="")
            result = _invoke_agent(
                client,
                cfg,
                target,
                prompt=user_prompt,
                title=_title_from_prompt(user_prompt),
                parent_session_id=parent_session_id,
            )
            parent_session_id = result.session_id
            if not result.assistant_text.endswith("\n"):
                typer.echo()
    except (AgentResolutionError, AgentStreamError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Agent Observability (migrated from agent_observe_cmd)
# ---------------------------------------------------------------------------

@app.command("status")
def status_command(
    agent: Annotated[
        Optional[str],
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ] = None,
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
        resolved_agent = agent
        if not resolved_agent:
            from loomcli.commands.agent_session_cmd import get_active_session_for_branch
            active = get_active_session_for_branch(client)
            if active:
                resolved_agent = active.get("agent_id") or active.get("actor_id")
            if not resolved_agent:
                 _console.print("[red]AGENT argument is required (no active coordination session found for branch).[/red]")
                 raise typer.Exit(1)

        target = _resolve_agent(client, resolved_agent, ou=ou)
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


@app.command("sessions")
def sessions_command(
    agent: Annotated[
        Optional[str],
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ] = None,
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
        resolved_agent = agent
        if not resolved_agent:
            from loomcli.commands.agent_session_cmd import get_active_session_for_branch
            active = get_active_session_for_branch(client)
            if active:
                resolved_agent = active.get("agent_id") or active.get("actor_id")
            if not resolved_agent:
                 _console.print("[red]AGENT argument is required (no active coordination session found for branch).[/red]")
                 raise typer.Exit(1)

        target = _resolve_agent(client, resolved_agent, ou=ou)
        rows = _list_agent_sessions(client, target.id)[:limit]
    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    if output == "json":
        _console.print_json(json.dumps(rows, default=str))
        return
    _print_sessions(rows, title=f"{resolved_agent} sessions")


@app.command("config")
def config_command(
    agent: Annotated[
        Optional[str],
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ] = None,
    ou: Annotated[
        str | None,
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
    output: Annotated[
        Literal["table", "json"],
        typer.Option("-o", "--output", help="Output format."),
    ] = "table",
) -> None:
    """Show provider/runtime and model configuration for one agent."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        resolved_agent = agent
        if not resolved_agent:
            from loomcli.commands.agent_session_cmd import get_active_session_for_branch
            active = get_active_session_for_branch(client)
            if active:
                resolved_agent = active.get("agent_id") or active.get("actor_id")
            if not resolved_agent:
                 _console.print("[red]AGENT argument is required (no active coordination session found for branch).[/red]")
                 raise typer.Exit(1)

        target = _resolve_agent(client, resolved_agent, ou=ou)
        row = client.get(f"/agents/{target.id}")
    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    if output == "json":
        _console.print_json(json.dumps(row, default=str))
        return

    table = Table(title="Agent config", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    for key in (
        "id",
        "name",
        "display_name",
        "runtime_type",
        "model",
        "agent_kind",
        "ou_id",
    ):
        table.add_row(key, str(row.get(key) or ""))
    _console.print(table)


@app.command("set-model")
def set_model_command(
    agent: Annotated[
        str,
        typer.Argument(help="Agent UUID, /ou/path/agent-name, or unique name."),
    ],
    model: Annotated[
        str,
        typer.Option("--model", help="New model value for the Agent row."),
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
    """Update an agent's model using the Agent PATCH API."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        target = _resolve_agent(client, agent, ou=ou)
        row = client.patch(f"/agents/{target.id}", {"model": model})
    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    if output == "json":
        _console.print_json(json.dumps(row, default=str))
        return
    _console.print(
        f"[green]Updated model[/green] {row.get('name') or target.id} "
        f"-> [bold]{row.get('model', model)}[/bold]"
    )


@app.command("watch")
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


# ---------------------------------------------------------------------------
# Sub-Principal Management
# ---------------------------------------------------------------------------

@sub_app.command("mint")
def subprincipal_mint_command(
    name: Annotated[
        str,
        typer.Option("--name", help="Friendly name for the agent (e.g. 'Shane-CC')."),
    ],
    description: Annotated[
        Optional[str],
        typer.Option("--description", help="Optional description of this agent."),
    ] = None,
    client_kind: Annotated[
        str,
        typer.Option("--client-kind", help="Source tool: claude_code, gemini_cli, etc."),
    ] = "gemini_cli",
    expires_in_days: Annotated[
        int,
        typer.Option("--expires-in", help="Token TTL in days."),
    ] = 90,
) -> None:
    """Register a new agent sub-principal and mint its first token."""
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        # Use existing /me/agents POST route
        payload = {
            "name": name,
            "description": description,
            "client_kind": client_kind,
        }
        res = client.post("/me/agents", payload)
        
        _console.print()
        _console.print(f"[green]Agent sub-principal '{name}' created.[/green]")
        _console.print(f"  [dim]id:[/dim] {res.get('id')}")
        _console.print(f"  [dim]principal_id:[/dim] {res.get('principal_id')}")
        
        key = res.get("first_key", {})
        _console.print()
        _console.print(f"[bold yellow]Initial Token (shown once 遯ｶ繝ｻcopy now):[/bold yellow]")
        _console.print(f"  {key.get('raw_token')}")
        _console.print()
        _console.print(
            "[dim]Use this value in your agent's configuration or "
            "with `weave login --pat <token>`.[/dim]"
        )
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _require_config() -> RuntimeConfig:
    cfg = load_runtime_config()
    if cfg.access_token is None:
        _console.print("[yellow]Not signed in. Run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    return cfg


def _read_prompt(prompt: str | None) -> str:
    if prompt == "-":
        text = sys.stdin.read().strip()
    elif prompt is None:
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        else:
            text = Prompt.ask("Prompt").strip()
    else:
        text = prompt.strip()
    if not text:
        raise typer.BadParameter("prompt is required")
    return text


def _resolve_agent(
    client: PowerloomClient, identifier: str, *, ou: str | None
) -> AgentTarget:
    if _looks_like_uuid(identifier):
        try:
            row = client.get(f"/agents/{identifier}")
        except PowerloomApiError as e:
            if e.status_code == 404:
                raise AgentResolutionError(f"agent not found: {identifier}") from e
            raise
        return AgentTarget(id=identifier, label=_agent_label(row), row=row)

    resolver = AddressResolver(client)
    if identifier.startswith("/"):
        parent, _, name = identifier.rstrip("/").rpartition("/")
        if not parent or not name:
            raise AgentResolutionError(
                "agent path must look like /ou/path/agent-name"
            )
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            raise AgentResolutionError(f"OU not found: {parent}")
        row = resolver.find_in_ou(list_path="/agents", ou_id=ou_id, name=name)
        if row is None:
            raise AgentResolutionError(f"agent not found: {identifier}")
        return AgentTarget(id=str(row["id"]), label=_agent_label(row), row=row)

    if ou:
        ou_id = resolver.try_ou_path_to_id(ou)
        if ou_id is None:
            raise AgentResolutionError(f"OU not found: {ou}")
        row = resolver.find_in_ou(list_path="/agents", ou_id=ou_id, name=identifier)
        if row is None:
            raise AgentResolutionError(f"agent {identifier!r} not found in {ou}")
        return AgentTarget(id=str(row["id"]), label=_agent_label(row), row=row)

    rows = client.get("/agents")
    matches = [
        r
        for r in rows if isinstance(r, dict)
        and (r.get("name") == identifier or r.get("display_name") == identifier)
    ]
    if len(matches) == 1:
        row = matches[0]
        return AgentTarget(id=str(row["id"]), label=_agent_label(row), row=row)
    if not matches:
        raise AgentResolutionError(
            f"agent {identifier!r} not found; pass a UUID, /ou/path/name, or --ou"
        )
    raise AgentResolutionError(
        f"agent name {identifier!r} is ambiguous ({len(matches)} matches); pass --ou"
    )


def _invoke_agent(
    client: PowerloomClient,
    cfg: RuntimeConfig,
    target: AgentTarget,
    *,
    prompt: str,
    title: str | None = None,
    parent_session_id: str | None = None,
    raw_events: bool = False,
    json_out: bool = False,
) -> AgentInvokeResult:
    body: dict[str, Any] = {
        "prompt": prompt,
        "mode": "fire_and_forget",
        "title": title,
    }
    if parent_session_id:
        body["parent_session_id"] = parent_session_id

    response = client.post(f"/agents/{target.id}/invoke", body)
    session_id = str(response.get("session_id", ""))
    if json_out:
        _console.print_json(json.dumps(response, default=str))
        return AgentInvokeResult(session_id=session_id, assistant_text="")

    ws_url = response.get("ws_url")
    if not isinstance(ws_url, str) or not ws_url:
        raise AgentStreamError(
            "invoke response did not include ws_url; cannot stream session"
        )
    text = _stream_session_events(
        _absolute_ws_url(cfg.api_base_url, ws_url),
        raw_events=raw_events,
    )
    return AgentInvokeResult(session_id=session_id, assistant_text=text)


def _stream_session_events(ws_url: str, *, raw_events: bool = False) -> str:
    try:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect
    except ImportError as e:
        raise AgentStreamError(
            "the websockets package is required for streaming; reinstall loomcli"
        ) from e

    text_parts: list[str] = []
    try:
        with connect(ws_url, close_timeout=2) as websocket:
            while True:
                try:
                    message = websocket.recv()
                except ConnectionClosed:
                    break
                try:
                    frame = json.loads(message)
                except json.JSONDecodeError:
                    if raw_events:
                        typer.echo(message)
                    continue
                if raw_events:
                    _console.print_json(json.dumps(frame, default=str))
                else:
                    text = _extract_text_from_event(frame)
                    if text:
                        text_parts.append(text)
                        sys.stdout.write(text)
                        sys.stdout.flush()
                if frame.get("type") in _TERMINAL_EVENT_TYPES:
                    break
    except OSError as e:
        raise AgentStreamError(f"WebSocket connection failed: {e}") from e
    return "".join(text_parts)


def _extract_text_from_event(frame: dict[str, Any]) -> str | None:
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return None

    for key in ("text", "delta", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    content = payload.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                parts.append(block["text"])
        if parts:
            return "".join(parts)

    return None


def _absolute_ws_url(api_base_url: str, ws_url: str) -> str:
    parsed_ws = urlparse(ws_url)
    if parsed_ws.scheme in {"ws", "wss"}:
        return ws_url
    http_url = urljoin(f"{api_base_url.rstrip('/')}/", ws_url.lstrip("/"))
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme))


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _agent_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "agent"
    name = (
        row.get("display_name")
        or row.get("display_title")
        or row.get("name")
        or row.get("id")
    )
    runtime = row.get("runtime_type")
    model = row.get("model")
    if runtime and model:
        return f"{name} [{runtime}:{model}]"
    if model:
        return f"{name} [{model}]"
    return str(name)


def _title_from_prompt(prompt: str) -> str:
    single_line = " ".join(prompt.split())
    return single_line[:80] or "weave ask"


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
