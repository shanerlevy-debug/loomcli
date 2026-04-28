"""`weave destroy` — delete everything in a manifest, reverse order."""
from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from loomcli.client import PowerloomClient
from loomcli.commands.apply import _render_outcomes
from loomcli.commands.plan import render_plan
from loomcli.config import is_json_output, load_runtime_config
from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.applier import (
    apply_plan,
    plan_destroy_for_resources,
)
from loomcli.manifest.parser import (
    ManifestParseError,
    parse_manifest_paths,
)


_console = Console()


def destroy_command(
    paths: Annotated[
        list[str],
        typer.Argument(help="Manifest files or directories."),
    ],
    auto_approve: Annotated[
        bool,
        typer.Option("--auto-approve", "-y", help="Skip interactive confirmation."),
    ] = False,
) -> None:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print(
            "[yellow]Not signed in.[/yellow]"
        )
        raise typer.Exit(1)

    try:
        resources = parse_manifest_paths(paths)
    except ManifestParseError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        plan = plan_destroy_for_resources(resources, resolver)
        
        if not is_json_output():
            render_plan(plan)

        actionable = [a for a in plan.actions if a.verb == "destroy"]
        if not actionable:
            if is_json_output():
                typer.echo(json.dumps({"status": "no_changes", "plan": []}))
            return

        if not auto_approve:
            _console.print()
            confirm = typer.confirm(
                f"Destroy {len(actionable)} resource(s)?", default=False
            )
            if not confirm:
                _console.print("[yellow]Aborted.[/yellow]")
                raise typer.Exit(130)

        outcomes = apply_plan(plan, resolver, client)

    _render_outcomes(outcomes)
    if any(o.status == "failed" for o in outcomes):
        raise typer.Exit(1)
