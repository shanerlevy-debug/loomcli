"""`weave doctor` environment and server compatibility checks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Annotated, Any, Optional

import typer
from packaging import version
from rich.console import Console
from rich.table import Table

from loomcli import __version__
from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import (
    config_dir,
    credentials_file,
    is_agent_mode,
    is_json_output,
    load_runtime_config,
)
from loomcli.plugin_assets import plugin_export_root


_console = Console()


def doctor_command(
    auto_upgrade: Annotated[
        bool,
        typer.Option("--auto-upgrade", help="Automatically run `pip install -U loomcli` if behind recommended version."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress output on the happy path (version up to date)."),
    ] = False,
) -> None:
    """Check local auth, server capabilities, and plugin prerequisites."""
    cfg = load_runtime_config()
    checks: list[dict[str, Any]] = [
        _check("loomcli.version", "ok", __version__),
        _check("python.executable", "ok", sys.executable),
        _check("python.version", "ok", sys.version.split()[0]),
        _check(
            "stdio.encoding",
            "ok",
            f"stdout={getattr(sys.stdout, 'encoding', None) or '?'} "
            f"stderr={getattr(sys.stderr, 'encoding', None) or '?'}",
        ),
        _check("config.dir", "ok", str(config_dir())),
        _check("plugin.export_root", "ok", str(plugin_export_root())),
        _check(
            "credentials",
            "ok" if cfg.access_token else "warn",
            str(credentials_file()) if cfg.access_token else "not signed in; run `weave login`",
        ),
        _check("api.url", "ok", cfg.api_base_url),
        _check(
            "agent_mode",
            "ok" if is_agent_mode() else "info",
            "active (compact output)" if is_agent_mode() else "inactive (human output)",
        ),
    ]

    capabilities: dict[str, Any] | None = None
    try:
        with PowerloomClient(cfg) as client:
            capabilities = client.get("/capabilities")
        
        server_ver = capabilities.get("server_version", "?")
        checks.append(
            _check(
                "server.capabilities",
                "ok",
                f"{server_ver} {capabilities.get('api_contract_version', '')}",
            )
        )

        # v061.x — CLI version health check
        min_ver = capabilities.get("min_loomcli_version")
        rec_ver = capabilities.get("recommended_loomcli_version")
        local_ver = version.parse(__version__)

        if min_ver:
            min_v = version.parse(min_ver)
            rec_v = version.parse(rec_ver) if rec_ver else min_v
            
            needs_upgrade = local_ver < rec_v
            is_blocking = local_ver < min_v

            if is_blocking:
                msg = f"BLOCKING — your version cannot talk to the engine. Minimum required is {min_ver}."
                if not auto_upgrade:
                    msg += " Run `pip install -U loomcli`."
                checks.append(_check("loomcli.health", "fail", msg))
            elif needs_upgrade:
                msg = f"UPDATE RECOMMENDED. Server suggests {rec_ver}."
                if not auto_upgrade:
                    msg += " Run `pip install -U loomcli`."
                checks.append(_check("loomcli.health", "warn", msg))
            else:
                checks.append(_check("loomcli.health", "ok", "Up to date"))

            if needs_upgrade and auto_upgrade:
                if not quiet:
                    level = "[red]BLOCKING[/red]" if is_blocking else "[yellow]RECOMMENDED[/yellow]"
                    _console.print(f"{level} loomcli upgrade found: {__version__} -> {rec_ver}")
                
                # Detect venv context (standard check: sys.prefix != sys.base_prefix)
                is_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
                pip_args = [sys.executable, "-m", "pip", "install", "--upgrade", "loomcli"]
                if not is_venv:
                    pip_args.insert(4, "--user")
                
                if not quiet:
                    _console.print(f"Running: [dim]{' '.join(pip_args)}[/dim]")
                
                try:
                    # Run upgrade. Capture output so we can show it on failure.
                    res = subprocess.run(pip_args, capture_output=True, text=True, check=False)
                    if res.returncode == 0:
                        if not quiet:
                            _console.print("[green]Upgrade successful.[/green] Please restart your session if needed.")
                        # Update the check status in the list so JSON output reflects success
                        for c in checks:
                            if c["key"] == "loomcli.health":
                                c["status"] = "ok"
                                c["detail"] = f"Upgraded to {rec_ver} (was {__version__})"
                    else:
                        _console.print("[red]Upgrade failed.[/red]")
                        if res.stdout: _console.print(f"[dim]{res.stdout}[/dim]")
                        if res.stderr: _console.print(f"[red]{res.stderr}[/red]")
                        # Failures are non-blocking per §4.11 but we mark the check as fail
                        for c in checks:
                            if c["key"] == "loomcli.health":
                                c["status"] = "fail"
                                c["detail"] = f"Upgrade failed: {res.stderr[:200]}"
                except Exception as e:
                    _console.print(f"[red]Upgrade error:[/red] {e}")

        actor_kinds = set(capabilities.get("actor_kinds") or [])
        for actor_kind in ("claude_code", "codex_cli", "gemini_cli", "antigravity"):
            checks.append(
                _check(
                    f"actor_kind.{actor_kind}",
                    "ok" if actor_kind in actor_kinds else "warn",
                    "supported" if actor_kind in actor_kinds else "not advertised by server",
                )
            )
        route_keys = {r.get("key") for r in capabilities.get("routes") or []}
        checks.append(
            _check(
                "route.threads_my_work",
                "ok" if "threads_my_work" in route_keys else "warn",
                "available" if "threads_my_work" in route_keys else "not advertised by server",
            )
        )
    except PowerloomApiError as e:
        checks.append(_check("server.capabilities", "fail", str(e)))

    if cfg.access_token:
        try:
            with PowerloomClient(cfg) as client:
                me = client.get("/me")
            checks.append(
                _check(
                    "auth.whoami",
                    "ok",
                    f"{me.get('email', '?')} org={me.get('organization_id', '?')}",
                )
            )
        except PowerloomApiError as e:
            checks.append(_check("auth.whoami", "fail", str(e)))

    for tool in ("weave", "codex", "gemini", "claude"):
        found = shutil.which(tool)
        checks.append(
            _check(
                f"path.{tool}",
                "ok" if found else ("warn" if tool != "weave" else "fail"),
                found or "not found on PATH",
            )
        )

    if is_json_output():
        typer.echo(json.dumps({"checks": checks, "capabilities": capabilities}, indent=2, default=str))
        return

    table = Table(title="Powerloom doctor", show_header=True)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for row in checks:
        table.add_row(row["key"], _status_label(row["status"]), row["detail"])
    _console.print(table)

    if any(row["status"] == "fail" for row in checks):
        raise typer.Exit(1)


def _check(key: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "status": status, "detail": detail}


def _status_label(status: str) -> str:
    if status == "ok":
        return "[green]ok[/green]"
    if status == "warn":
        return "[yellow]warn[/yellow]"
    if status == "info":
        return "[blue]info[/blue]"
    return "[red]fail[/red]"
