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

    # Sprint polish-doctor-resume-20260430 / thread f3ebdda4 —
    # launch readiness: clone-auth mode, machine credential health,
    # active sessions count.
    _append_launch_readiness_checks(checks, cfg)

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


def _append_launch_readiness_checks(
    checks: list[dict[str, Any]], cfg: Any
) -> None:
    """Sprint thread f3ebdda4 — extends `weave doctor` with launch-readiness.

    Appends rows in-place to the shared ``checks`` list. Each helper
    catches its own errors so one flaky engine call doesn't taint the
    rest of the doctor run.
    """
    # Org clone-auth mode (drives whether `weave open` returns a
    # server-minted GitHub App token or expects local creds).
    clone_auth_mode: Optional[str] = None
    try:
        from loomcli.client import PowerloomClient as _PC

        with _PC(cfg) as _client:
            settings = _client.get("/organizations/me/settings")
        if isinstance(settings, dict):
            clone_auth_mode = settings.get("clone_auth_mode")
    except Exception:  # noqa: BLE001
        # Endpoint may not be available (older engine, no permission, etc.).
        # Surface as warn rather than failing the whole doctor run.
        clone_auth_mode = None

    if clone_auth_mode:
        checks.append(
            _check(
                "launch.org.clone_auth_mode",
                "ok",
                clone_auth_mode,
            ),
        )
    else:
        checks.append(
            _check(
                "launch.org.clone_auth_mode",
                "warn",
                "could not read /organizations/me/settings",
            ),
        )

    # Machine credential — present? expires when?
    try:
        from loomcli.config import read_machine_credential as _read_mcred

        mcred = _read_mcred()
        if mcred is None:
            checks.append(
                _check(
                    "launch.machine_credential",
                    "info",
                    "none on this host (run `weave open <token>` to bootstrap)",
                ),
            )
        else:
            expires = mcred.get("expires_at", "?")
            credential_id = (mcred.get("credential_id") or "?")[:8]
            checks.append(
                _check(
                    "launch.machine_credential",
                    "ok",
                    f"id={credential_id}… expires={expires}",
                ),
            )
    except Exception:  # noqa: BLE001
        # Pre-Sprint-3 loomcli (or import failure) — silent.
        pass

    # Local clone-auth check — only meaningful when org policy is
    # local_credentials. Re-uses the preflight subsystem from sprint 4.
    if clone_auth_mode == "local_credentials":
        try:
            from loomcli._open.preflight import (
                check_local_clone_credentials as _check_creds,
            )

            cred_check = _check_creds("https://github.com")
            checks.append(
                _check(
                    "launch.local_clone_credentials",
                    cred_check.status,
                    cred_check.message.splitlines()[0],
                ),
            )
        except Exception:  # noqa: BLE001
            pass

    # Active sessions for this user — count + quick "see `weave session list`".
    try:
        from loomcli.client import PowerloomClient as _PC

        with _PC(cfg) as _client:
            sessions = _client.get("/agent-sessions", status="active")
        rows = sessions if isinstance(sessions, list) else []
        active_count = len(rows)
        checks.append(
            _check(
                "launch.active_sessions",
                "ok",
                f"{active_count} active session(s); `weave session list` for detail",
            ),
        )
    except Exception:  # noqa: BLE001
        pass


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
