"""Tests for `weave apply` Workflow kind support.

Coverage:
  * Parser accepts a v1 Workflow manifest (smoke — proves KINDS wiring +
    Pydantic spec model validate against the JSON schema).
  * Apply-order: Workflows sort after Agents (workflows reference agents
    by name; agents must land first).
  * Apply (create) calls POST /workflows with the expected wire shape:
    {name, ou_id, definition: {display_name, description, status,
    nodes, edges}}.
  * Read narrows by both name AND ou_id — two workflows with the same
    name in different OUs don't collide.
  * Delete raises NotImplementedError (no DELETE /workflows in v031).
  * Edge `from` / `to` round-trip through the alias correctly.

All tests use a MagicMock PowerloomClient — no live API calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.applier import apply_plan, sort_for_apply
from loomcli.manifest.handlers import get_handler
from loomcli.manifest.parser import parse_manifest_text
from loomcli.manifest.planner import plan_resources
from loomcli.manifest.schema import (
    OUPathScopedMetadata,
    WorkflowSpec,
)


_WORKFLOW_YAML = """
apiVersion: powerloom/v1
kind: Workflow
metadata:
  name: support-triage
  ou_path: /dev-org/engineering
spec:
  display_name: Support triage pipeline
  description: Routes incoming support tickets to the right agent.
  status: draft
  nodes:
    - id: start
      kind: trigger
      trigger_kind: webhook
      webhook_path: /hooks/support
    - id: classify
      kind: agent
      agent_ref: triage-bot
    - id: emit
      kind: output
      output_kind: webhook
  edges:
    - from: start
      to: classify
    - from: classify
      to: emit
"""


def _resolver_with_ou_tree(client: MagicMock) -> AddressResolver:
    """Build a resolver pre-warmed with a `/dev-org/engineering` OU."""
    # Configure get() so the AddressResolver's /ous/tree call returns
    # a sensible tree, while subsequent /workflows calls return whatever
    # the test sets via side_effect.
    resolver = AddressResolver(client)
    resolver._ou_path_to_id = {
        "/dev-org": "ou-org-id",
        "/dev-org/engineering": "ou-eng-id",
    }
    resolver._ou_id_to_path = {
        "ou-org-id": "/dev-org",
        "ou-eng-id": "/dev-org/engineering",
    }
    return resolver


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def test_parser_accepts_workflow_manifest():
    """KINDS wiring + Pydantic spec model accept the canonical v1
    Workflow shape. Pre-fix: the parser raised "kind 'Workflow' is
    schema-valid but not yet implemented in the CLI"."""
    resources = parse_manifest_text(_WORKFLOW_YAML)
    assert len(resources) == 1
    r = resources[0]
    assert r.kind == "Workflow"
    assert isinstance(r.metadata, OUPathScopedMetadata)
    assert r.metadata.name == "support-triage"
    assert r.metadata.ou_path == "/dev-org/engineering"
    assert isinstance(r.spec, WorkflowSpec)
    assert r.spec.display_name == "Support triage pipeline"
    assert len(r.spec.nodes) == 3
    assert len(r.spec.edges) == 2


def test_workflow_edge_from_alias_roundtrips():
    """The Pydantic `from_node` field uses `from` as its YAML alias.
    Make sure it survives parse + dump."""
    resources = parse_manifest_text(_WORKFLOW_YAML)
    r = resources[0]
    assert isinstance(r.spec, WorkflowSpec)
    edge0 = r.spec.edges[0]
    assert edge0.from_node == "start"
    assert edge0.to == "classify"
    # Wire-shape dump must use the aliased key, not `from_node`.
    dumped = edge0.model_dump(by_alias=True, exclude_none=True)
    assert dumped == {"from": "start", "to": "classify"}


# ---------------------------------------------------------------------------
# Apply order
# ---------------------------------------------------------------------------
def test_workflow_sorts_after_agent():
    """Workflows reference agents by name; agents must apply first."""
    manifest = _WORKFLOW_YAML + """
---
apiVersion: powerloom/v1
kind: Agent
metadata: { name: triage-bot, ou_path: /dev-org/engineering }
spec:
  display_name: Triage bot
  model: claude-sonnet-4-6
  system_prompt: "classify tickets"
  owner_principal_ref: user:admin@dev.local
"""
    resources = parse_manifest_text(manifest)
    sorted_r = sort_for_apply(resources)
    kinds = [r.kind for r in sorted_r]
    agent_idx = kinds.index("Agent")
    workflow_idx = kinds.index("Workflow")
    assert agent_idx < workflow_idx, (
        f"Agent must sort before Workflow in apply order; got {kinds}"
    )


# ---------------------------------------------------------------------------
# Create — wire-shape
# ---------------------------------------------------------------------------
def test_apply_create_posts_to_workflows_with_full_definition():
    """First-time apply: read returns no row, planner emits CREATE,
    handler POSTs to /workflows with body matching WorkflowApplyIn."""
    client = MagicMock()
    # /workflows?name=... → empty list (workflow doesn't exist yet).
    # The route returns {"workflows": [...], "total": N}; the read code
    # handles both wrapped and flat shapes.
    client.get = MagicMock(return_value={"workflows": [], "total": 0})
    client.post = MagicMock(
        return_value={
            "definition": {
                "id": "wf-id-1",
                "name": "support-triage",
                "ou_id": "ou-eng-id",
                "version": 1,
            },
            "created_new": True,
        }
    )

    resolver = _resolver_with_ou_tree(client)
    resources = parse_manifest_text(_WORKFLOW_YAML)
    plan = plan_resources(resources, resolver)
    assert len(plan.actions) == 1
    assert plan.actions[0].verb == "create"

    outcomes = apply_plan(plan, resolver, client)
    assert len(outcomes) == 1
    assert outcomes[0].status == "ok", outcomes[0].error

    client.post.assert_called_once()
    args, _ = client.post.call_args
    assert args[0] == "/workflows"
    body = args[1]
    assert body["name"] == "support-triage"
    assert body["ou_id"] == "ou-eng-id"
    defn = body["definition"]
    assert defn["display_name"] == "Support triage pipeline"
    assert defn["description"] == "Routes incoming support tickets to the right agent."
    assert defn["status"] == "draft"
    assert len(defn["nodes"]) == 3
    # Edge dump uses the aliased `from` key, not `from_node`.
    assert defn["edges"][0] == {"from": "start", "to": "classify"}
    assert defn["edges"][1] == {"from": "classify", "to": "emit"}


# ---------------------------------------------------------------------------
# Read — name+ou_id narrowing
# ---------------------------------------------------------------------------
def test_read_narrows_by_ou_id_not_just_name():
    """If two workflows in different OUs share a name, the handler
    must pick the one in the manifest's OU."""
    client = MagicMock()
    # Server returns both workflows; read should pick the eng one.
    client.get = MagicMock(
        return_value={
            "workflows": [
                {
                    "id": "wf-other-ou",
                    "name": "support-triage",
                    "ou_id": "ou-other-id",
                    "definition_json": {"display_name": "wrong"},
                },
                {
                    "id": "wf-eng",
                    "name": "support-triage",
                    "ou_id": "ou-eng-id",
                    "definition_json": {"display_name": "Support triage pipeline"},
                },
            ],
            "total": 2,
        }
    )
    resolver = _resolver_with_ou_tree(client)
    resources = parse_manifest_text(_WORKFLOW_YAML)
    handler = get_handler("Workflow")
    assert handler is not None
    row = handler.read(resources[0], resolver)
    assert row is not None
    assert row["id"] == "wf-eng"


# ---------------------------------------------------------------------------
# Update is upsert via POST (no PATCH)
# ---------------------------------------------------------------------------
def test_apply_update_posts_to_workflows_again():
    """When the server already has a stale definition, planner emits
    UPDATE and handler POSTs the new definition (server bumps version)."""
    client = MagicMock()
    client.get = MagicMock(
        return_value={
            "workflows": [
                {
                    "id": "wf-eng",
                    "name": "support-triage",
                    "ou_id": "ou-eng-id",
                    "definition_json": {
                        "display_name": "Stale title",  # ← differs from manifest
                        "description": "old",
                        "status": "draft",
                        "nodes": [],
                        "edges": [],
                    },
                }
            ],
            "total": 1,
        }
    )
    client.post = MagicMock(
        return_value={
            "definition": {
                "id": "wf-eng",
                "name": "support-triage",
                "ou_id": "ou-eng-id",
                "version": 2,
            },
            "created_new": False,
        }
    )

    resolver = _resolver_with_ou_tree(client)
    resources = parse_manifest_text(_WORKFLOW_YAML)
    plan = plan_resources(resources, resolver)
    assert len(plan.actions) == 1
    assert plan.actions[0].verb == "update"

    outcomes = apply_plan(plan, resolver, client)
    assert outcomes[0].status == "ok", outcomes[0].error
    client.post.assert_called_once()
    args, _ = client.post.call_args
    assert args[0] == "/workflows"


# ---------------------------------------------------------------------------
# Delete — no endpoint exists
# ---------------------------------------------------------------------------
def test_delete_raises_not_implemented():
    """v031 has no DELETE /workflows. Same posture as MCPDeployment."""
    handler = get_handler("Workflow")
    assert handler is not None
    resources = parse_manifest_text(_WORKFLOW_YAML)
    with pytest.raises(NotImplementedError):
        handler.delete(
            resources[0],
            current={"id": "wf-eng"},
            resolver=MagicMock(),
            client=MagicMock(),
        )
