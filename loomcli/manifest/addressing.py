"""OU-path addressing + resource address derivation.

OU paths are the canonical identifier for anything OU-scoped:

    /dev-org                        # root OU of the org
    /dev-org/engineering            # child
    /dev-org/engineering/backend    # grandchild

The control plane doesn't expose OU-paths directly — OUs have UUIDs
and parent pointers. This module caches GET /ous/tree once per CLI
run and resolves paths to UUIDs on demand.

principal_ref grammar:
    user:<email>
    group:<group_path>
    ou:<ou_path>

Only used in GroupMembership and RoleBinding metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.manifest.schema import (
    AgentMcpAttachmentMetadata,
    AgentSkillAttachmentMetadata,
    CredentialMetadata,
    GroupMembershipMetadata,
    OUMetadata,
    OUPathScopedMetadata,
    Resource,
    RoleBindingMetadata,
    SkillGrantMetadata,
)


@dataclass
class _OUNode:
    id: str
    name: str
    display_name: str
    parent_id: str | None
    children: list["_OUNode"] = field(default_factory=list)


class AddressResolutionError(Exception):
    pass


class AddressResolver:
    """Stateful resolver: caches the OU tree + lookups. One per CLI
    invocation. Not thread-safe; CLI is serial by design."""

    def __init__(self, client: PowerloomClient):
        self._client = client
        self._ou_path_to_id: dict[str, str] | None = None
        self._ou_id_to_path: dict[str, str] | None = None
        # Lazy caches for listed resources keyed by (organization, kind).
        self._cache: dict[str, list[dict[str, Any]]] = {}

    # -------- OU tree --------
    def _load_ou_tree(self) -> None:
        if self._ou_path_to_id is not None:
            return
        tree = self._client.get("/ous/tree")
        if not isinstance(tree, list):
            raise AddressResolutionError(
                f"/ous/tree returned unexpected shape: {type(tree).__name__}"
            )
        path_to_id: dict[str, str] = {}
        id_to_path: dict[str, str] = {}

        def walk(node: dict[str, Any], parent_path: str) -> None:
            path = f"{parent_path}/{node['name']}"
            path_to_id[path] = node["id"]
            id_to_path[node["id"]] = path
            for child in node.get("children", []):
                walk(child, path)

        for root in tree:
            walk(root, "")
        self._ou_path_to_id = path_to_id
        self._ou_id_to_path = id_to_path

    def ou_path_to_id(self, path: str) -> str:
        self._load_ou_tree()
        assert self._ou_path_to_id is not None
        try:
            return self._ou_path_to_id[path.rstrip("/")]
        except KeyError as e:
            raise AddressResolutionError(
                f"OU path {path!r} not found. Known paths: {sorted(self._ou_path_to_id)}"
            ) from e

    def ou_id_to_path(self, ou_id: str) -> str | None:
        self._load_ou_tree()
        assert self._ou_id_to_path is not None
        return self._ou_id_to_path.get(ou_id)

    def try_ou_path_to_id(self, path: str) -> str | None:
        """Non-raising variant — returns None when the OU doesn't
        exist yet. Used at plan time for freshly-created OUs that
        haven't been applied."""
        self._load_ou_tree()
        assert self._ou_path_to_id is not None
        return self._ou_path_to_id.get(path.rstrip("/"))

    # -------- generic resource lookup --------
    def find_in_ou(
        self,
        *,
        list_path: str,
        ou_id: str,
        name: str,
        cache_key: str | None = None,
    ) -> dict[str, Any] | None:
        """GET <list_path>?ou_id=<id> and return the row whose name
        matches. Shared helper used by agents, skills, mcp-servers,
        deployments."""
        key = cache_key or f"{list_path}?ou_id={ou_id}"
        if key not in self._cache:
            try:
                rows = self._client.get(list_path, ou_id=ou_id)
            except PowerloomApiError as e:
                if e.status_code == 404:
                    return None
                raise
            self._cache[key] = rows if isinstance(rows, list) else []
        for row in self._cache[key]:
            if row.get("name") == name:
                return row
        return None

    def invalidate_cache_for(self, list_path: str) -> None:
        """Called by the applier after a successful create/update so
        subsequent lookups re-read."""
        stale = [k for k in self._cache if k.startswith(list_path)]
        for k in stale:
            self._cache.pop(k, None)


# ---------------------------------------------------------------------------
# resource_address — canonical human-readable address for plan output
# ---------------------------------------------------------------------------
def resource_address(r: Resource) -> str:
    """Shape varies by kind. Examples:
        OU:                     /dev-org/engineering
        Agent:                  /dev-org/engineering/code-reviewer (Agent)
        AgentSkill attachment:  /dev-org/engineering/code-reviewer ↔ python-lint
        RoleBinding:            user:jane@dev.local → AgentViewer @ /dev-org/engineering
    """
    m = r.metadata
    if isinstance(m, OUMetadata):
        parent = (m.parent_ou_path or "").rstrip("/")
        return f"{parent}/{m.name}"
    if isinstance(m, OUPathScopedMetadata):
        return f"{m.ou_path.rstrip('/')}/{m.name} ({r.kind})"
    if isinstance(m, GroupMembershipMetadata):
        return f"{m.group_path} ⊂ {m.member_ref}"
    if isinstance(m, RoleBindingMetadata):
        arrow = "-/->" if m.decision_type == "deny" else "→"
        return f"{m.principal_ref} {arrow} {m.role} @ {m.scope_ou_path}"
    if isinstance(m, AgentSkillAttachmentMetadata):
        return f"{m.agent_path} ↔ skill:{m.skill_path}"
    if isinstance(m, AgentMcpAttachmentMetadata):
        return f"{m.agent_path} ↔ mcp:{m.mcp_registration_path}"
    if isinstance(m, CredentialMetadata):
        return f"cred: {m.agent_path} ↔ {m.mcp_registration_path}"
    if isinstance(m, SkillGrantMetadata):
        return f"grant: {m.skill_path} → {m.principal_ref}"
    return f"{r.kind}/{getattr(m, 'name', '?')}"
