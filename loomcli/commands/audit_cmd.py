"""`weave audit` — query the Powerloom audit log.

Shipped in v0.5.5 after Shane asked for an "audit log parser" —
the Web UI + raw API return audit rows but reading them at scale is
painful. This wraps `GET /audit` with sane defaults and human-readable
output.

Typical uses:
    weave audit                                    # last hour, all kinds
    weave audit --since 24h                        # last day
    weave audit --kind skill --action create       # skill creates only
    weave audit --actor user:shane@example.com     # what did shane do?
    weave audit --approval-request <id>            # traces around an approval
    weave -o json audit                            # machine-readable

Filters chain with AND. Output defaults to a human-readable table
with column-wrapping, not the raw JSON.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import is_json_output, load_runtime_config

app = typer.Typer(help="Query the Powerloom audit log.")
_console = Console()


_DURATION_SUFFIXES = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_since(expr: str) -> datetime:
    """Parse expressions like '1h', '24h', '7d', '30m' into a UTC
    datetime. Raises ValueError on unrecognized input."""
    expr = expr.strip().lower()
    if not expr:
        raise ValueError("empty --since")
    unit = expr[-1]
    if unit not in _DURATION_SUFFIXES:
        # Fall back to ISO 8601.
        try:
            dt = datetime.fromisoformat(expr)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError as e:
            raise ValueError(
                f"--since {expr!r} isn't a duration (e.g. 1h, 7d) or ISO 8601"
            ) from e
    try:
        amount = int(expr[:-1])
    except ValueError as e:
        raise ValueError(
            f"--since {expr!r}: number before unit must be an integer"
        ) from e
    delta_s = amount * _DURATION_SUFFIXES[unit]
    return datetime.now(timezone.utc) - timedelta(seconds=delta_s)


def _format_timestamp(iso_str: str) -> str:
    """Display timestamps as compact local-ish format."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return str(iso_str)


def _summarize_payload(payload: Any, max_len: int = 60) -> str:
    """One-line summary of a before/after JSON payload, truncated."""
    if payload is None:
        return "-"
    if isinstance(payload, dict):
        # Common fields worth highlighting first.
        for key in ("name", "display_name", "id", "status"):
            if key in payload and payload[key]:
                val = str(payload[key])
                if len(val) > max_len:
                    val = val[:max_len - 1] + "…"
                return f"{key}={val}"
        # Fallback: first 60 chars of JSON repr.
        s = json.dumps(payload, separators=(",", ":"))
        return (s[:max_len - 1] + "…") if len(s) > max_len else s
    s = str(payload)
    return (s[:max_len - 1] + "…") if len(s) > max_len else s


@app.callback(invoke_without_command=True)
def audit(
    since: str = typer.Option(
        "1h",
        "--since", "-s",
        help="Lookback window. Accepts '1h', '24h', '7d', '30m', or ISO 8601.",
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", "-k",
        help="Filter by resource_kind (e.g. 'skill', 'agent', 'approval_request').",
    ),
    action: Optional[str] = typer.Option(
        None, "--action", "-a",
        help="Filter by action_verb (e.g. 'create', 'update', 'delete', 'approve').",
    ),
    actor: Optional[str] = typer.Option(
        None, "--actor",
        help="Filter by actor principal_ref (e.g. 'user:shane@example.com').",
    ),
    resource_id: Optional[str] = typer.Option(
        None, "--resource-id", "-r",
        help="Filter to a specific resource UUID.",
    ),
    approval_request: Optional[str] = typer.Option(
        None, "--approval-request",
        help="Filter to rows linked to a specific approval_request UUID.",
    ),
    limit: int = typer.Option(
        50, "--limit", "-n",
        help="Max rows to return (server-capped at 1000).",
    ),
) -> None:
    """List audit log entries matching the filters.

    Default window is 1 hour. Use --since for longer windows. Columns
    in table view: time, actor, verb, kind, resource/context, linked
    approval request.
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in \u2014 run `weave login` first.[/yellow]")
        raise typer.Exit(1)

    try:
        since_dt = _parse_since(since)
    except ValueError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    params: dict[str, Any] = {
        "since": since_dt.isoformat(),
        "limit": min(max(limit, 1), 1000),
    }
    if kind:
        params["resource_kind"] = kind
    if action:
        params["action_verb"] = action
    if actor:
        params["actor"] = actor
    if resource_id:
        params["resource_id"] = resource_id
    if approval_request:
        params["approval_request_id"] = approval_request

    with PowerloomClient(cfg) as client:
        try:
            resp = client.get("/audit", **params)
        except PowerloomApiError as e:
            _console.print(f"[red]Audit query failed:[/red] {e}")
            raise typer.Exit(1) from None

    rows = resp if isinstance(resp, list) else resp.get("items", [])
    if not rows:
        _console.print("[dim]No audit entries match.[/dim]")
        return

    if is_json_output():
        _console.print(json.dumps(rows, indent=2, default=str))
        return

    table = Table(
        title=f"Audit \u2014 {len(rows)} row(s) since {since_dt.strftime('%Y-%m-%d %H:%M')}",
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
    )
    table.add_column("Time", no_wrap=True)
    table.add_column("Actor", overflow="fold")
    table.add_column("Verb")
    table.add_column("Kind")
    table.add_column("Resource", overflow="fold")
    table.add_column("Context", overflow="fold")
    table.add_column("Approval", overflow="fold")

    for r in rows:
        if not isinstance(r, dict):
            continue
        # Prefer 'after' payload summary (what changed TO); fall back to before.
        after_sum = _summarize_payload(r.get("after_json"))
        before_sum = _summarize_payload(r.get("before_json"))
        context = after_sum if after_sum != "-" else before_sum
        approval = str(r.get("approval_request_id") or "")[:8] or "-"
        table.add_row(
            _format_timestamp(str(r.get("created_at", ""))),
            str(r.get("actor_principal_ref") or r.get("actor_principal_id") or "-"),
            str(r.get("action_verb") or "-"),
            str(r.get("resource_kind") or "-"),
            str(r.get("resource_id") or "-")[:12],
            context,
            approval,
        )
    _console.print(table)


# Expose `weave audit` as a top-level command too.
def audit_command(*args, **kwargs):
    """Wrapper so cli.py can register this as a top-level command."""
    return audit(*args, **kwargs)
