"""`weave doctor` environment and server compatibility checks."""
from __future__ import annotations

import json
import shutil
import sys
from typing import Annotated, Any

import typer
from packaging import version
from rich.console import Console
from rich.table import Table

from loomcli import __version__
from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import config_dir, credentials_file, load_runtime_config
from loomcli.plugin_assets import plugin_export_root


_console = Console()


def doctor_command(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable check results."),
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
            if local_ver < version.parse(min_ver):
                checks.append(
                    _check(
                        "loomcli.health",
                        "fail",
                        f"OUTDATED. Minimum required is {min_ver}. Run `pip install -U loomcli`.",
                    )
                )
            elif rec_ver and local_ver < version.parse(rec_ver):
                checks.append(
                    _check(
                        "loomcli.health",
                        "warn",
                        f"UPDATE RECOMMENDED. Server suggests {rec_ver}. Run `pip install -U loomcli`.",
                    )
                )
            else:
                checks.append(_check("loomcli.health", "ok", "Up to date"))

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

    if json_out:
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
    return "[red]fail[/red]"
