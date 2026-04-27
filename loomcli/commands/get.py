"""`weave get <kind> [--ou <ou-path>]` — list resources.

One row per resource. Columns are kind-specific. kubectl conventions:
tabular output by default, `-o yaml` emits a manifest fragment.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config
from loomcli.manifest.addressing import AddressResolver


_console = Console()


# Map kubectl-ish kinds to the (list_path, columns) tuple.
_LISTABLE = {
    "ou": ("/ous", ["name", "display_name", "parent_id"]),
    "ous": ("/ous", ["name", "display_name", "parent_id"]),
    "group": ("/groups", ["name", "display_name"]),
    "groups": ("/groups", ["name", "display_name"]),
    "skill": ("/skills", ["name", "display_title", "cma_skill_id"]),
    "skills": ("/skills", ["name", "display_title", "cma_skill_id"]),
    "agent": ("/agents", ["name", "model", "cma_agent_id"]),
    "agents": ("/agents", ["name", "model", "cma_agent_id"]),
    "mcp-server": ("/mcp-servers", ["name", "url"]),
    "mcp-servers": ("/mcp-servers", ["name", "url"]),
    "mcp-deployment": ("/mcp-deployments", ["name", "template_kind", "status"]),
    "mcp-deployments": ("/mcp-deployments", ["name", "template_kind", "status"]),
    "session": ("/sessions", ["id", "status", "event_count"]),
    "sessions": ("/sessions", ["id", "status", "event_count"]),
}


def get_command(
    kind: Annotated[str, typer.Argument(help=f"Resource kind. One of: {', '.join(sorted(set(_LISTABLE)))}.")],
    ou: Annotated[
        str | None,
        typer.Option("--ou", help="Filter to resources in the given OU path."),
    ] = None,
    output: Annotated[
        Optional[str],
        typer.Option("-o", "--output", help="Output format."),
    ] = None,
    tree: Annotated[
        bool,
        typer.Option("--tree", help="Show OU hierarchy as a tree (OUs only)."),
    ] = False,
) -> None:
    wiring = _LISTABLE.get(kind.lower())
    if wiring is None:
        _console.print(
            f"[red]Unknown kind {kind!r}. Known: {sorted(set(_LISTABLE))}[/red]"
        )
        raise typer.Exit(1)
    list_path, columns = wiring

    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    # Use explicitly provided output format, or fall back to config/env default
    output_format = output or cfg.default_output or "table"

    params: dict[str, str] = {}
    with PowerloomClient(cfg) as client:
        if tree and kind.lower() not in ("ou", "ous"):
            _console.print("[red]--tree is only supported for OUs.[/red]")
            raise typer.Exit(1)

        if tree:
            try:
                # OUs have a special tree endpoint
                data = client.get("/ous/tree")
                if output_format == "json":
                    _console.print_json(json.dumps(data))
                    return
                _print_ou_tree(data)
                return
            except PowerloomApiError as e:
                _console.print(f"[red]{e}[/red]")
                raise typer.Exit(1)

        if ou:
            resolver = AddressResolver(client)
            ou_id = resolver.try_ou_path_to_id(ou)
            if ou_id is None:
                _console.print(f"[red]OU not found: {ou}[/red]")
                raise typer.Exit(1)
            params["ou_id"] = ou_id
        try:
            rows = client.get(list_path, **params) if params else client.get(list_path)
        except PowerloomApiError as e:
            _console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

    if not isinstance(rows, list):
        _console.print(f"[red]Unexpected shape from {list_path}[/red]")
        raise typer.Exit(1)

    if output_format == "json":
        _console.print_json(json.dumps(rows))
        return

    table = Table(title=f"{kind} — {len(rows)} row(s)", show_header=True)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(row.get(c, "")) for c in columns))
    _console.print(table)


def _print_ou_tree(tree: list[dict[str, Any]]) -> None:
    from rich.tree import Tree

    def add_children(rich_tree: Tree, nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            branch = rich_tree.add(
                f"[bold]{node['name']}[/bold] [dim]({node['display_name']})[/dim]"
            )
            add_children(branch, node.get("children", []))

    root_tree = Tree("Organization Units")
    add_children(root_tree, tree)
    _console.print(root_tree)
