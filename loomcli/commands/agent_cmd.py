"""Agentic CLI commands: `weave ask` and `weave chat`.

These commands deliberately do not call Anthropic/OpenAI/Gemini APIs
directly. They invoke a Powerloom Agent through the control plane; the
server chooses the runtime/model from the Agent row and uses the
user/org's configured runtime credential.
"""
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import typer
from rich.console import Console
from rich.prompt import Prompt

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import RuntimeConfig, load_runtime_config
from loomcli.manifest.addressing import AddressResolver


_console = Console()

_TERMINAL_EVENT_TYPES = {
    "session.status_idle",
    "session.status_terminated",
    "powerloom.session_ended",
    "powerloom.session_already_ended",
}


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


def ask_command(
    agent: Annotated[
        str,
        typer.Argument(
            help=(
                "Agent UUID, /ou/path/agent-name, or a unique agent name. "
                "Use --ou when the name is not globally unique."
            )
        ),
    ],
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
    """Ask one Powerloom agent a single question and stream the answer.

    Provider/model selection stays server-side: the target Agent's
    runtime_type + model determine which provider is used, and the
    control plane reads the matching user/org runtime credential.
    """
    user_prompt = _read_prompt(prompt)
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        target = _resolve_agent(client, agent, ou=ou)
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
        str,
        typer.Argument(
            help="Agent UUID, /ou/path/agent-name, or unique agent name."
        ),
    ],
    initial_prompt: Annotated[
        Optional[str],
        typer.Argument(help="Optional first prompt. Omit for interactive chat."),
    ] = None,
    ou: Annotated[
        Optional[str],
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
) -> None:
    """Start a lightweight terminal chat with a Powerloom agent.

    Each turn is a Powerloom Session. The previous session is passed as
    parent_session_id so the backend can link turn state while still
    letting provider/model choice remain an Agent configuration concern.
    """
    cfg = _require_config()
    client = PowerloomClient(cfg)
    try:
        target = _resolve_agent(client, agent, ou=ou)
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
