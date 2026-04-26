"""`weave auth login / logout / whoami` + PAT management.

Also exposes top-level aliases `weave login` / `weave logout` /
`weave whoami` via loomcli.cli (the aliases call the same functions).
"""
from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli import auth as auth_api
from loomcli.client import PowerloomApiError
from loomcli.config import load_runtime_config

app = typer.Typer(help="Authenticate against the control plane.")
_console = Console()


# ---------------------------------------------------------------------------
# Shared implementations — called by both `weave auth login` and the
# top-level `weave login` alias in cli.py.
# ---------------------------------------------------------------------------


def run_login(
    dev_as: Optional[str] = None,
    pat: Optional[str] = None,
    oidc: bool = False,
    no_browser: bool = False,
) -> None:
    """Unified login entry point. Default behavior (no flags) is the
    browser-paste flow against the configured control plane."""
    cfg = load_runtime_config()

    # OIDC stub — land in v056.
    if oidc:
        try:
            auth_api.login_oidc(cfg)
        except NotImplementedError as e:
            _console.print(f"[yellow]{e}[/yellow]")
            raise typer.Exit(1) from None
        return

    # Explicit PAT injection (scripts / CI).
    if pat:
        try:
            me = auth_api.login_pat(cfg, pat)
        except PowerloomApiError as e:
            _console.print(f"[red]Token verification failed:[/red] {e}")
            raise typer.Exit(1) from None
        _console.print(
            f"[green]Signed in as {me.get('email')} "
            f"({me.get('id')}).[/green]"
        )
        return

    # Dev-mode impersonation — requires POWERLOOM_AUTH_MODE=dev on the API.
    if dev_as:
        try:
            auth_api.login_dev(cfg, dev_as)
        except PowerloomApiError as e:
            _console.print(f"[red]Dev login failed:[/red] {e}")
            _console.print(
                "[dim]Is the control plane running with "
                "POWERLOOM_AUTH_MODE=dev? --dev-as only works locally.[/dim]"
            )
            raise typer.Exit(1) from None
        _console.print(f"[green]Signed in as {dev_as}.[/green]")
        return

    # Default: browser-paste flow.
    if cfg.api_base_url.startswith(("http://localhost", "http://127.")):
        _console.print(
            "[yellow]Your API URL is localhost.[/yellow] "
            "Did you mean [bold]weave login --dev-as <email>[/bold]?"
        )
        _console.print(
            "[dim]For production, set POWERLOOM_API_BASE_URL=https://api.powerloom.org "
            "or pass --api-url.[/dim]"
        )
        _console.print(
            "[dim]Continuing with the browser flow — this will fail "
            "unless your localhost API accepts PATs.[/dim]\n"
        )

    try:
        me = auth_api.login_browser(cfg, open_browser=not no_browser)
    except PowerloomApiError as e:
        _console.print(f"[red]Token verification failed:[/red] {e}")
        raise typer.Exit(1) from None
    _console.print(
        f"[green]Signed in as {me.get('email')} ({me.get('id')}).[/green]"
    )


def run_logout() -> None:
    auth_api.logout()
    _console.print("[green]Signed out.[/green]")


def run_whoami() -> None:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)
    try:
        me = auth_api.whoami(cfg)
    except PowerloomApiError as e:
        _console.print(f"[red]whoami failed:[/red] {e}")
        raise typer.Exit(1) from None
    _console.print(
        f"{me.get('email')} ({me.get('id')}) @ org {me.get('organization_id')}"
    )


# ---------------------------------------------------------------------------
# `weave auth login / logout / whoami` commands (original location)
# ---------------------------------------------------------------------------


@app.command("login")
def login(
    dev_as: Optional[str] = typer.Option(
        None,
        "--dev-as",
        help="Dev-mode impersonation (requires POWERLOOM_AUTH_MODE=dev).",
    ),
    pat: Optional[str] = typer.Option(
        None,
        "--pat",
        help="Inject a Personal Access Token directly (for scripts / CI).",
    ),
    oidc: bool = typer.Option(
        False,
        "--oidc",
        help="(v056) Fully-automated device-code OIDC flow. Not yet implemented.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Skip the browser launch (headless / remote systems). "
        "The PAT-mint URL is still printed; you open it yourself.",
    ),
) -> None:
    """Sign in to Powerloom.

    Default: opens your browser to https://powerloom.org/settings/access-tokens,
    prompts for the token you just minted. Suppress with --no-browser.
    """
    run_login(dev_as=dev_as, pat=pat, oidc=oidc, no_browser=no_browser)


@app.command("logout")
def logout() -> None:
    """Clear local credentials."""
    run_logout()


@app.command("whoami")
def whoami() -> None:
    """Print the signed-in user + their organization."""
    run_whoami()


@app.command("mcp-url")
def mcp_url() -> None:
    """Print the hosted MCP server URL for this account.

    Output is a plain URL suitable for use in .mcp.json or scripts:
      https://<mcp_proxy_id>.mcp.powerloom.org/mcp
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave auth login` first.[/yellow]")
        raise typer.Exit(1)
    try:
        me = auth_api.whoami(cfg)
    except PowerloomApiError as e:
        _console.print(f"[red]Failed to fetch account info:[/red] {e}")
        raise typer.Exit(1) from None
    proxy_id = me.get("mcp_proxy_id")
    if not proxy_id:
        _console.print("[red]No MCP proxy ID on this account. Contact support.[/red]")
        raise typer.Exit(1)
    typer.echo(f"https://{proxy_id}.mcp.powerloom.org/mcp")


@app.command("token")
def token_cmd() -> None:
    """Print the stored Personal Access Token.

    Intended for scripts and setup automation. Output is the raw token,
    suitable for piping into other commands or writing to config files.
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave auth login` first.[/yellow]")
        raise typer.Exit(1)
    typer.echo(cfg.access_token)


# ---------------------------------------------------------------------------
# `weave auth pat ...` subgroup — PAT management (0.5.1)
# ---------------------------------------------------------------------------

pat_app = typer.Typer(help="Manage Personal Access Tokens.")
app.add_typer(pat_app, name="pat")


@pat_app.command("create")
def pat_create_cmd(
    name: str = typer.Option(
        ...,
        "--name",
        help="Human-readable label for this token (e.g. 'my-laptop', 'ci-prod').",
    ),
    expires_at: Optional[str] = typer.Option(
        None,
        "--expires-at",
        help="Optional ISO 8601 expiration timestamp (e.g. 2027-01-01T00:00:00Z).",
    ),
) -> None:
    """Mint a new PAT. The raw token is shown ONCE — copy it immediately."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    try:
        minted = auth_api.pat_create(cfg, name=name, expires_at=expires_at)
    except PowerloomApiError as e:
        _console.print(f"[red]Failed to mint PAT:[/red] {e}")
        raise typer.Exit(1) from None

    _console.print()
    _console.print(f"[green]PAT '{minted.get('name')}' created.[/green]")
    _console.print(f"  [dim]id:[/dim] {minted.get('id')}")
    _console.print(f"  [dim]prefix:[/dim] {minted.get('token_prefix')}")
    if minted.get("expires_at"):
        _console.print(f"  [dim]expires:[/dim] {minted.get('expires_at')}")
    _console.print()
    _console.print(f"[bold yellow]Token (shown once — copy now):[/bold yellow]")
    _console.print(f"  {minted.get('raw_token')}")
    _console.print()
    _console.print(
        "[dim]Use this value with `weave login --pat <token>` on another "
        "machine, or paste into CI/CD secrets.[/dim]"
    )


@pat_app.command("list")
def pat_list_cmd() -> None:
    """List all PATs on this account (metadata only — tokens are not recoverable)."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    try:
        items = auth_api.pat_list(cfg)
    except PowerloomApiError as e:
        _console.print(f"[red]Failed to list PATs:[/red] {e}")
        raise typer.Exit(1) from None

    if not items:
        _console.print("[dim]No PATs.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", overflow="fold")
    table.add_column("Name")
    table.add_column("Prefix")
    table.add_column("Created")
    table.add_column("Last used")
    table.add_column("Expires")
    table.add_column("Revoked")
    for it in items:
        table.add_row(
            str(it.get("id", "")),
            it.get("name", ""),
            it.get("token_prefix", ""),
            str(it.get("created_at", "")),
            str(it.get("last_used_at") or "-"),
            str(it.get("expires_at") or "-"),
            str(it.get("revoked_at") or "-"),
        )
    _console.print(table)


@pat_app.command("revoke")
def pat_revoke_cmd(
    pat_id: str = typer.Argument(
        ...,
        help="UUID of the PAT to revoke. Get it from `weave auth pat list`.",
    ),
) -> None:
    """Revoke a PAT by its UUID. Revoked tokens stop working immediately."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    try:
        auth_api.pat_revoke(cfg, pat_id)
    except PowerloomApiError as e:
        _console.print(f"[red]Failed to revoke PAT:[/red] {e}")
        raise typer.Exit(1) from None
    _console.print(f"[green]Revoked PAT {pat_id}.[/green]")
