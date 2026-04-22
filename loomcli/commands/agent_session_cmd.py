"""`weave agent-session register / end / ls / get` — Phase 14 Foundation coordination commands.

Replaces hand-edited COWORK.md §6 Active-sessions workflow. A Claude
Code session calls `weave agent-session register` at task start to
check in with its scope; when its PR merges, it calls `weave
agent-session end --outcome merged`.

Named `agent-session` to disambiguate from `weave get session` which
lists CMA agent-runtime sessions.
"""
from __future__ import annotations

import json
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


_console = Console()

app = typer.Typer(
    help="Agent-session coordination (Phase 14 Foundation).",
    no_args_is_help=True,
)


def _client() -> PowerloomClient:
    cfg = load_runtime_config()
    if cfg.access_token is None:
        _console.print(
            "[yellow]Not signed in. Run `weave auth login --dev-as <email>` first.[/yellow]"
        )
        raise typer.Exit(1)
    return PowerloomClient(cfg)


@app.command("register")
def register_cmd(
    scope: Annotated[str, typer.Option("--scope", help="Session scope slug; typically `<name>-<yyyymmdd>`")],
    summary: Annotated[str, typer.Option("--summary", help="One-line scope description")],
    branch: Annotated[Optional[str], typer.Option("--branch", help="Feature branch name")] = None,
    capabilities: Annotated[Optional[str], typer.Option("--capabilities", help="Comma-separated capability tags (e.g. 'ui,docs,python')")] = None,
    cross_cutting: Annotated[bool, typer.Option("--cross-cutting/--no-cross-cutting", help="Does this session touch many files across modules?")] = False,
    migration: Annotated[bool, typer.Option("--migration/--no-migration", help="Does this session add an Alembic migration?")] = False,
    version: Annotated[Optional[str], typer.Option("--version", help="Target version, e.g. v030")] = None,
    actor_kind: Annotated[str, typer.Option("--actor-kind", help="claude_code | cma | human")] = "claude_code",
    actor_id: Annotated[Optional[str], typer.Option("--actor-id", help="Session identifier (defaults to caller email)")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of human output")] = False,
) -> None:
    """Register a new active agent session. Returns session id, work-chain
    event hash, and any overlap warnings."""
    caps = [c.strip() for c in (capabilities or "").split(",") if c.strip()]
    body = {
        "session_slug": scope,
        "scope_summary": summary,
        "branch_name": branch,
        "capabilities": caps,
        "cross_cutting": cross_cutting,
        "touches_migration": migration,
        "version_claimed": version,
        "actor_kind": actor_kind,
        "actor_id": actor_id,
    }
    client = _client()
    try:
        resp = client.post("/agent-sessions", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    sess = resp["session"]
    _console.print(f"[green]Registered[/green] session [bold]{sess['session_slug']}[/bold]")
    _console.print(f"  id: {sess['id']}")
    _console.print(f"  work-chain event hash: {resp['work_chain_event_hash']}")
    _console.print(f"  version claimed: {sess.get('version_claimed') or '(none)'}")
    _console.print(f"  capabilities: {sess.get('capabilities') or []}")
    warnings = resp.get("overlap_warnings", [])
    if warnings:
        _console.print("[yellow]Overlap warnings:[/yellow]")
        for w in warnings:
            _console.print(f"  - {w}")
    else:
        _console.print("[dim]No overlaps with other active sessions.[/dim]")


@app.command("end")
def end_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    outcome: Annotated[str, typer.Option("--outcome", help="merged | abandoned")] = "merged",
    pr_url: Annotated[Optional[str], typer.Option("--pr-url", help="Merged PR URL")] = None,
    reason: Annotated[Optional[str], typer.Option("--reason", help="Abandonment reason")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Terminate an active session with outcome merged or abandoned."""
    body = {
        "outcome": outcome,
        "pr_url": pr_url,
        "abandoned_reason": reason,
    }
    client = _client()
    try:
        resp = client.post(f"/agent-sessions/{session_id}/end", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    _console.print(
        f"[green]Session {resp['session_slug']!r} ended "
        f"({resp['status']}).[/green]"
    )


@app.command("ls")
def ls_cmd(
    status_filter: Annotated[Optional[str], typer.Option("--status", help="active | yielded | merged | abandoned")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List sessions."""
    client = _client()
    kw: dict = {"limit": limit}
    if status_filter:
        kw["status"] = status_filter
    try:
        resp = client.get("/agent-sessions", **kw)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    sessions = resp.get("sessions", [])
    if not sessions:
        _console.print("[dim]No sessions.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug")
    table.add_column("Status")
    table.add_column("Actor")
    table.add_column("Version")
    table.add_column("XX")
    table.add_column("Mig")
    table.add_column("Started")
    for s in sessions:
        table.add_row(
            s.get("session_slug", ""),
            s.get("status", ""),
            s.get("actor_kind", ""),
            s.get("version_claimed") or "-",
            "yes" if s.get("cross_cutting") else "-",
            "yes" if s.get("touches_migration") else "-",
            (s.get("started_at") or "")[:19],
        )
    _console.print(table)


@app.command("get")
def get_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fetch one session's full detail."""
    client = _client()
    try:
        resp = client.get(f"/agent-sessions/{session_id}")
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    _console.print(f"[bold]{resp['session_slug']}[/bold]")
    for k in (
        "id",
        "status",
        "actor_kind",
        "actor_id",
        "branch_name",
        "version_claimed",
        "capabilities",
        "cross_cutting",
        "touches_migration",
        "scope_summary",
        "pr_url",
        "started_at",
        "merged_at",
        "abandoned_reason",
    ):
        v = resp.get(k)
        if v is not None and v != "":
            _console.print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# Phase 14 Runtime (v031) — task claim / complete for agent-node execution
# ---------------------------------------------------------------------------


@app.command("tasks")
def tasks_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List workflow steps currently assigned to this session.

    A step shows up here when the workflow scheduler matched this
    session's capabilities to an `agent` node's `required_capabilities`
    and transitioned the step to `running`. Act on the step, then call
    `weave agent-session task-complete` to report outputs.
    """
    client = _client()
    try:
        resp = client.get(f"/agent-sessions/{session_id}/tasks")
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    tasks = resp.get("tasks", [])
    if not tasks:
        _console.print("[dim]No assigned tasks.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Step ID")
    table.add_column("Workflow")
    table.add_column("Node")
    table.add_column("Kind")
    table.add_column("Status")
    for t in tasks:
        table.add_row(
            t.get("id", ""),
            t.get("workflow_name", "") or "",
            t.get("node_id", ""),
            t.get("node_kind", ""),
            t.get("status", ""),
        )
    _console.print(table)


@app.command("task-complete")
def task_complete_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    step_id: Annotated[str, typer.Argument(help="Step ID (UUID)")],
    outcome: Annotated[str, typer.Option("--outcome", help="done | failed")] = "done",
    outputs_file: Annotated[Optional[str], typer.Option("--outputs-file", help="Path to JSON/YAML file with outputs")] = None,
    output_kv: Annotated[Optional[list[str]], typer.Option("--output", help="key=value output (repeatable)")] = None,
    error_reason: Annotated[Optional[str], typer.Option("--error-reason")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark an assigned task done or failed. Emits a work-chain event;
    the scheduler advances the workflow on its next tick."""
    import re as _re

    body: dict = {"outcome": outcome}

    outputs: dict = {}
    if outputs_file:
        from pathlib import Path as _P
        p = _P(outputs_file)
        if not p.exists():
            _console.print(f"[red]No such file:[/red] {outputs_file}")
            raise typer.Exit(1)
        text = p.read_text(encoding="utf-8")
        import yaml as _yaml
        if outputs_file.endswith(".json"):
            outputs = json.loads(text)
        else:
            outputs = _yaml.safe_load(text) or {}
    for kv in output_kv or []:
        if "=" not in kv:
            _console.print(f"[red]--output expects key=value; got:[/red] {kv}")
            raise typer.Exit(1)
        k, _, v = kv.partition("=")
        outputs[k] = v
    if outputs:
        body["outputs"] = outputs
    if outcome == "failed":
        body["error"] = {"reason": error_reason or "agent-reported failure"}

    client = _client()
    try:
        resp = client.post(
            f"/agent-sessions/{session_id}/tasks/{step_id}/complete", body
        )
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    _console.print(
        f"[green]Step {resp.get('node_id')}[/green] marked {resp.get('status')}."
    )
