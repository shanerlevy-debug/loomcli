"""`weave commands` - export command metadata."""
from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from loomcli.command_registry import list_commands

_console = Console()


def commands_command(
    prefix: Annotated[
        str | None,
        typer.Option("--prefix", help="Only include commands with this prefix."),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable command metadata."),
    ] = False,
) -> None:
    """List command metadata for autocomplete, plugins, and mobile clients."""
    rows = list_commands(prefix=prefix)
    if json_out:
        _console.print_json(json.dumps(rows, default=str))
        return

    table = Table(title="Weave commands", show_header=True)
    table.add_column("Command")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Summary")
    for row in rows:
        table.add_row(
            row["command"],
            row["category"],
            row["status"],
            row["summary"],
        )
    _console.print(table)
