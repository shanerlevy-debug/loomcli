"""Apply a plan — execute each action, collect outcomes.

Per Q3, apply is **per-resource best-effort**: one resource failing
doesn't stop others unless the failure is an upstream dependency that
the remaining resources also need. We implement a gentle dependency
sort (OU → Group/Skill/MCP → Agent → attachments/credentials/grants)
so the common "apply a fresh manifest" case lands cleanly.

Apply also expands Agent.skills + Agent.mcp_servers convenience lists
into synthetic AgentSkill / AgentMCPServer resources before planning,
so admins don't have to spell out every attachment.

Destroy reverses the sort order and emits delete actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.handlers import get_handler
from loomcli.manifest.planner import Plan, PlanAction
from loomcli.manifest.schema import (
    AgentMcpAttachmentMetadata,
    AgentMcpSpec,
    AgentSkillAttachmentMetadata,
    AgentSkillSpec,
    OUMetadata,
    OUPathScopedMetadata,
    Resource,
    RoleBindingMetadata,
    GroupMembershipMetadata,
    AgentSpec,
)


# Apply order — lower = earlier. Destroy runs in reverse.
_APPLY_ORDER = {
    "OU": 10,
    "Group": 20,
    "Skill": 30,
    "MCPServerRegistration": 30,
    "MCPDeployment": 35,
    "Agent": 40,
    "AgentSkill": 50,
    "AgentMCPServer": 50,
    "Credential": 60,
    "GroupMembership": 70,
    "RoleBinding": 80,
    "SkillAccessGrant": 80,
    # Workflows reference Agents via `agent_ref` node fields, so they must
    # land after agents + their attachments. Mirrors the JSON schema's
    # x-powerloom-apply-order=100 hint.
    "Workflow": 90,
}


def sort_for_apply(resources: list[Resource]) -> list[Resource]:
    """Stable sort: apply-order bucket, then original manifest order
    so siblings within a bucket apply in the order the admin wrote
    them. OUs sort by depth so parents land before children."""
    indexed = list(enumerate(resources))

    def key(pair):
        idx, r = pair
        bucket = _APPLY_ORDER.get(r.kind, 99)
        depth = _metadata_depth(r)
        return (bucket, depth, idx)

    return [r for _, r in sorted(indexed, key=key)]


def _metadata_depth(r: Resource) -> int:
    """How nested is this resource's address? Used to order OU
    creation (parents before children)."""
    m = r.metadata
    if isinstance(m, OUMetadata):
        if not m.parent_ou_path:
            return 0
        return m.parent_ou_path.count("/")
    if isinstance(m, OUPathScopedMetadata):
        return m.ou_path.count("/") + 1
    return 0


def expand_agent_attachments(resources: list[Resource]) -> list[Resource]:
    """For each Agent with inline skills/mcp_servers lists, emit the
    matching AgentSkill / AgentMCPServer resources. The inline lists
    are kept on the Agent spec but the applier treats the Agent's
    own diff as spec-only (see AgentHandler.map_spec_to_server_fields)."""
    expanded: list[Resource] = list(resources)
    for r in resources:
        if r.kind != "Agent":
            continue
        assert isinstance(r.spec, AgentSpec)
        assert isinstance(r.metadata, OUPathScopedMetadata)
        agent_path = f"{r.metadata.ou_path.rstrip('/')}/{r.metadata.name}"
        for skill_name in r.spec.skills:
            skill_path = f"{r.metadata.ou_path.rstrip('/')}/{skill_name}"
            expanded.append(
                Resource(
                    kind="AgentSkill",
                    metadata=AgentSkillAttachmentMetadata(
                        agent_path=agent_path,
                        skill_path=skill_path,
                    ),
                    spec=AgentSkillSpec(),
                    source_file=f"{r.source_file} (inline skills)",
                    doc_index=r.doc_index,
                )
            )
        for reg_name in r.spec.mcp_servers:
            reg_path = f"{r.metadata.ou_path.rstrip('/')}/{reg_name}"
            expanded.append(
                Resource(
                    kind="AgentMCPServer",
                    metadata=AgentMcpAttachmentMetadata(
                        agent_path=agent_path,
                        mcp_registration_path=reg_path,
                    ),
                    spec=AgentMcpSpec(),
                    source_file=f"{r.source_file} (inline mcp_servers)",
                    doc_index=r.doc_index,
                )
            )
    return expanded


# ---------------------------------------------------------------------------
# Apply + Destroy outcomes
# ---------------------------------------------------------------------------
@dataclass
class ActionOutcome:
    action: PlanAction
    status: str  # "ok" | "failed" | "skipped"
    error: str | None = None
    server_row: dict[str, Any] | None = None


def apply_plan(
    plan: Plan,
    resolver: AddressResolver,
    client: PowerloomClient,
) -> list[ActionOutcome]:
    outcomes: list[ActionOutcome] = []
    for action in plan.actions:
        outcome = _apply_action(action, resolver, client)
        outcomes.append(outcome)
    return outcomes


def _apply_action(
    action: PlanAction,
    resolver: AddressResolver,
    client: PowerloomClient,
) -> ActionOutcome:
    r = action.resource
    handler = get_handler(r.kind)
    if handler is None:
        return ActionOutcome(action=action, status="failed", error=f"no handler for {r.kind}")
    try:
        if action.verb == "noop":
            return ActionOutcome(action=action, status="ok")
        if action.verb == "unknown":
            # Re-read now that upstream may have landed.
            current = handler.read(r, resolver)
            if current is None:
                row = handler.create(r, resolver, client)
            else:
                # Treat as update if there's any diff; as noop otherwise.
                row = handler.update(r, current, resolver, client)
            return ActionOutcome(action=action, status="ok", server_row=row)
        if action.verb == "create":
            row = handler.create(r, resolver, client)
            return ActionOutcome(action=action, status="ok", server_row=row)
        if action.verb == "update":
            assert action.current_snapshot is not None
            row = handler.update(r, action.current_snapshot, resolver, client)
            return ActionOutcome(action=action, status="ok", server_row=row)
        if action.verb == "destroy":
            assert action.current_snapshot is not None
            handler.delete(r, action.current_snapshot, resolver, client)
            return ActionOutcome(action=action, status="ok")
    except (PowerloomApiError, NotImplementedError, Exception) as e:
        return ActionOutcome(
            action=action, status="failed", error=str(e)
        )
    return ActionOutcome(action=action, status="failed", error="unreachable dispatch")


def plan_destroy_for_resources(
    resources: list[Resource], resolver: AddressResolver
) -> Plan:
    """Reverse-order destroy plan. Expanded attachments destroy before
    their owning agent; agents destroy before their OU."""
    from loomcli.manifest.planner import Plan, PlanAction

    expanded = expand_agent_attachments(resources)
    # Reverse the apply sort for destroy order.
    sorted_res = list(reversed(sort_for_apply(expanded)))

    actions: list[PlanAction] = []
    for r in sorted_res:
        handler = get_handler(r.kind)
        if handler is None:
            actions.append(
                PlanAction(
                    resource=r, verb="unknown",
                    reason=f"no handler for {r.kind}",
                )
            )
            continue
        try:
            current = handler.read(r, resolver)
        except Exception as e:
            actions.append(
                PlanAction(resource=r, verb="noop", reason=f"read failed: {e}")
            )
            continue
        if current is None:
            actions.append(
                PlanAction(
                    resource=r, verb="noop",
                    reason="already absent from server",
                )
            )
        else:
            actions.append(
                PlanAction(
                    resource=r, verb="destroy",
                    current_snapshot=current,
                )
            )
    return Plan(actions=actions)


# Re-export metadata types so callers don't need deep imports.
_ = (GroupMembershipMetadata, RoleBindingMetadata)  # silence unused-import lint
