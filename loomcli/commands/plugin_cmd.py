"""Client-plugin setup helpers."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table


CLIENT_NAMES = ("claude-code", "codex", "gemini", "antigravity")

app = typer.Typer(no_args_is_help=True, help="Inspect and install Powerloom client plugins.")
_console = Console()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _client_specs() -> dict[str, dict[str, Any]]:
    root = _repo_root()
    return {
        "claude-code": {
            "binary": "claude",
            "path": root / "plugin",
            "instructions": [
                "weave setup-claude-code --project <path-to-powerloom-checkout>",
                f"claude --plugin-dir {root / 'plugin'}",
            ],
            "install": ["weave", "setup-claude-code"],
        },
        "codex": {
            "binary": "codex",
            "path": root / "plugins" / "codex",
            "instructions": [
                f"codex plugin marketplace add {root / 'plugins' / 'codex'}",
                "Enable powerloom-weave@powerloom in Codex if it is not auto-enabled.",
            ],
            "install": ["codex", "plugin", "marketplace", "add", str(root / "plugins" / "codex")],
        },
        "gemini": {
            "binary": "gemini",
            "path": root / "plugins" / "gemini" / "powerloom-weave",
            "instructions": [
                f"gemini extensions install {root / 'plugins' / 'gemini' / 'powerloom-weave'} --consent --skip-settings",
                "gemini extensions enable powerloom-weave",
            ],
            "install": [
                "gemini",
                "extensions",
                "install",
                str(root / "plugins" / "gemini" / "powerloom-weave"),
                "--consent",
                "--skip-settings",
            ],
        },
        "antigravity": {
            "binary": None,
            "path": root / "plugins" / "gemini" / "powerloom-weave",
            "instructions": [
                "Open ~/.gemini/antigravity/mcp_config.json.",
                "Add Powerloom as a local stdio or hosted remote MCP server.",
                "Restart Antigravity so it reloads MCP config.",
            ],
            "install": None,
        },
    }


def _spec_or_exit(client: str) -> dict[str, Any]:
    specs = _client_specs()
    if client not in specs:
        _console.print(
            f"[red]Unknown client {client!r}.[/red] "
            f"Expected one of: {', '.join(CLIENT_NAMES)}"
        )
        raise typer.Exit(2)
    return specs[client]


@app.command("doctor")
def doctor_cmd(
    client: Annotated[
        str | None,
        typer.Argument(help="Optional client to check."),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Attempt to fix identified issues automatically."),
    ] = False,
) -> None:
    """Check local plugin files and client binaries."""
    specs = _client_specs()
    if client and client not in specs:
        _spec_or_exit(client)
    keys = [client] if client else sorted(specs)
    rows = []
    for key in keys:
        spec = specs[key]
        binary = spec.get("binary")
        binary_path = shutil.which(binary) if binary else None
        plugin_path = Path(spec["path"])
        
        status_plugin = "ok" if plugin_path.exists() else "fail"
        status_binary = "ok" if (not binary or binary_path) else "warn"

        if fix:
            if status_plugin == "fail" or status_binary == "warn":
                _console.print(f"[bold]Attempting to fix {key}...[/bold]")
                install_cmd(key, execute=True)
                # Re-check
                binary_path = shutil.which(binary) if binary else None
                status_plugin = "ok" if plugin_path.exists() else "fail"
                status_binary = "ok" if (not binary or binary_path) else "warn"
            
            # Special case for Gemini: ensure enablement if requested
            if key == "gemini" and status_plugin == "ok" and status_binary == "ok":
                _console.print("Enabling gemini extension...")
                try:
                    subprocess.run(["gemini", "extensions", "enable", "powerloom-weave"], check=True)
                except (FileNotFoundError, subprocess.CalledProcessError) as e:
                    _console.print(f"[yellow]Enable failed (non-critical):[/yellow] {e}")

        rows.append(
            {
                "client": key,
                "plugin_path": str(plugin_path),
                "plugin_path_status": status_plugin,
                "binary": binary or "(manual config)",
                "binary_status": status_binary,
                "binary_path": binary_path or "",
            }
        )

    if json_out:
        typer.echo(json.dumps(rows, indent=2))
        return

    table = Table(title="Powerloom plugin doctor", show_header=True)
    for col in ("client", "plugin", "binary", "detail"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            row["client"],
            _status(row["plugin_path_status"], row["plugin_path"]),
            _status(row["binary_status"], row["binary"]),
            row["binary_path"],
        )
    _console.print(table)

    if any(row["plugin_path_status"] == "fail" for row in rows):
        raise typer.Exit(1)


@app.command("instructions")
def instructions_cmd(
    client: Annotated[str, typer.Argument(help="Client name.")],
) -> None:
    """Print install/setup instructions for one client."""
    spec = _spec_or_exit(client)
    _console.print(f"[bold]{client}[/bold]")
    for line in spec["instructions"]:
        _console.print(f"  {line}")


@app.command("install")
def install_cmd(
    client: Annotated[str, typer.Argument(help="Client name.")],
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually run the install command. Default prints it only."),
    ] = False,
) -> None:
    """Install or print the install command for one client plugin."""
    spec = _spec_or_exit(client)
    command = spec.get("install")
    if not command:
        _console.print("[yellow]This client needs manual setup.[/yellow]")
        for line in spec["instructions"]:
            _console.print(f"  {line}")
        return

    _console.print(" ".join(command))
    if not execute:
        _console.print("[dim]Dry run. Re-run with --execute to run it.[/dim]")
        return
    try:
        subprocess.run(command, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        _console.print(f"[red]Install failed:[/red] {e}")
        raise typer.Exit(1) from e


def _status(status: str, value: str) -> str:
    if status == "ok":
        return f"[green]ok[/green] {value}"
    if status == "warn":
        return f"[yellow]warn[/yellow] {value}"
    return f"[red]fail[/red] {value}"
