"""`weave auth login / logout / whoami`."""
from __future__ import annotations

import typer
from rich.console import Console

from loomcli import auth as auth_api
from loomcli.client import PowerloomApiError
from loomcli.config import load_runtime_config

app = typer.Typer(help="Authenticate against the control plane.")
_console = Console()


@app.command("login")
def login(
    dev_as: str = typer.Option(
        None,
        "--dev-as",
        help="Dev-mode impersonation. Requires POWERLOOM_AUTH_MODE=dev on the control plane.",
    ),
    oidc: bool = typer.Option(
        False,
        "--oidc",
        help="(Phase 6) Device-code OIDC flow. Not implemented in v009.",
    ),
) -> None:
    cfg = load_runtime_config()
    if oidc:
        try:
            auth_api.login_oidc(cfg)
        except NotImplementedError as e:
            _console.print(f"[yellow]{e}[/yellow]")
            raise typer.Exit(1)
        return
    if not dev_as:
        _console.print(
            "[red]Provide --dev-as <email> or --oidc (when available).[/red]"
        )
        raise typer.Exit(1)
    try:
        auth_api.login_dev(cfg, dev_as)
    except PowerloomApiError as e:
        _console.print(f"[red]Login failed: {e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]Signed in as {dev_as}.[/green]")


@app.command("logout")
def logout() -> None:
    auth_api.logout()
    _console.print("[green]Signed out.[/green]")


@app.command("whoami")
def whoami() -> None:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)
    try:
        me = auth_api.whoami(cfg)
    except PowerloomApiError as e:
        _console.print(f"[red]whoami failed: {e}[/red]")
        raise typer.Exit(1)
    _console.print(f"{me.get('email')} ({me.get('id')}) @ org {me.get('organization_id')}")
