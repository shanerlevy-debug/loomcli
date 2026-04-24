"""`weave approval` — inspect + cancel approval requests.

Shipped in v0.5.5 alongside `weave audit`. Wraps `/approvals` endpoints
with human-readable output. Most-common pain point this solves: after
a broken-retry scenario, you have dozens of pending requests. This
gives you `weave approval bulk-cancel` as a one-liner fix instead of
clicking each one individually in the Web UI or curl-ing per-id.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config

app = typer.Typer(help="Inspect + decide on approval requests.")
_console = Console()


def _format_ts(iso_str: str) -> str:
    if not iso_str:
        return "-"
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return str(iso_str)


@app.command("list")
def list_approvals(
    tab: str = typer.Option(
        "to_approve", "--tab",
        help="Which tab: 'to_approve' (for you to decide), 'mine' (you requested), 'recent' (everything visible).",
    ),
    limit: int = typer.Option(50, "--limit", "-n"),
    output_format: str = typer.Option(
        "table", "--format", "-f",
        help="Output: 'table' or 'json'.",
    ),
) -> None:
    """List approval requests."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in \u2014 run `weave login` first.[/yellow]")
        raise typer.Exit(1)

    with PowerloomClient(cfg) as client:
        try:
            resp = client.get("/approvals", tab=tab, limit=limit)
        except PowerloomApiError as e:
            _console.print(f"[red]Failed:[/red] {e}")
            raise typer.Exit(1) from None

    items = resp if isinstance(resp, list) else resp.get("items", [])
    if not items:
        _console.print(f"[dim]No approval requests in '{tab}' tab.[/dim]")
        return

    if output_format == "json":
        _console.print(json.dumps(items, indent=2, default=str))
        return

    table = Table(
        title=f"Approvals \u2014 tab: {tab} \u2014 {len(items)} row(s)",
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
    )
    table.add_column("ID", overflow="fold")
    table.add_column("Status")
    table.add_column("Kind")
    table.add_column("Action")
    table.add_column("Requester", overflow="fold")
    table.add_column("Policy", overflow="fold")
    table.add_column("Created")
    table.add_column("Payload preview", overflow="fold")

    for r in items:
        if not isinstance(r, dict):
            continue
        payload = r.get("pending_payload") or {}
        preview = payload.get("name") or payload.get("display_name") or "-"
        status_color = {
            "pending": "yellow",
            "approved": "green",
            "rejected": "red",
            "cancelled": "dim",
            "failed": "red",
            "expired": "dim",
        }.get(r.get("status", ""), "white")
        table.add_row(
            str(r.get("id", ""))[:8],
            f"[{status_color}]{r.get('status', '-')}[/{status_color}]",
            str(r.get("resource_kind", "-")),
            str(r.get("action", "-")),
            str(r.get("requester_email", "-")),
            str(r.get("policy_name", "-")),
            _format_ts(str(r.get("created_at", ""))),
            str(preview),
        )
    _console.print(table)


@app.command("get")
def get_approval(
    request_id: str = typer.Argument(..., help="Approval request UUID."),
) -> None:
    """Show full detail on one approval request."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    with PowerloomClient(cfg) as client:
        try:
            r = client.get(f"/approvals/{request_id}")
        except PowerloomApiError as e:
            _console.print(f"[red]Failed:[/red] {e}")
            raise typer.Exit(1) from None

    _console.print(json.dumps(r, indent=2, default=str))


@app.command("approve")
def approve(
    request_id: str = typer.Argument(...),
    comment: str = typer.Option("", "--comment", "-c"),
) -> None:
    """Approve a pending request."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    with PowerloomClient(cfg) as client:
        try:
            r = client.post(f"/approvals/{request_id}/approve", {"comment": comment})
        except PowerloomApiError as e:
            _console.print(f"[red]Approve failed:[/red] {e}")
            raise typer.Exit(1) from None
    _console.print(f"[green]Approved {request_id}[/green] status: {r.get('status', '?')}")


@app.command("reject")
def reject(
    request_id: str = typer.Argument(...),
    comment: str = typer.Option("", "--comment", "-c"),
) -> None:
    """Reject a pending request."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    with PowerloomClient(cfg) as client:
        try:
            r = client.post(f"/approvals/{request_id}/reject", {"comment": comment})
        except PowerloomApiError as e:
            _console.print(f"[red]Reject failed:[/red] {e}")
            raise typer.Exit(1) from None
    _console.print(f"[red]Rejected {request_id}[/red] status: {r.get('status', '?')}")


@app.command("cancel")
def cancel(
    request_id: str = typer.Argument(...),
    comment: str = typer.Option("", "--comment", "-c"),
) -> None:
    """Cancel your own pending request (requester only)."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    with PowerloomClient(cfg) as client:
        try:
            r = client.post(f"/approvals/{request_id}/cancel", {"comment": comment})
        except PowerloomApiError as e:
            _console.print(f"[red]Cancel failed:[/red] {e}")
            raise typer.Exit(1) from None
    _console.print(f"[dim]Cancelled {request_id}[/dim] status: {r.get('status', '?')}")


@app.command("bulk-cancel")
def bulk_cancel(
    resource_kind: Optional[str] = typer.Option(
        None, "--kind", "-k",
        help="Filter by resource_kind (e.g. 'skill', 'agent').",
    ),
    scope_ou_id: Optional[str] = typer.Option(
        None, "--scope-ou-id",
        help="Filter by scope OU UUID.",
    ),
    comment: str = typer.Option(
        "Bulk cancel via weave approval bulk-cancel",
        "--comment", "-c",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Cancel ALL of YOUR pending approval requests matching the filters.

    Shane's primary use case: you ran a bootstrap script that queued
    dozens of duplicate requests (v0.5.5 gate dedup prevents new
    duplicates but legacy pending rows from earlier runs still exist).
    This blows them away in one call.

    Security: server-side enforces requester-only per row. You cannot
    bulk-cancel someone else's requests.
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    # Preview what we'll cancel.
    with PowerloomClient(cfg) as client:
        try:
            mine = client.get("/approvals", tab="mine", limit=500)
        except PowerloomApiError as e:
            _console.print(f"[red]Preview failed:[/red] {e}")
            raise typer.Exit(1) from None

    mine_items = mine if isinstance(mine, list) else mine.get("items", [])
    pending = [
        r for r in mine_items
        if isinstance(r, dict) and r.get("status") == "pending"
    ]
    if resource_kind:
        pending = [r for r in pending if r.get("resource_kind") == resource_kind]
    if scope_ou_id:
        pending = [r for r in pending if r.get("scope_ou_id") == scope_ou_id]

    if not pending:
        _console.print("[dim]No matching pending requests to cancel.[/dim]")
        return

    _console.print(
        f"[yellow]Will cancel {len(pending)} pending request(s):[/yellow]"
    )
    for r in pending[:10]:
        payload = r.get("pending_payload") or {}
        _console.print(
            f"  \u2022 {r.get('resource_kind')} create \u2014 "
            f"{payload.get('name', '?')} "
            f"(id {str(r.get('id', ''))[:8]})"
        )
    if len(pending) > 10:
        _console.print(f"  ... and {len(pending) - 10} more")

    if not yes:
        if not typer.confirm("Proceed?"):
            _console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    body: dict[str, Any] = {"comment": comment}
    if resource_kind:
        body["resource_kind"] = resource_kind
    if scope_ou_id:
        body["scope_ou_id"] = scope_ou_id

    with PowerloomClient(cfg) as client:
        try:
            r = client.post("/approvals/bulk-cancel", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Bulk-cancel failed:[/red] {e}")
            raise typer.Exit(1) from None

    _console.print(
        f"[green]Cancelled {r.get('cancelled_count', 0)} request(s).[/green]"
    )
    if r.get("skipped_ids"):
        _console.print(
            f"[yellow]Skipped {len(r['skipped_ids'])} "
            f"(probably raced with another state change).[/yellow]"
        )
