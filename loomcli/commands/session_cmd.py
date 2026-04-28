"""Session inspection commands."""
from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.commands.agent_cmd import _extract_text_from_event, _require_config
from loomcli.config import is_json_output


app = typer.Typer(no_args_is_help=True, help="Inspect session event traces.")
_console = Console()


def events_command(
    session_id: Annotated[str, typer.Argument(help="Powerloom session UUID.")],
    after_seq: Annotated[
        int | None,
        typer.Option("--after-seq", min=0, help="Only return events after this sequence."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=500, help="Maximum events to return."),
    ] = 100,
) -> None:
    """Print durable events for a session."""
    cfg = _require_config()
    with PowerloomClient(cfg) as client:
        try:
            rows = _fetch_events(client, session_id, after_seq=after_seq, limit=limit)
        except PowerloomApiError as e:
            _console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from e

    if is_json_output():
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    _print_events_table(rows)


def tail_command(
    session_id: Annotated[str, typer.Argument(help="Powerloom session UUID.")],
    after_seq: Annotated[
        int,
        typer.Option("--after-seq", min=0, help="Start after this sequence."),
    ] = 0,
    interval: Annotated[
        float,
        typer.Option("--interval", min=1.0, help="Polling interval in seconds."),
    ] = 2.0,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=500, help="Maximum events per poll."),
    ] = 50,
    raw_events: Annotated[
        bool,
        typer.Option("--raw-events", help="Print raw event rows as JSON."),
    ] = False,
    once: Annotated[
        bool,
        typer.Option("--once", help="Poll once and exit."),
    ] = False,
) -> None:
    """Poll session events and print text deltas or concise event summaries."""
    cfg = _require_config()
    printed = 0
    with PowerloomClient(cfg) as client:
        try:
            while True:
                rows = _fetch_events(
                    client, session_id, after_seq=after_seq, limit=limit
                )
                for row in rows:
                    _print_event(row, raw_events=raw_events)
                    printed += 1
                    after_seq = max(after_seq, int(row.get("seq") or after_seq))
                if once:
                    if printed == 0:
                        _console.print("[dim]No events.[/dim]")
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            typer.echo()
        except PowerloomApiError as e:
            _console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from e


def _fetch_events(
    client: PowerloomClient,
    session_id: str,
    *,
    after_seq: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if after_seq is not None:
        params["after_seq"] = after_seq
    rows = client.get(f"/sessions/{session_id}/events", **params)
    return rows if isinstance(rows, list) else []


def _print_events_table(rows: list[dict[str, Any]]) -> None:
    table = Table(title=f"Session events - {len(rows)} row(s)", show_header=True)
    for col in ("seq", "event_type", "created_at", "summary"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("seq", "")),
            str(row.get("event_type", "")),
            str(row.get("created_at", "")),
            _event_summary(row),
        )
    _console.print(table)


def _print_event(row: dict[str, Any], *, raw_events: bool) -> None:
    if raw_events:
        typer.echo(json.dumps(row, default=str))
        return
    frame = {"type": row.get("event_type"), "payload": row.get("payload") or {}}
    text = _extract_text_from_event(frame)
    if text:
        typer.echo(text, nl=False)
        return
    _console.print(
        f"[dim]#{row.get('seq')} {row.get('event_type')}[/dim] "
        f"{_event_summary(row)}"
    )


def _event_summary(row: dict[str, Any]) -> str:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return ""
    frame = {"type": row.get("event_type"), "payload": payload}
    text = _extract_text_from_event(frame)
    if text:
        return text[:120]
    for key in ("status", "message", "reason", "last_error"):
        value = payload.get(key)
        if value:
            return str(value)[:120]
    if "payload" in payload and isinstance(payload["payload"], dict):
        nested = payload["payload"]
        for key in ("text", "message", "status"):
            value = nested.get(key)
            if value:
                return str(value)[:120]
    return json.dumps(payload, default=str)[:120]


app.command("events", help="Print durable events for a session.")(events_command)
app.command("tail", help="Poll and print session events.")(tail_command)
