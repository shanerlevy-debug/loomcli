"""`weave profile` - local CLI defaults."""
from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from loomcli.config import (
    PROFILE_FIELDS,
    clear_profile_values,
    config_file,
    is_json_output,
    load_cli_config,
    update_profile,
)

app = typer.Typer(help="Manage local CLI profiles and defaults.", no_args_is_help=True)
_console = Console()


@app.command("show")
def show_profile(
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Profile name. Defaults to active profile."),
    ] = None,
) -> None:
    """Show local defaults for one profile."""
    cfg = load_cli_config()
    name = profile or cfg.active_profile
    row = cfg.profiles.get(name)
    if row is None:
        _console.print(f"[red]Profile not found:[/red] {name}")
        raise typer.Exit(1)

    payload = _profile_payload(name, cfg.active_profile, row)
    if is_json_output():
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    table = Table(title=f"Profile: {name}", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("active", "yes" if name == cfg.active_profile else "no")
    for key in PROFILE_FIELDS:
        table.add_row(key, str(payload.get(key) or ""))
    table.add_row("config_file", str(config_file()))
    _console.print(table)


@app.command("set")
def set_profile(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Profile name to update and activate."),
    ] = "default",
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Default Powerloom API base URL."),
    ] = None,
    default_org: Annotated[
        str | None,
        typer.Option("--default-org", help="Default org path, e.g. /acme."),
    ] = None,
    default_ou: Annotated[
        str | None,
        typer.Option("--default-ou", help="Default OU path, e.g. /acme/eng."),
    ] = None,
    default_agent: Annotated[
        str | None,
        typer.Option("--default-agent", help="Default agent address."),
    ] = None,
    default_project: Annotated[
        str | None,
        typer.Option("--default-project", help="Default project slug (e.g. 'powerloom-ui')."),
    ] = None,
    default_runtime: Annotated[
        str | None,
        typer.Option("--default-runtime", help="Default runtime for new agents."),
    ] = None,
    default_model: Annotated[
        str | None,
        typer.Option("--default-model", help="Default model for new agents."),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option("--output", help="Default output format, e.g. table or json."),
    ] = None,
) -> None:
    """Set local defaults for a profile and make it active."""
    values = {
        "api_base_url": api_url,
        "default_org": default_org,
        "default_ou": default_ou,
        "default_agent": default_agent,
        "default_project": default_project,
        "default_runtime": default_runtime,
        "default_model": default_model,
        "output": output,
    }
    selected = {k: v for k, v in values.items() if v is not None}
    if not selected:
        _console.print("[yellow]No profile values supplied.[/yellow]")
        raise typer.Exit(1)
    cfg = update_profile(profile, selected, activate=True)
    _console.print(f"[green]Updated profile[/green] {cfg.active_profile}")


@app.command("switch")
def switch_profile(
    profile: Annotated[
        str,
        typer.Argument(help="Profile name to activate."),
    ],
) -> None:
    """Switch the active profile."""
    # update_profile with empty values but activate=True performs a switch.
    cfg = update_profile(profile, {}, activate=True)
    _console.print(f"[green]Switched to profile[/green] {cfg.active_profile}")


@app.command("clear")
def clear_profile(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Profile name to update and activate."),
    ] = "default",
    api_url: Annotated[bool, typer.Option("--api-url")] = False,
    default_org: Annotated[bool, typer.Option("--default-org")] = False,
    default_ou: Annotated[bool, typer.Option("--default-ou")] = False,
    default_agent: Annotated[bool, typer.Option("--default-agent")] = False,
    default_project: Annotated[bool, typer.Option("--default-project")] = False,
    default_runtime: Annotated[bool, typer.Option("--default-runtime")] = False,
    default_model: Annotated[bool, typer.Option("--default-model")] = False,
    output: Annotated[bool, typer.Option("--output")] = False,
    all_values: Annotated[
        bool,
        typer.Option("--all", help="Clear every local default in the profile."),
    ] = False,
) -> None:
    """Clear one or more profile defaults."""
    if all_values:
        fields = list(PROFILE_FIELDS)
    else:
        fields = []
        if api_url:
            fields.append("api_base_url")
        if default_org:
            fields.append("default_org")
        if default_ou:
            fields.append("default_ou")
        if default_agent:
            fields.append("default_agent")
        if default_project:
            fields.append("default_project")
        if default_runtime:
            fields.append("default_runtime")
        if default_model:
            fields.append("default_model")
        if output:
            fields.append("output")
    if not fields:
        _console.print("[yellow]No profile fields selected.[/yellow]")
        raise typer.Exit(1)
    cfg = clear_profile_values(profile, fields, activate=True)
    _console.print(f"[green]Cleared {len(fields)} value(s)[/green] in {cfg.active_profile}")


def _profile_payload(name: str, active_profile: str, row) -> dict[str, str | bool | None]:
    payload: dict[str, str | bool | None] = {
        "name": name,
        "active": name == active_profile,
    }
    for key in PROFILE_FIELDS:
        payload[key] = getattr(row, key)
    return payload
