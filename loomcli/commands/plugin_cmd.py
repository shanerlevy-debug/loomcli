"""Client-plugin setup helpers."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from loomcli.config import is_json_output
from loomcli.plugin_assets import (
    CLIENT_NAMES,
    PluginAssetError,
    plugin_export_root,
    plugin_path,
)

app = typer.Typer(no_args_is_help=True, help="Inspect and install Powerloom client plugins.")
_console = Console()


def _client_specs() -> dict[str, dict[str, Any]]:
    return {client: _client_spec(client) for client in CLIENT_NAMES}


def _client_spec(client: str) -> dict[str, Any]:
    if client == "claude-code":
        claude_path = plugin_path("claude-code")
        return {
            "binary": "claude",
            "path": claude_path,
            "instructions": [
                "weave plugin install claude-code --execute --project-dir <path-to-powerloom-checkout>",
                "or: weave setup-claude-code --project-dir <path-to-powerloom-checkout>",
                f"claude --plugin-dir {claude_path}",
            ],
            "install": ["weave", "setup-claude-code"],
        }
    if client == "codex":
        codex_path = plugin_path("codex")
        return {
            "binary": "codex",
            "path": codex_path,
            "instructions": [
                f"codex plugin marketplace add {codex_path}",
                "Enable powerloom-weave@powerloom in Codex if it is not auto-enabled.",
            ],
            "install": ["codex", "plugin", "marketplace", "add", str(codex_path)],
        }
    if client == "gemini":
        gemini_path = plugin_path("gemini")
        return {
            "binary": "gemini",
            "path": gemini_path,
            "instructions": [
                f"gemini extensions install {gemini_path} --consent --skip-settings",
                "gemini extensions enable powerloom-weave",
            ],
            "install": [
                "gemini",
                "extensions",
                "install",
                str(gemini_path),
                "--consent",
                "--skip-settings",
            ],
        }
    if client == "antigravity":
        return {
            "binary": None,
            "path": plugin_path("antigravity"),
            "instructions": [
                "Powerloom uses the bundled Gemini-style extension assets for Antigravity.",
                f"Extension assets: {plugin_path('antigravity')}",
                "Open ~/.gemini/antigravity/mcp_config.json.",
                "Add Powerloom as a local stdio or hosted remote MCP server.",
                "Restart Antigravity so it reloads MCP config.",
            ],
            "install": None,
        }
    expected = ", ".join(CLIENT_NAMES)
    raise PluginAssetError(f"unknown client {client!r}; expected one of: {expected}")


def _spec_or_exit(client: str) -> dict[str, Any]:
    if client not in CLIENT_NAMES:
        _console.print(
            f"[red]Unknown client {client!r}.[/red] "
            f"Expected one of: {', '.join(CLIENT_NAMES)}"
        )
        raise typer.Exit(2)
    try:
        return _client_spec(client)
    except PluginAssetError as e:
        _console.print(f"[red]Plugin assets unavailable:[/red] {e}")
        raise typer.Exit(1) from e


@app.command("doctor")
def doctor_cmd(
    client: Annotated[
        str | None,
        typer.Argument(help="Optional client to check."),
    ] = None,
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Attempt to fix identified issues automatically."),
    ] = False,
) -> None:
    """Check local plugin files and client binaries."""
    try:
        specs = {client: _client_spec(client)} if client else _client_specs()
        asset_error = None
    except PluginAssetError as e:
        specs = {}
        asset_error = str(e)
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
                "install_command": spec.get("install") or [],
            }
        )
    if asset_error:
        rows.append(
            {
                "client": client or "all",
                "plugin_path": "",
                "plugin_path_status": "fail",
                "binary": "",
                "binary_status": "warn",
                "binary_path": asset_error,
                "install_command": [],
            }
        )

    if is_json_output():
        typer.echo(
            json.dumps(
                {"export_root": str(plugin_export_root()), "clients": rows},
                indent=2,
            )
        )
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


@app.command("path")
def path_cmd(
    client: Annotated[str, typer.Argument(help="Client name.")],
) -> None:
    """Print the exported plugin path for one client."""
    try:
        path = plugin_path(client)
    except PluginAssetError as e:
        if is_json_output():
            typer.echo(json.dumps({"client": client, "error": str(e)}, indent=2))
        else:
            _console.print(f"[red]Plugin assets unavailable:[/red] {e}")
        raise typer.Exit(1) from e
    if is_json_output():
        typer.echo(json.dumps({"client": client, "path": str(path)}, indent=2))
        return
    typer.echo(str(path))


_INSTALL_HINTS: dict[str, str] = {
    "gemini": (
        "Install the Gemini CLI first: "
        "https://github.com/google-gemini/gemini-cli "
        "(npm: `npm install -g @google/gemini-cli`)."
    ),
    "codex": (
        "Install the OpenAI Codex CLI first: "
        "https://github.com/openai/codex"
    ),
    "claude": (
        "Install Claude Code first: "
        "https://docs.anthropic.com/en/docs/claude-code/quickstart"
    ),
}


@app.command("install")
def install_cmd(
    client: Annotated[str, typer.Argument(help="Client name.")],
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually run the install command. Default prints it only."),
    ] = False,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help=(
                "Claude Code project root to configure. Defaults to the current "
                "directory. Ignored by clients that install into their own config."
            ),
        ),
    ] = None,
    use_env_substitution: Annotated[
        bool,
        typer.Option(
            "--use-env-substitution",
            help=(
                "Claude Code only: write Bearer ${POWERLOOM_MCP_TOKEN} instead "
                "of inlining the token in .mcp.json."
            ),
        ),
    ] = False,
) -> None:
    """Install or print the install command for one client plugin."""
    spec = _spec_or_exit(client)
    command = _install_command(client, spec, project_dir, use_env_substitution)
    if not command:
        _console.print("[yellow]This client needs manual setup.[/yellow]")
        for line in spec["instructions"]:
            _console.print(f"  {line}")
        return

    _console.print(_format_command(command))
    if not execute:
        _console.print("[dim]Dry run. Re-run with --execute to run it.[/dim]")
        return

    # v0.7.x — pre-flight check that the client binary is on PATH so the
    # error message is actionable. Without this, Windows surfaces the
    # subprocess failure as "[WinError 2] The system cannot find the file
    # specified" with no hint about which file or why.
    binary = command[0] if command else None
    resolved_binary = shutil.which(binary) if binary else None
    if binary and resolved_binary is None:
        _console.print(
            f"[red]Install failed:[/red] {binary!r} is not on PATH."
        )
        hint = _INSTALL_HINTS.get(binary)
        if hint:
            _console.print(f"  {hint}")
        _console.print(
            "  Once installed, re-run "
            f"[bold]weave plugin install {client} --execute[/bold]."
        )
        raise typer.Exit(1)

    run_command = (
        [resolved_binary, *command[1:]]
        if resolved_binary is not None
        else command
    )
    if client == "codex" and resolved_binary is not None:
        if not _prepare_codex_marketplace_install(resolved_binary, Path(command[-1])):
            return
    try:
        subprocess.run(run_command, check=True)
    except FileNotFoundError as e:
        # Shouldn't reach here with the pre-flight above, but keep the
        # safety net in case the binary disappears between which() and run().
        _console.print(
            f"[red]Install failed:[/red] could not exec {binary!r} ({e}). "
            "Is it on PATH?"
        )
        raise typer.Exit(1) from e
    except subprocess.CalledProcessError as e:
        _console.print(
            f"[red]Install failed:[/red] {binary!r} exited {e.returncode}."
        )
        raise typer.Exit(1) from e


def _status(status: str, value: str) -> str:
    if status == "ok":
        return f"[green]ok[/green] {value}"
    if status == "warn":
        return f"[yellow]warn[/yellow] {value}"
    return f"[red]fail[/red] {value}"


def _format_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _install_command(
    client: str,
    spec: dict[str, Any],
    project_dir: Path | None,
    use_env_substitution: bool,
) -> list[str] | None:
    command = spec.get("install")
    if command is None:
        return None

    command = list(command)
    if client == "claude-code":
        if project_dir is not None:
            command.extend(["--project-dir", str(project_dir)])
        if use_env_substitution:
            command.append("--use-env-substitution")
    return command


def _prepare_codex_marketplace_install(codex_executable: str, marketplace_path: Path) -> bool:
    existing = _codex_marketplace_source()
    if not existing:
        return True
    if _same_local_source(existing, str(marketplace_path)):
        _console.print(
            "[green]Codex marketplace 'powerloom' already points at the exported path.[/green]"
        )
        return False

    _console.print(
        "[yellow]Replacing existing Codex marketplace 'powerloom' from a different source.[/yellow]"
    )
    try:
        subprocess.run(
            [codex_executable, "plugin", "marketplace", "remove", "powerloom"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        _console.print(
            f"[red]Install failed:[/red] could not remove existing Codex marketplace "
            f"'powerloom' (exit {e.returncode})."
        )
        raise typer.Exit(1) from e
    return True


def _codex_marketplace_source() -> str | None:
    path = Path.home() / ".codex" / "config.toml"
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    marketplaces = data.get("marketplaces") or {}
    if not isinstance(marketplaces, dict):
        return None
    powerloom = marketplaces.get("powerloom") or {}
    if not isinstance(powerloom, dict):
        return None
    source = powerloom.get("source")
    return str(source) if source else None


def _same_local_source(left: str, right: str) -> bool:
    return _normalize_local_source(left) == _normalize_local_source(right)


def _normalize_local_source(value: str) -> str:
    value = value.removeprefix("\\\\?\\")
    return os.path.normcase(os.path.normpath(value))
