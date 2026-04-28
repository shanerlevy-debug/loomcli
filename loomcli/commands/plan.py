"""`weave plan` — render what apply would do, no server writes.

Output format mirrors Terraform plan: one section per action,
color-coded, with per-field diffs for updates. Summary footer has
counts.
"""
from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from loomcli.client import PowerloomClient
from loomcli.config import is_json_output, load_runtime_config
from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.applier import (
    expand_agent_attachments,
    sort_for_apply,
)
from loomcli.manifest.parser import (
    ManifestParseError,
    parse_manifest_paths,
)
from loomcli.manifest.planner import Plan, PlanAction, plan_resources


_console = Console()


def plan_command(
    paths: Annotated[
        list[str],
        typer.Argument(help="Manifest files or directories. `-` reads stdin."),
    ],
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

    if is_json_output():
        _render_json_plan(plan)
        return

    render_plan(plan)


def render_plan(plan: Plan) -> None:
    if not plan.actions:
        _console.print("No resources in manifest.")
        return
    for action in plan.actions:
        _render_action(action)
    counts = plan.summary_counts()
    summary_bits = []
    for verb in ("create", "update", "destroy", "noop", "unknown"):
        if counts.get(verb):
            summary_bits.append(f"{counts[verb]} {verb}")
    _console.print()
    _console.print(
        f"[bold]Plan:[/bold] {', '.join(summary_bits) if summary_bits else 'nothing to do'}."
    )


def _render_action(a: PlanAction) -> None:
    symbol, color = {
        "create": ("+", "green"),
        "update": ("~", "yellow"),
        "destroy": ("-", "red"),
        "noop": (" ", "dim"),
        "unknown": ("?", "cyan"),
    }.get(a.verb, ("·", "white"))
    _console.print(
        f"  [{color}]{symbol} {a.resource.kind} {a.resource.address}[/{color}] "
        f"[dim]({a.verb})[/dim]"
    )
    if a.verb == "update":
        for diff in a.changed_fields:
            _console.print(
                f"      [dim]{diff.field}:[/dim] {_fmt(diff.before)} [yellow]→[/yellow] {_fmt(diff.after)}"
            )
    if a.reason:
        _console.print(f"      [dim italic]{a.reason}[/dim italic]")


def _fmt(value: object) -> str:
    if value is None:
        return "[dim]None[/dim]"
    s = str(value)
    if len(s) > 80:
        return f'"{s[:77]}…"'
    return repr(s) if isinstance(value, str) else str(value)


def _render_json_plan(plan: Plan) -> None:
    actions = []
    for a in plan.actions:
        actions.append({
            "verb": a.verb,
            "kind": a.resource.kind,
            "address": a.resource.address,
            "reason": a.reason,
            "changed_fields": [
                {"field": d.field, "before": d.before, "after": d.after}
                for d in a.changed_fields
            ]
        })
    typer.echo(json.dumps({
        "actions": actions,
        "summary": plan.summary_counts()
    }, indent=2, default=str))
