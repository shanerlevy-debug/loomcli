"""`weave import <kind> <identifier>` — adopt an existing resource
into manifest form.

Emits a YAML fragment to stdout suitable for saving as a manifest.
Intended flow:

    weave import agent /dev-org/engineering/code-reviewer > agent.yaml
    # Edit agent.yaml to taste.
    weave apply agent.yaml

Only the core kinds ship with import in v009. Attachments, credentials,
and grants are most usefully imported alongside their parent agent —
the v009 implementation emits the agent fragment only and leaves
attachments for a follow-up pass.
"""
from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config
from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.parser import dump_resource_to_yaml
from loomcli.manifest.schema import (
    API_VERSION,
    AgentSpec,
    GroupSpec,
    KINDS,
    McpDeploymentSpec,
    McpServerRegistrationSpec,
    OUMetadata,
    OUPathScopedMetadata,
    OUSpec,
    Resource,
    SkillSpec,
)
from loomcli.commands.describe import _fetch, _looks_like_uuid

_console = Console()


def import_command(
    kind: Annotated[str, typer.Argument(help="Resource kind to import.")],
    identifier: Annotated[str, typer.Argument(help="UUID or OU-path.")],
) -> None:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in.[/yellow]")
        raise typer.Exit(1)

    kind_lower = kind.lower()
    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        try:
            row = _fetch(kind_lower, identifier, resolver, client)
        except PowerloomApiError as e:
            _console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        if row is None:
            _console.print(f"[red]Not found: {kind} {identifier}[/red]")
            raise typer.Exit(1)
        resource = _row_to_resource(kind_lower, row, resolver)

    if resource is None:
        _console.print(
            f"[yellow]import for kind {kind!r} is not implemented in v009. "
            "Supported kinds: ou, group, skill, mcp-server, mcp-deployment, agent.[/yellow]"
        )
        raise typer.Exit(1)

    typer.echo(dump_resource_to_yaml(resource))


def _row_to_resource(kind: str, row: dict, resolver: AddressResolver) -> Resource | None:
    """Pure mapping function: API row → Resource. Kept narrow — import
    in v009 supports the core kinds used in the demo manifest.
    """
    if kind == "ou":
        parent_path = (
            resolver.ou_id_to_path(row["parent_id"]) if row.get("parent_id") else None
        )
        return Resource(
            kind="OU",
            metadata=OUMetadata(name=row["name"], parent_ou_path=parent_path),
            spec=OUSpec(display_name=row["display_name"]),
            source_file="<imported>",
            doc_index=1,
        )
    if kind == "group":
        ou_path = resolver.ou_id_to_path(row["ou_id"])
        return Resource(
            kind="Group",
            metadata=OUPathScopedMetadata(name=row["name"], ou_path=ou_path or ""),
            spec=GroupSpec(
                display_name=row["display_name"],
                description=row.get("description"),
            ),
            source_file="<imported>",
            doc_index=1,
        )
    if kind == "skill":
        ou_path = resolver.ou_id_to_path(row["ou_id"])
        return Resource(
            kind="Skill",
            metadata=OUPathScopedMetadata(name=row["name"], ou_path=ou_path or ""),
            spec=SkillSpec(
                display_name=row.get("display_name") or row.get("display_title") or row["name"],
                description=row.get("description"),
                current_version_id=row.get("current_version_id"),
            ),
            source_file="<imported>",
            doc_index=1,
        )
    if kind == "mcp-server":
        ou_path = resolver.ou_id_to_path(row["ou_id"])
        return Resource(
            kind="MCPServerRegistration",
            metadata=OUPathScopedMetadata(name=row["name"], ou_path=ou_path or ""),
            spec=McpServerRegistrationSpec(
                display_name=row["display_name"],
                url=row["url"],
                description=row.get("description"),
            ),
            source_file="<imported>",
            doc_index=1,
        )
    if kind == "mcp-deployment":
        ou_path = resolver.ou_id_to_path(row["ou_id"])
        return Resource(
            kind="MCPDeployment",
            metadata=OUPathScopedMetadata(name=row["name"], ou_path=ou_path or ""),
            spec=McpDeploymentSpec(
                display_name=row["display_name"],
                template_kind=row["template_kind"],
                config=row.get("config_json") or {},
                policy=row.get("policy_json") or {},
            ),
            source_file="<imported>",
            doc_index=1,
        )
    if kind == "agent":
        ou_path = resolver.ou_id_to_path(row["ou_id"])
        # Pick a recognizable principal_ref — try user: lookup by id.
        principal_ref = f"principal-id:{row['owner_principal_id']}"
        try:
            users = resolver._client.get("/users")  # type: ignore[attr-defined]
            user = next(
                (u for u in users if u.get("principal_id") == row["owner_principal_id"]),
                None,
            )
            if user is not None:
                principal_ref = f"user:{user['email']}"
        except Exception:
            pass
        return Resource(
            kind="Agent",
            metadata=OUPathScopedMetadata(name=row["name"], ou_path=ou_path or ""),
            spec=AgentSpec(
                display_name=row["display_name"],
                description=row.get("description"),
                model=row["model"],
                system_prompt=row.get("system_prompt", ""),
                owner_principal_ref=principal_ref,
                skills=[],
                mcp_servers=[],
            ),
            source_file="<imported>",
            doc_index=1,
        )
    return None


# Silence unused-import warning
_ = (KINDS, API_VERSION, _looks_like_uuid)
