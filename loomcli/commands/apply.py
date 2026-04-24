"""`weave apply` — execute plan actions against the control plane.

Flow:
  1. Parse manifest(s)
  2. Expand agent attachments + sort for apply
  3. Plan against current state
  4. Render plan
  5. Prompt for confirmation (or --auto-approve)
  6. Apply each action; print per-resource outcome
  7. Exit 0 if all ok, 1 if any failed

Per-resource best-effort: one failure doesn't stop others (Q3).
"""
from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomClient
from loomcli.commands.plan import render_plan
from loomcli.config import load_runtime_config
from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.applier import (
    apply_plan,
    expand_agent_attachments,
    sort_for_apply,
)
from loomcli.manifest.parser import (
    ManifestParseError,
    parse_manifest_paths,
)
from loomcli.manifest.planner import plan_resources


_console = Console()


def apply_command(
    paths: Annotated[
        list[str],
        typer.Argument(help="Manifest files or directories. `-` reads stdin."),
    ],
    auto_approve: Annotated[
        bool,
        typer.Option("--auto-approve", "-y", help="Skip interactive confirmation."),
    ] = False,
) -> None:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print(
            "[yellow]Not signed in. Run `weave auth login --dev-as <email>` first.[/yellow]"
        )
        raise typer.Exit(1)

    try:
        resources = parse_manifest_paths(paths)
    except ManifestParseError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)

    expanded = expand_agent_attachments(resources)
    sorted_res = sort_for_apply(expanded)

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        plan = plan_resources(sorted_res, resolver)
        render_plan(plan)

        actionable = [
            a for a in plan.actions
            if a.verb in ("create", "update", "destroy", "unknown")
        ]
        if not actionable:
            return

        if not auto_approve:
            _console.print()
            confirm = typer.confirm(
                "Apply these changes?", default=False
            )
            if not confirm:
                _console.print("[yellow]Aborted.[/yellow]")
                raise typer.Exit(130)

        outcomes = apply_plan(plan, resolver, client)

    _render_outcomes(outcomes)
    if any(o.status == "failed" for o in outcomes):
        raise typer.Exit(1)


def _render_outcomes(outcomes) -> None:
    """Render outcome table.

    Table columns stay narrow for scanning; full error bodies are printed
    below the table for any failed row. The table had been truncating
    errors at 80 chars, which hid the most important information when
    something went wrong (v0.5.3 fix).
    """
    table = Table(title="Apply results", show_header=True, header_style="bold")
    table.add_column("Kind")
    table.add_column("Address")
    table.add_column("Action")
    table.add_column("Status")
    table.add_column("Error (summary)")
    for o in outcomes:
        color = {
            "ok": "green",
            "failed": "red",
            "skipped": "yellow",
        }.get(o.status, "white")
        table.add_row(
            o.action.resource.kind,
            o.action.resource.address,
            o.action.verb,
            f"[{color}]{o.status}[/{color}]",
            # First 80 chars — enough to scan "what kind of error" per row
            # without the Rich column auto-shrink clipping it to nothing.
            (o.error or "")[:80],
        )
    _console.print()
    _console.print(table)

    # Full error bodies for failed rows — printed below the table so they
    # aren't subject to column-width clipping.
    failed = [o for o in outcomes if o.status == "failed" and o.error]
    if failed:
        _console.print()
        _console.print("[bold red]Full error details:[/bold red]")
        for o in failed:
            _console.print()
            _console.print(f"[red]✗[/red] {o.action.resource.kind} {o.action.resource.address}")
            _console.print(f"  [dim]({o.action.verb})[/dim]")
            # Indent each line of the error for readability
            for line in str(o.error).splitlines() or [str(o.error)]:
                _console.print(f"  {line}")
