"""`weave workflow apply / run / status / ls / cancel` (Phase 14 Runtime, v031).

Also extends `weave agent-session` with `tasks` + `task-complete` so an
agent can discover its assigned work and report back.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import is_json_output, load_runtime_config


_console = Console()

app = typer.Typer(
    help="Workflow definitions + runs (Phase 14 Runtime).",
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


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@app.command("apply")
def apply_cmd(
    file: Annotated[Path, typer.Option("-f", "--file", help="Workflow YAML manifest")],
) -> None:
    """Apply (upsert) a workflow definition from a YAML manifest."""
    if not file.exists():
        _console.print(f"[red]No such file:[/red] {file}")
        raise typer.Exit(1)
    try:
        manifest = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        _console.print(f"[red]YAML parse error:[/red] {e}")
        raise typer.Exit(1) from e

    if not isinstance(manifest, dict) or manifest.get("kind") != "Workflow":
        _console.print(
            "[red]Manifest must have kind: Workflow[/red]"
        )
        raise typer.Exit(1)
    metadata = manifest.get("metadata") or {}
    spec = manifest.get("spec") or {}
    body = {
        "name": metadata.get("name"),
        "definition": spec,
    }
    client = _client()
    try:
        resp = client.post("/workflows", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    d = resp["definition"]
    tag = "created" if resp.get("created_new") else "no-change"
    _console.print(
        f"[green]{tag}[/green] workflow [bold]{d['name']}[/bold] version {d['version']} "
        f"({d['id']})"
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command("run")
def run_cmd(
    workflow: Annotated[str, typer.Argument(help="Workflow name or definition UUID")],
    inputs_file: Annotated[Optional[Path], typer.Option("--inputs", help="YAML/JSON inputs file")] = None,
) -> None:
    """Start a new workflow run. Returns the run id; the scheduler
    advances it in the background."""
    body: dict = {}
    # Simple heuristic: if argument looks like a UUID, use it as definition_id;
    # else treat as workflow_name.
    import re
    if re.fullmatch(r"[0-9a-fA-F-]{36}", workflow):
        body["definition_id"] = workflow
    else:
        body["workflow_name"] = workflow

    if inputs_file:
        if not inputs_file.exists():
            _console.print(f"[red]No such file:[/red] {inputs_file}")
            raise typer.Exit(1)
        text = inputs_file.read_text(encoding="utf-8")
        if inputs_file.suffix in (".json",):
            body["inputs"] = json.loads(text)
        else:
            body["inputs"] = yaml.safe_load(text)

    client = _client()
    try:
        resp = client.post("/workflow-runs", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    _console.print(
        f"[green]Queued[/green] run [bold]{resp['id']}[/bold] "
        f"(triggered_by={resp['triggered_by']}, status={resp['status']})"
    )
    _console.print(
        f"[dim]Poll with `weave workflow status {resp['id']}`[/dim]"
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status_cmd(
    run_id: Annotated[str, typer.Argument(help="Run ID")],
) -> None:
    """Show a workflow run's current state with per-step status."""
    client = _client()
    try:
        resp = client.get(f"/workflow-runs/{run_id}")
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    _console.print(
        f"[bold]run[/bold] {resp['id']} — status: [bold]{resp['status']}[/bold]"
    )
    _console.print(f"  triggered_by: {resp['triggered_by']}")
    if resp.get("completed_at"):
        _console.print(f"  completed_at: {resp['completed_at']}")

    steps = resp.get("steps", [])
    if steps:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Node")
        table.add_column("Kind")
        table.add_column("Status")
        table.add_column("Started")
        table.add_column("Completed")
        for s in steps:
            table.add_row(
                s.get("node_id", ""),
                s.get("node_kind", ""),
                s.get("status", ""),
                (s.get("started_at") or "")[:19],
                (s.get("completed_at") or "")[:19],
            )
        _console.print(table)


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


@app.command("ls")
def ls_cmd(
    status_filter: Annotated[Optional[str], typer.Option("--status", help="queued | running | waiting | done | failed | cancelled")] = None,
    workflow: Annotated[Optional[str], typer.Option("--workflow", help="Filter by workflow name")] = None,
    runs: Annotated[bool, typer.Option("--runs", help="List recent runs instead of definitions")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """List workflow definitions (default) or recent runs (`--runs`)."""
    client = _client()
    if runs:
        kw: dict = {"limit": limit}
        if status_filter:
            kw["status"] = status_filter
        if workflow:
            kw["workflow_name"] = workflow
        try:
            resp = client.get("/workflow-runs", **kw)
        except PowerloomApiError as e:
            _console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from e
        if is_json_output():
            typer.echo(json.dumps(resp, indent=2, default=str))
            return
        rows = resp.get("runs", [])
        if not rows:
            _console.print("[dim]No runs.[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("Run ID")
        table.add_column("Status")
        table.add_column("Triggered by")
        table.add_column("Started")
        for r in rows:
            table.add_row(
                r.get("id", "")[:8],
                r.get("status", ""),
                r.get("triggered_by", ""),
                (r.get("started_at") or "")[:19],
            )
        _console.print(table)
    else:
        try:
            resp = client.get("/workflows")
        except PowerloomApiError as e:
            _console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from e
        if is_json_output():
            typer.echo(json.dumps(resp, indent=2, default=str))
            return
        rows = resp.get("workflows", [])
        if not rows:
            _console.print("[dim]No workflow definitions.[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Nodes")
        table.add_column("Created")
        for r in rows:
            n_nodes = len((r.get("definition_json") or {}).get("nodes", []))
            table.add_row(
                r.get("name", ""),
                str(r.get("version", "")),
                str(n_nodes),
                (r.get("created_at") or "")[:19],
            )
        _console.print(table)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@app.command("cancel")
def cancel_cmd(
    run_id: Annotated[str, typer.Argument(help="Run ID")],
    reason: Annotated[Optional[str], typer.Option("--reason")] = None,
) -> None:
    """Cancel a running workflow. Cascades to child runs + pending timers."""
    client = _client()
    try:
        resp = client.post(f"/workflow-runs/{run_id}/cancel", {"reason": reason})
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    _console.print(f"[yellow]Cancelled[/yellow] run {resp['id']}")


# ---------------------------------------------------------------------------
# approval — approve / reject a waiting approval-node step (Phase 14 v031.1)
# ---------------------------------------------------------------------------


@app.command("approve")
def approve_cmd(
    run_id: Annotated[str, typer.Argument(help="Workflow run UUID")],
    step_id: Annotated[str, typer.Argument(help="Approval step UUID")],
    comment: Annotated[Optional[str], typer.Option("--comment")] = None,
) -> None:
    """Approve a waiting `kind: approval` step."""
    client = _client()
    try:
        resp = client.post(
            f"/workflow-runs/{run_id}/steps/{step_id}/approve",
            {"comment": comment},
        )
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    _console.print(
        f"[green]Approved[/green] step {resp.get('node_id')} ({resp.get('status')})."
    )


@app.command("reject")
def reject_cmd(
    run_id: Annotated[str, typer.Argument(help="Workflow run UUID")],
    step_id: Annotated[str, typer.Argument(help="Approval step UUID")],
    comment: Annotated[Optional[str], typer.Option("--comment")] = None,
) -> None:
    """Reject a waiting `kind: approval` step. Transitions the step to
    failed; the run fails on next scheduler tick."""
    client = _client()
    try:
        resp = client.post(
            f"/workflow-runs/{run_id}/steps/{step_id}/reject",
            {"comment": comment},
        )
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    _console.print(
        f"[yellow]Rejected[/yellow] step {resp.get('node_id')} ({resp.get('status')})."
    )


@app.command("approvals")
def approvals_cmd(
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """List workflow-step approvals currently pending. Unified Phase 12
    inbox integration is a v031.2+ follow-up."""
    client = _client()
    try:
        resp = client.get("/workflow-approvals/pending", limit=limit)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if is_json_output():
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    items = resp.get("approvals", [])
    if not items:
        _console.print("[dim]No pending approvals.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Run")
    table.add_column("Workflow")
    table.add_column("Node")
    table.add_column("Step ID")
    table.add_column("Waiting since")
    for a in items:
        table.add_row(
            str(a.get("run_id", ""))[:8],
            a.get("workflow_name", "") or "",
            a.get("node_id", ""),
            str(a.get("id", "")),
            (a.get("started_at") or "")[:19],
        )
    _console.print(table)
