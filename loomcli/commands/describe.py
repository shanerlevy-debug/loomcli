"""`weave describe <kind> <id-or-path>` — show a single resource.

For OUs/agents/skills/mcp-deployments, accepts either UUID or an
OU-path-style identifier. Calls the matching GET endpoint and pretty-
prints the full response.
"""
from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config
from loomcli.manifest.addressing import AddressResolver

_console = Console()


def describe_command(
    kind: Annotated[str, typer.Argument(help="Resource kind (agent, ou, skill, mcp-deployment, ...).")],
    identifier: Annotated[str, typer.Argument(help="UUID or OU-path (e.g. /dev-org/engineering/code-reviewer).")],
) -> None:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        try:
            row = _fetch(kind.lower(), identifier, resolver, client)
        except PowerloomApiError as e:
            _console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    if row is None:
        _console.print(f"[red]Not found: {kind} {identifier}[/red]")
        raise typer.Exit(1)
    _console.print_json(json.dumps(row, default=str))


def _fetch(kind: str, identifier: str, resolver: AddressResolver, client: PowerloomClient):
    # UUID → direct GET.
    if _looks_like_uuid(identifier):
        path = _kind_to_detail_path(kind, identifier)
        return client.get(path)

    # OU-path: resolve parent_ou + name → ID.
    parent, _, name = identifier.rstrip("/").rpartition("/")
    if kind in ("ou",):
        ou_id = resolver.try_ou_path_to_id(identifier)
        return client.get(f"/ous/{ou_id}") if ou_id else None
    ou_id = resolver.try_ou_path_to_id(parent) if parent else None
    if ou_id is None:
        return None
    list_path = {
        "agent": "/agents",
        "skill": "/skills",
        "group": "/groups",
        "mcp-server": "/mcp-servers",
        "mcp-deployment": "/mcp-deployments",
    }.get(kind)
    if list_path is None:
        raise typer.BadParameter(f"unsupported kind for path-lookup: {kind}")
    row = resolver.find_in_ou(list_path=list_path, ou_id=ou_id, name=name)
    if row is None:
        return None
    return client.get(f"{list_path}/{row['id']}")


def _kind_to_detail_path(kind: str, ident: str) -> str:
    mapping = {
        "ou": f"/ous/{ident}",
        "agent": f"/agents/{ident}",
        "skill": f"/skills/{ident}",
        "group": f"/groups/{ident}",
        "mcp-server": f"/mcp-servers/{ident}",
        "mcp-deployment": f"/mcp-deployments/{ident}",
        "session": f"/sessions/{ident}",
    }
    path = mapping.get(kind)
    if not path:
        raise typer.BadParameter(f"describe doesn't know kind={kind!r}")
    return path


def _looks_like_uuid(s: str) -> bool:
    import re
    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            s,
        )
    )
