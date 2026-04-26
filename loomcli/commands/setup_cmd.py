"""`weave setup-claude-code` — idempotent Claude Code plugin wiring.

Writes (or updates) two files in the target project directory so that
Claude Code sessions get the Powerloom hosted-MCP server wired
automatically:

  .mcp.json
    {"powerloom": {"type": "http", "url": "<proxy-url>",
                   "headers": {"Authorization": "Bearer ${POWERLOOM_MCP_TOKEN}"}}}

  .claude/settings.local.json
    env.POWERLOOM_MCP_TOKEN  ← the caller's PAT
    enabledMcpjsonServers    ← ["powerloom"] (appended if absent)

Both writes are idempotent — existing keys are updated in-place;
unrelated keys are preserved.

After running, restart Claude Code for the MCP server to appear.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from loomcli import auth as auth_api
from loomcli.client import PowerloomApiError
from loomcli.config import load_runtime_config

app = typer.Typer(help="Wire the Powerloom Claude Code plugin (idempotent).")
_console = Console()

MCP_SERVER_NAME = "powerloom"
MCP_ZONE = "mcp.powerloom.org"
TOKEN_ENV_VAR = "POWERLOOM_MCP_TOKEN"


def _mcp_url(proxy_id: str) -> str:
    return f"https://{proxy_id}.{MCP_ZONE}/mcp"


def _write_mcp_json(
    project_dir: Path,
    url: str,
    quiet: bool,
    *,
    pat: str,
    use_env_substitution: bool = False,
) -> None:
    """Write/update .mcp.json with the powerloom server entry under the
    canonical `mcpServers` wrapper.

    Schema (Claude Code .mcp.json format):

        {"mcpServers": {"<name>": {"type": "http", "url": "...", ...}}}

    NOT the broken pre-hotfix shape (server entries at top level):

        {"<name>": {...}}

    Pre-hotfix versions of `weave setup-claude-code` wrote the broken shape;
    Claude Code rejects it with `mcpServers: Does not adhere to MCP server
    configuration schema`. This function now auto-migrates broken files on
    next write — detects top-level keys whose value is a dict containing
    server-config keys (type / url / command / args), MOVES them under
    `mcpServers`, and removes the duplicates from the top level so the file
    isn't left in a confusingly-doubled state.
    """
    mcp_file = project_dir / ".mcp.json"
    data: dict = {}
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}

    # Auto-migrate pre-hotfix broken shape: move + remove top-level server
    # entries under the canonical `mcpServers` wrapper.
    server_keys_at_top = [
        k for k, v in list(data.items())
        if k != "mcpServers"
        and isinstance(v, dict)
        and any(field in v for field in ("type", "url", "command", "args"))
    ]
    if server_keys_at_top:
        data.setdefault("mcpServers", {})
        for k in server_keys_at_top:
            data["mcpServers"][k] = data.pop(k)

    data.setdefault("mcpServers", {})
    # Authorization: write the literal Bearer token rather than the
    # `${POWERLOOM_MCP_TOKEN}` substitution. Claude Code's HTTP-transport
    # MCP config doesn't reliably env-var-substitute inside the `headers`
    # dict (verified 2026-04-27 — server reported `failed` until the literal
    # token was inlined). The literal value is per-user + per-machine and
    # `.mcp.json` is gitignored in personal workspaces, so this isn't a
    # secret-leak risk for the typical case. For shared/committed workspaces
    # (e.g. the powerloom repo itself), pass `--use-env-substitution` to opt
    # back into the `${VAR}` form (the user then sets POWERLOOM_MCP_TOKEN in
    # their shell).
    auth_value = (
        f"Bearer ${{{TOKEN_ENV_VAR}}}"
        if use_env_substitution
        else f"Bearer {pat}"
    )
    data["mcpServers"][MCP_SERVER_NAME] = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": auth_value},
    }
    mcp_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        _console.print(f"  [green]✓[/green] {mcp_file.relative_to(project_dir)}")


def _write_settings_local(project_dir: Path, pat: str, quiet: bool) -> None:
    """Merge env var + enabledMcpjsonServers into .claude/settings.local.json."""
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.local.json"

    data: dict = {}
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}

    # env section
    data.setdefault("env", {})[TOKEN_ENV_VAR] = pat

    # enabledMcpjsonServers — append if missing
    servers: list = data.setdefault("enabledMcpjsonServers", [])
    if MCP_SERVER_NAME not in servers:
        servers.append(MCP_SERVER_NAME)

    settings_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        rel = settings_file.relative_to(project_dir)
        _console.print(f"  [green]✓[/green] {rel}")


@app.callback(invoke_without_command=True)
def setup_claude_code(
    project_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--project-dir",
            help="Project root to write .mcp.json and .claude/settings.local.json into. "
                 "Defaults to the current working directory.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress all output (for use in hooks)."),
    ] = False,
    use_env_substitution: Annotated[
        bool,
        typer.Option(
            "--use-env-substitution",
            help=(
                "Write `Bearer ${POWERLOOM_MCP_TOKEN}` instead of the literal "
                "token in .mcp.json. Useful for shared/committed workspaces "
                "(e.g. the powerloom repo itself) where the token must come "
                "from each user's shell env. Default: write the literal token "
                "(more reliable — Claude Code's HTTP-transport MCP config "
                "doesn't always env-var-substitute inside header values)."
            ),
        ),
    ] = False,
) -> None:
    """Wire the Powerloom MCP server into a Claude Code project.

    Idempotent — safe to re-run. Existing config keys are preserved;
    only the powerloom-specific entries are written or updated.

    After running, restart Claude Code to activate the MCP server.
    """
    target = (project_dir or Path.cwd()).resolve()

    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print(
            "[yellow]Not signed in — run `weave auth login` first.[/yellow]"
        )
        raise typer.Exit(1)

    # Fetch mcp_proxy_id from /me
    try:
        me = auth_api.whoami(cfg)
    except PowerloomApiError as e:
        _console.print(f"[red]Failed to fetch account info:[/red] {e}")
        raise typer.Exit(1) from None

    proxy_id = me.get("mcp_proxy_id")
    if not proxy_id:
        _console.print(
            "[red]No MCP proxy ID on this account. Contact support.[/red]"
        )
        raise typer.Exit(1)

    url = _mcp_url(proxy_id)

    if not quiet:
        _console.print(
            f"[bold]Setting up Powerloom Claude Code plugin[/bold] in [dim]{target}[/dim]"
        )

    _write_mcp_json(
        target, url, quiet,
        pat=cfg.access_token,
        use_env_substitution=use_env_substitution,
    )
    _write_settings_local(target, cfg.access_token, quiet)

    if not quiet:
        _console.print()
        _console.print("[bold green]Done.[/bold green] Restart Claude Code to activate the MCP server.")
        _console.print(f"  MCP URL  : [dim]{url}[/dim]")
        _console.print(f"  Token var: [dim]{TOKEN_ENV_VAR}[/dim]")
