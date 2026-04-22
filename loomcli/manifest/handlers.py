"""Per-kind handlers — one class per resource kind.

Each handler knows three things:
  - how to READ the current server state for a manifest resource
    (returns a dict or None)
  - how to map a manifest spec to server-shaped fields for diffing
  - how to CREATE / UPDATE / DELETE via the existing REST API

Keeping them all in one file (rather than a sub-package) trades
file length for discoverability — one grep finds every kind's
behavior. If a kind outgrows its section (>150 lines), split it out.

The applier walks plan actions and dispatches via `get_handler(kind)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.manifest.addressing import (
    AddressResolutionError,
    AddressResolver,
)
from loomcli.manifest.schema import (
    AgentMcpAttachmentMetadata,
    AgentSkillAttachmentMetadata,
    CredentialMetadata,
    GroupMembershipMetadata,
    McpDeploymentSpec,
    OUMetadata,
    OUPathScopedMetadata,
    Resource,
    RoleBindingMetadata,
    SkillGrantMetadata,
)


# ---------------------------------------------------------------------------
# Protocol + registry
# ---------------------------------------------------------------------------
class Handler(Protocol):
    kind: str

    def read(
        self, r: Resource, resolver: AddressResolver
    ) -> dict[str, Any] | None: ...

    def map_spec_to_server_fields(
        self, spec: Any, *, current: dict[str, Any] | None
    ) -> dict[str, Any]: ...

    def create(
        self, r: Resource, resolver: AddressResolver, client: PowerloomClient
    ) -> dict[str, Any]: ...

    def update(
        self,
        r: Resource,
        current: dict[str, Any],
        resolver: AddressResolver,
        client: PowerloomClient,
    ) -> dict[str, Any]: ...

    def delete(
        self,
        r: Resource,
        current: dict[str, Any],
        resolver: AddressResolver,
        client: PowerloomClient,
    ) -> None: ...


_HANDLERS: dict[str, Handler] = {}


def register(handler: Handler) -> Handler:
    _HANDLERS[handler.kind] = handler
    return handler


def get_handler(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


# ---------------------------------------------------------------------------
# Principal reference resolution
# ---------------------------------------------------------------------------
# principal_ref: "user:<email>" | "group:<group_path>" | "ou:<ou_path>"
def _resolve_principal_ref(ref: str, resolver: AddressResolver) -> str:
    """Return the principal's UUID."""
    if ":" not in ref:
        raise AddressResolutionError(
            f"invalid principal_ref {ref!r}; expected "
            "'user:<email>', 'group:<group_path>', or 'ou:<ou_path>'"
        )
    kind, value = ref.split(":", 1)
    if kind == "user":
        users = resolver._client.get("/users")  # type: ignore[attr-defined]
        for u in users:
            if u.get("email") == value:
                return u["principal_id"]
        raise AddressResolutionError(f"user not found: {value}")
    if kind == "group":
        # Group path: <ou_path>/<group_name>
        parent, _, name = value.rstrip("/").rpartition("/")
        ou_id = resolver.ou_path_to_id(parent) if parent else resolver.ou_path_to_id("/")
        groups = resolver._client.get("/groups", ou_id=ou_id)  # type: ignore[attr-defined]
        for g in groups:
            if g.get("name") == name:
                return g["principal_id"]
        raise AddressResolutionError(f"group not found at {value}")
    if kind == "ou":
        ou_id = resolver.ou_path_to_id(value)
        # OU has its own principal_id field — fetch to get it.
        ou = resolver._client.get(f"/ous/{ou_id}")  # type: ignore[attr-defined]
        return ou["principal_id"]
    raise AddressResolutionError(f"unknown principal_ref kind: {kind}")


# ---------------------------------------------------------------------------
# Helpers for OU-scoped resource address parsing
# ---------------------------------------------------------------------------
def _split_path(full_path: str) -> tuple[str, str]:
    """Split `/dev-org/engineering/code-reviewer` → (`/dev-org/engineering`, `code-reviewer`)."""
    parent, _, name = full_path.rstrip("/").rpartition("/")
    if not parent or not name:
        raise AddressResolutionError(f"invalid resource path {full_path!r}")
    return parent, name


# ===========================================================================
# OU
# ===========================================================================
@dataclass
class OUHandler:
    kind: str = "OU"

    def read(
        self, r: Resource, resolver: AddressResolver
    ) -> dict[str, Any] | None:
        assert isinstance(r.metadata, OUMetadata)
        full_path = (
            f"{(r.metadata.parent_ou_path or '').rstrip('/')}/{r.metadata.name}"
        )
        ou_id = resolver.try_ou_path_to_id(full_path)
        if ou_id is None:
            return None
        return resolver._client.get(f"/ous/{ou_id}")  # type: ignore[attr-defined]

    def map_spec_to_server_fields(
        self, spec: Any, *, current: dict[str, Any] | None
    ) -> dict[str, Any]:
        return {"display_name": spec.display_name}

    def create(
        self, r: Resource, resolver: AddressResolver, client: PowerloomClient
    ) -> dict[str, Any]:
        assert isinstance(r.metadata, OUMetadata)
        parent_path = r.metadata.parent_ou_path
        parent_id = resolver.ou_path_to_id(parent_path) if parent_path else None
        body = {
            "name": r.metadata.name,
            "display_name": r.spec.display_name,
            "parent_id": parent_id,
        }
        out = client.post("/ous", body)
        # New OU invalidates the tree cache.
        resolver._ou_path_to_id = None  # type: ignore[attr-defined]
        resolver._ou_id_to_path = None  # type: ignore[attr-defined]
        return out

    def update(
        self, r: Resource, current: dict[str, Any], resolver, client
    ) -> dict[str, Any]:
        return client.patch(
            f"/ous/{current['id']}",
            {"display_name": r.spec.display_name},
        )

    def delete(self, r: Resource, current, resolver, client) -> None:
        client.delete(f"/ous/{current['id']}")


register(OUHandler())


# ===========================================================================
# Group
# ===========================================================================
@dataclass
class GroupHandler:
    kind: str = "Group"

    def read(self, r: Resource, resolver: AddressResolver) -> dict[str, Any] | None:
        assert isinstance(r.metadata, OUPathScopedMetadata)
        ou_id = resolver.try_ou_path_to_id(r.metadata.ou_path)
        if ou_id is None:
            raise AddressResolutionError(f"OU {r.metadata.ou_path} not yet created")
        return resolver.find_in_ou(
            list_path="/groups", ou_id=ou_id, name=r.metadata.name
        )

    def map_spec_to_server_fields(self, spec, *, current):
        return {"display_name": spec.display_name, "description": spec.description}

    def create(self, r, resolver, client):
        ou_id = resolver.ou_path_to_id(r.metadata.ou_path)
        out = client.post(
            "/groups",
            {
                "name": r.metadata.name,
                "display_name": r.spec.display_name,
                "description": r.spec.description,
                "ou_id": ou_id,
            },
        )
        resolver.invalidate_cache_for("/groups")
        return out

    def update(self, r, current, resolver, client):
        return client.patch(
            f"/groups/{current['id']}",
            {
                "display_name": r.spec.display_name,
                "description": r.spec.description,
            },
        )

    def delete(self, r, current, resolver, client) -> None:
        client.delete(f"/groups/{current['id']}")
        resolver.invalidate_cache_for("/groups")


register(GroupHandler())


# ===========================================================================
# GroupMembership — attachment-style; create/delete only, no update
# ===========================================================================
@dataclass
class GroupMembershipHandler:
    kind: str = "GroupMembership"

    def read(self, r: Resource, resolver):
        assert isinstance(r.metadata, GroupMembershipMetadata)
        # /groups/{id} returns GroupWithMembers including members[] ids.
        group = self._group_row(r.metadata.group_path, resolver)
        if group is None:
            return None
        detail = resolver._client.get(f"/groups/{group['id']}")  # type: ignore[attr-defined]
        member_principal_id = _resolve_principal_ref(r.metadata.member_ref, resolver)
        for m in detail.get("members", []):
            if m.get("principal_id") == member_principal_id:
                # Return a sentinel dict so the planner treats it as NOOP.
                return {"group_id": group["id"], "principal_id": member_principal_id}
        return None

    def map_spec_to_server_fields(self, spec, *, current):
        return {}  # identity is fully in metadata; no spec fields

    def create(self, r, resolver, client):
        group = self._require_group(r.metadata.group_path, resolver)
        kind, value = r.metadata.member_ref.split(":", 1)
        if kind == "user":
            users = client.get("/users")
            user = next((u for u in users if u.get("email") == value), None)
            if user is None:
                raise AddressResolutionError(f"user not found: {value}")
            client.post(f"/groups/{group['id']}/users", {"user_id": user["id"]})
        elif kind == "group":
            member = self._require_group(value, resolver)
            client.post(
                f"/groups/{group['id']}/groups",
                {"member_group_id": member["id"]},
            )
        else:
            raise AddressResolutionError(
                "GroupMembership only supports user: and group: members"
            )
        return {"group_id": group["id"]}

    def update(self, r, current, resolver, client):
        return current  # identity-only; nothing to update

    def delete(self, r, current, resolver, client) -> None:
        group = self._require_group(r.metadata.group_path, resolver)
        kind, value = r.metadata.member_ref.split(":", 1)
        if kind == "user":
            users = client.get("/users")
            user = next((u for u in users if u.get("email") == value), None)
            if user is not None:
                client.delete(f"/groups/{group['id']}/users/{user['id']}")
        elif kind == "group":
            member = self._require_group(value, resolver)
            client.delete(
                f"/groups/{group['id']}/groups/{member['id']}"
            )

    def _group_row(self, path: str, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(list_path="/groups", ou_id=ou_id, name=name)

    def _require_group(self, path: str, resolver):
        row = self._group_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"group not found at {path}")
        return row


register(GroupMembershipHandler())


# ===========================================================================
# RoleBinding — create/delete only; no update (change = delete + create)
# ===========================================================================
@dataclass
class RoleBindingHandler:
    kind: str = "RoleBinding"

    def read(self, r: Resource, resolver):
        assert isinstance(r.metadata, RoleBindingMetadata)
        scope_id = resolver.try_ou_path_to_id(r.metadata.scope_ou_path)
        if scope_id is None:
            raise AddressResolutionError(
                f"scope OU {r.metadata.scope_ou_path} not yet created"
            )
        principal_id = _resolve_principal_ref(r.metadata.principal_ref, resolver)
        # Roles are named (AgentViewer, OrgAdmin, etc.); convert to id.
        role_id = _resolve_role(r.metadata.role, resolver)
        rows = resolver._client.get(  # type: ignore[attr-defined]
            "/role-bindings",
            principal_id=principal_id,
            scope_ou_id=scope_id,
        )
        for b in rows:
            if (
                b.get("role_id") == role_id
                and b.get("decision_type") == r.metadata.decision_type
                and b.get("scope_ou_id") == scope_id
            ):
                return b
        return None

    def map_spec_to_server_fields(self, spec, *, current):
        return {}  # all identity in metadata

    def create(self, r, resolver, client):
        scope_id = resolver.ou_path_to_id(r.metadata.scope_ou_path)
        principal_id = _resolve_principal_ref(r.metadata.principal_ref, resolver)
        role_id = _resolve_role(r.metadata.role, resolver)
        return client.post(
            "/role-bindings",
            {
                "principal_id": principal_id,
                "role_id": role_id,
                "scope_ou_id": scope_id,
                "decision_type": r.metadata.decision_type,
            },
        )

    def update(self, r, current, resolver, client):
        return current  # treat as immutable; planner emits NOOP

    def delete(self, r, current, resolver, client) -> None:
        client.delete(f"/role-bindings/{current['id']}")


def _resolve_role(role_name: str, resolver) -> str:
    roles = resolver._client.get("/roles")  # type: ignore[attr-defined]
    for role in roles:
        if role.get("name") == role_name:
            return role["id"]
    raise AddressResolutionError(f"role not found: {role_name}")


register(RoleBindingHandler())


# ===========================================================================
# Skill
# ===========================================================================
@dataclass
class SkillHandler:
    kind: str = "Skill"

    def read(self, r, resolver):
        ou_id = resolver.try_ou_path_to_id(r.metadata.ou_path)
        if ou_id is None:
            raise AddressResolutionError(f"OU {r.metadata.ou_path} not yet created")
        return resolver.find_in_ou(
            list_path="/skills", ou_id=ou_id, name=r.metadata.name
        )

    def map_spec_to_server_fields(self, spec, *, current):
        fields = {"description": spec.description}
        # current_version_id is manifest-assigned but server-computed
        # when versions are uploaded via REST. Only diff if the
        # manifest explicitly sets it.
        if spec.current_version_id is not None:
            fields["current_version_id"] = spec.current_version_id
        return fields

    def create(self, r, resolver, client):
        ou_id = resolver.ou_path_to_id(r.metadata.ou_path)
        body: dict = {
            "name": r.metadata.name,
            "display_name": r.spec.display_name,
            "description": r.spec.description,
            "ou_id": ou_id,
            "skill_type": r.spec.skill_type,
        }
        if r.spec.tool_schema is not None:
            body["tool_schema"] = r.spec.tool_schema
        out = client.post("/skills", body)
        resolver.invalidate_cache_for("/skills")
        return out

    def update(self, r, current, resolver, client):
        # Only description is updatable via PATCH — display_name is
        # write-once per the v005 service. Skip if unchanged.
        return client.patch(
            f"/skills/{current['id']}",
            {"description": r.spec.description},
        )

    def delete(self, r, current, resolver, client) -> None:
        client.delete(f"/skills/{current['id']}")
        resolver.invalidate_cache_for("/skills")


register(SkillHandler())


# ===========================================================================
# MCPServerRegistration (manually registered — BYO)
# ===========================================================================
@dataclass
class McpServerRegistrationHandler:
    kind: str = "MCPServerRegistration"

    def read(self, r, resolver):
        ou_id = resolver.try_ou_path_to_id(r.metadata.ou_path)
        if ou_id is None:
            raise AddressResolutionError(f"OU {r.metadata.ou_path} not yet created")
        return resolver.find_in_ou(
            list_path="/mcp-servers", ou_id=ou_id, name=r.metadata.name
        )

    def map_spec_to_server_fields(self, spec, *, current):
        return {
            "display_name": spec.display_name,
            "url": spec.url,
            "description": spec.description,
        }

    def create(self, r, resolver, client):
        ou_id = resolver.ou_path_to_id(r.metadata.ou_path)
        out = client.post(
            "/mcp-servers?skip_validation=true",
            {
                "name": r.metadata.name,
                "display_name": r.spec.display_name,
                "url": r.spec.url,
                "description": r.spec.description,
                "ou_id": ou_id,
            },
        )
        resolver.invalidate_cache_for("/mcp-servers")
        return out

    def update(self, r, current, resolver, client):
        return client.patch(
            f"/mcp-servers/{current['id']}",
            {
                "display_name": r.spec.display_name,
                "description": r.spec.description,
            },
        )

    def delete(self, r, current, resolver, client) -> None:
        client.delete(f"/mcp-servers/{current['id']}")
        resolver.invalidate_cache_for("/mcp-servers")


register(McpServerRegistrationHandler())


# ===========================================================================
# MCPDeployment
# ===========================================================================
@dataclass
class McpDeploymentHandler:
    kind: str = "MCPDeployment"

    def read(self, r, resolver):
        ou_id = resolver.try_ou_path_to_id(r.metadata.ou_path)
        if ou_id is None:
            raise AddressResolutionError(f"OU {r.metadata.ou_path} not yet created")
        return resolver.find_in_ou(
            list_path="/mcp-deployments", ou_id=ou_id, name=r.metadata.name
        )

    def map_spec_to_server_fields(self, spec: McpDeploymentSpec, *, current):
        # MCPDeployment is create-only at the spec level: config/policy
        # updates require a destroy + re-apply in v008 (no PATCH endpoint).
        # We surface diffs for readability but update() raises.
        return {
            "template_kind": spec.template_kind,
            "config_json": spec.config,
            "policy_json": spec.policy,
        }

    def create(self, r, resolver, client):
        ou_id = resolver.ou_path_to_id(r.metadata.ou_path)
        out = client.post(
            "/mcp-deployments",
            {
                "name": r.metadata.name,
                "display_name": r.spec.display_name,
                "ou_id": ou_id,
                "template_kind": r.spec.template_kind,
                "isolation_mode": r.spec.isolation_mode,
                "config_json": r.spec.config,
                "policy_json": r.spec.policy,
            },
        )
        resolver.invalidate_cache_for("/mcp-deployments")
        return out

    def update(self, r, current, resolver, client):
        raise NotImplementedError(
            "MCPDeployment config/policy changes aren't patchable in v008. "
            "Destroy the deployment and re-apply to change it."
        )

    def delete(self, r, current, resolver, client) -> None:
        client.post(f"/mcp-deployments/{current['id']}/destroy")
        resolver.invalidate_cache_for("/mcp-deployments")


register(McpDeploymentHandler())


# ===========================================================================
# Agent
# ===========================================================================
@dataclass
class AgentHandler:
    kind: str = "Agent"

    def read(self, r, resolver):
        ou_id = resolver.try_ou_path_to_id(r.metadata.ou_path)
        if ou_id is None:
            raise AddressResolutionError(f"OU {r.metadata.ou_path} not yet created")
        return resolver.find_in_ou(
            list_path="/agents", ou_id=ou_id, name=r.metadata.name
        )

    def map_spec_to_server_fields(self, spec, *, current):
        # Convenience `skills:` / `mcp_servers:` lists on AgentSpec are
        # NOT diffed here — they're treated as AgentSkill / AgentMCPServer
        # resources by the applier (separate plan actions). Keeping diff
        # scope to the agent row itself keeps per-resource actions clean.
        return {
            "display_name": spec.display_name,
            "description": spec.description,
            "model": spec.model,
            "system_prompt": spec.system_prompt,
        }

    def create(self, r, resolver, client):
        ou_id = resolver.ou_path_to_id(r.metadata.ou_path)
        owner_principal_id = _resolve_principal_ref(
            r.spec.owner_principal_ref, resolver
        )
        out = client.post(
            "/agents",
            {
                "name": r.metadata.name,
                "display_name": r.spec.display_name,
                "description": r.spec.description,
                "ou_id": ou_id,
                "model": r.spec.model,
                "system_prompt": r.spec.system_prompt,
                "runtime_type": r.spec.runtime_type,
                "agent_kind": r.spec.agent_kind,
                "owner_principal_id": owner_principal_id,
            },
        )
        resolver.invalidate_cache_for("/agents")
        return out

    def update(self, r, current, resolver, client):
        return client.patch(
            f"/agents/{current['id']}",
            {
                "display_name": r.spec.display_name,
                "description": r.spec.description,
                "model": r.spec.model,
                "system_prompt": r.spec.system_prompt,
            },
        )

    def delete(self, r, current, resolver, client) -> None:
        client.delete(f"/agents/{current['id']}")
        resolver.invalidate_cache_for("/agents")


register(AgentHandler())


# ===========================================================================
# AgentSkill / AgentMCPServer attachments — identity-only, create/delete
# ===========================================================================
@dataclass
class AgentSkillHandler:
    kind: str = "AgentSkill"

    def read(self, r, resolver):
        assert isinstance(r.metadata, AgentSkillAttachmentMetadata)
        agent = self._agent_row(r.metadata.agent_path, resolver)
        if agent is None:
            return None
        skill = self._skill_row(r.metadata.skill_path, resolver)
        if skill is None:
            return None
        attachments = resolver._client.get(  # type: ignore[attr-defined]
            f"/agents/{agent['id']}/skills"
        )
        for a in attachments:
            if a.get("skill_id") == skill["id"]:
                return a
        return None

    def map_spec_to_server_fields(self, spec, *, current):
        fields: dict[str, Any] = {}
        if spec.skill_version_id is not None:
            fields["skill_version_id"] = spec.skill_version_id
        return fields

    def create(self, r, resolver, client):
        agent = self._require_agent(r.metadata.agent_path, resolver)
        skill = self._require_skill(r.metadata.skill_path, resolver)
        return client.post(
            f"/agents/{agent['id']}/skills",
            {
                "skill_id": skill["id"],
                "skill_version_id": r.spec.skill_version_id,
            },
        )

    def update(self, r, current, resolver, client):
        # Re-attach = same POST path (idempotent, bumps version).
        return self.create(r, resolver, client)

    def delete(self, r, current, resolver, client) -> None:
        agent = self._require_agent(r.metadata.agent_path, resolver)
        skill = self._require_skill(r.metadata.skill_path, resolver)
        client.delete(f"/agents/{agent['id']}/skills/{skill['id']}")

    def _agent_row(self, path, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(list_path="/agents", ou_id=ou_id, name=name)

    def _skill_row(self, path, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(list_path="/skills", ou_id=ou_id, name=name)

    def _require_agent(self, path, resolver):
        row = self._agent_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"agent not found at {path}")
        return row

    def _require_skill(self, path, resolver):
        row = self._skill_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"skill not found at {path}")
        return row


register(AgentSkillHandler())


@dataclass
class AgentMcpHandler:
    kind: str = "AgentMCPServer"

    def read(self, r, resolver):
        assert isinstance(r.metadata, AgentMcpAttachmentMetadata)
        agent = self._agent_row(r.metadata.agent_path, resolver)
        if agent is None:
            return None
        reg = self._reg_row(r.metadata.mcp_registration_path, resolver)
        if reg is None:
            return None
        attachments = resolver._client.get(  # type: ignore[attr-defined]
            f"/agents/{agent['id']}/mcp-servers"
        )
        for a in attachments:
            if a.get("mcp_server_registration_id") == reg["id"]:
                return a
        return None

    def map_spec_to_server_fields(self, spec, *, current):
        return {}

    def create(self, r, resolver, client):
        agent = self._require_agent(r.metadata.agent_path, resolver)
        reg = self._require_reg(r.metadata.mcp_registration_path, resolver)
        return client.post(
            f"/agents/{agent['id']}/mcp-servers",
            {"mcp_server_registration_id": reg["id"]},
        )

    def update(self, r, current, resolver, client):
        return current

    def delete(self, r, current, resolver, client) -> None:
        agent = self._require_agent(r.metadata.agent_path, resolver)
        reg = self._require_reg(r.metadata.mcp_registration_path, resolver)
        client.delete(f"/agents/{agent['id']}/mcp-servers/{reg['id']}")

    def _agent_row(self, path, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(list_path="/agents", ou_id=ou_id, name=name)

    def _reg_row(self, path, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(
            list_path="/mcp-servers", ou_id=ou_id, name=name
        )

    def _require_agent(self, path, resolver):
        row = self._agent_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"agent not found at {path}")
        return row

    def _require_reg(self, path, resolver):
        row = self._reg_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"mcp registration not found at {path}")
        return row


register(AgentMcpHandler())


# ===========================================================================
# Credential
# ===========================================================================
@dataclass
class CredentialHandler:
    kind: str = "Credential"

    def read(self, r, resolver):
        assert isinstance(r.metadata, CredentialMetadata)
        agent = self._require_agent(r.metadata.agent_path, resolver)
        # /credentials?agent_id=<id> returns whatever credentials exist
        # for that agent. We match on mcp_server_registration_id.
        rows = resolver._client.get(  # type: ignore[attr-defined]
            "/credentials", agent_id=agent["id"]
        )
        reg = self._require_reg(r.metadata.mcp_registration_path, resolver)
        for row in rows:
            if row.get("mcp_server_registration_id") == reg["id"]:
                return row
        return None

    def map_spec_to_server_fields(self, spec, *, current):
        return {}  # no spec — existence-only

    def create(self, r, resolver, client):
        agent = self._require_agent(r.metadata.agent_path, resolver)
        reg = self._require_reg(r.metadata.mcp_registration_path, resolver)
        return client.post(
            "/credentials",
            {
                "agent_id": agent["id"],
                "mcp_server_registration_id": reg["id"],
            },
        )

    def update(self, r, current, resolver, client):
        return current

    def delete(self, r, current, resolver, client) -> None:
        client.delete(f"/credentials/{current['id']}")

    def _agent_row(self, path, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(list_path="/agents", ou_id=ou_id, name=name)

    def _reg_row(self, path, resolver):
        parent, name = _split_path(path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            return None
        return resolver.find_in_ou(
            list_path="/mcp-servers", ou_id=ou_id, name=name
        )

    def _require_agent(self, path, resolver):
        row = self._agent_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"agent not found at {path}")
        return row

    def _require_reg(self, path, resolver):
        row = self._reg_row(path, resolver)
        if row is None:
            raise AddressResolutionError(f"mcp registration not found at {path}")
        return row


register(CredentialHandler())


# ===========================================================================
# SkillAccessGrant
# ===========================================================================
@dataclass
class SkillGrantHandler:
    kind: str = "SkillAccessGrant"

    def read(self, r, resolver):
        assert isinstance(r.metadata, SkillGrantMetadata)
        parent, name = _split_path(r.metadata.skill_path)
        ou_id = resolver.try_ou_path_to_id(parent)
        if ou_id is None:
            raise AddressResolutionError(
                f"OU {parent} not yet created (from skill_path {r.metadata.skill_path})"
            )
        skill = resolver.find_in_ou(list_path="/skills", ou_id=ou_id, name=name)
        if skill is None:
            return None
        principal_id = _resolve_principal_ref(r.metadata.principal_ref, resolver)
        grants = resolver._client.get(f"/skills/{skill['id']}/grants")  # type: ignore[attr-defined]
        for g in grants:
            if g.get("principal_id") == principal_id:
                return g
        return None

    def map_spec_to_server_fields(self, spec, *, current):
        return {}

    def create(self, r, resolver, client):
        parent, name = _split_path(r.metadata.skill_path)
        ou_id = resolver.ou_path_to_id(parent)
        skill = resolver.find_in_ou(list_path="/skills", ou_id=ou_id, name=name)
        if skill is None:
            raise AddressResolutionError(
                f"skill not found at {r.metadata.skill_path}"
            )
        principal_id = _resolve_principal_ref(r.metadata.principal_ref, resolver)
        return client.post(
            f"/skills/{skill['id']}/grants",
            {"principal_id": principal_id},
        )

    def update(self, r, current, resolver, client):
        return current

    def delete(self, r, current, resolver, client) -> None:
        parent, name = _split_path(r.metadata.skill_path)
        ou_id = resolver.ou_path_to_id(parent)
        skill = resolver.find_in_ou(list_path="/skills", ou_id=ou_id, name=name)
        if skill is None:
            return
        client.delete(f"/skills/{skill['id']}/grants/{current['id']}")


register(SkillGrantHandler())
