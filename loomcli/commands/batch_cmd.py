"""`weave batch` — run multiple commands in one process.

Useful for shell-agnostic chaining (avoiding && issues in PowerShell)
and for agents to perform setup-heavy sequences efficiently.
"""
from __future__ import annotations

import shlex
import sys
from typing import Annotated, Optional

import typer
from rich.console import Console

_console = Console()

def batch_command(
    commands: Annotated[
        Optional[list[str]],
        typer.Argument(help="List of commands to run (e.g. 'agent status' 'ask hello')"),
    ] = None,
    file: Annotated[
        Optional[typer.FileText],
        typer.Option("--file", "-f", help="Read commands from a file (one per line)."),
    ] = None,
    stop_on_error: Annotated[
        bool,
        typer.Option("--stop-on-error/--continue-on-error", help="Stop execution if a command fails."),
    ] = True,
) -> None:
    """Run multiple weave commands sequentially."""
    from loomcli.cli import app
    
    cmds_to_run = []
    if commands:
        cmds_to_run.extend(commands)
    if file:
        cmds_to_run.extend([line.strip() for line in file if line.strip() and not line.startswith("#")])

    if not cmds_to_run:
        _console.print("[yellow]No commands provided to batch.[/yellow]")
        return

    for cmd_str in cmds_to_run:
        _console.print(f"[bold cyan]batch:[/bold cyan] weave {cmd_str}")
        
        # Split command string into arguments
        # We assume the user didn't include 'weave' in the string, but if they did, we strip it
        args = shlex.split(cmd_str)
        if args and args[0] == "weave":
            args = args[1:]
            
        try:
            # We use app() which is the Typer entry point.
            # Typer/Click usually expects sys.argv[1:], so we monkeypatch sys.argv
            # or use a more direct invocation if possible.
            # For Typer, the cleanest way to re-invoke is often via a subprocess 
            # OR by calling the app with a list of strings.
            # app(args) works in Typer.
            app(args)
        except SystemExit as e:
            if e.code != 0 and stop_on_error:
                _console.print(f"[red]Batch failed on command:[/red] weave {cmd_str}")
                sys.exit(e.code)
        except Exception as e:
            _console.print(f"[red]Batch error:[/red] {e}")
            if stop_on_error:
                sys.exit(1)
