"""Applier behavior tests — agent-attachment expansion, sort order,
and apply-plan execution against a MagicMock client."""
from __future__ import annotations

from unittest.mock import MagicMock

from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.applier import (
    apply_plan,
    expand_agent_attachments,
    plan_destroy_for_resources,
    sort_for_apply,
)
from loomcli.manifest.parser import parse_manifest_text
from loomcli.manifest.planner import plan_resources


def test_expand_agent_attachments_adds_synthetic_resources():
    manifest = """
apiVersion: powerloom/v1
kind: Agent
metadata: { name: bot, ou_path: /dev-org/engineering }
spec:
  display_name: Bot
  model: claude-sonnet-4-6
  system_prompt: "be good"
  owner_principal_ref: user:admin@dev.local
  skills: [python-lint, shell-safety]
  mcp_servers: [reports-files]
"""
    resources = parse_manifest_text(manifest)
    expanded = expand_agent_attachments(resources)
    # 1 agent + 2 skill attachments + 1 mcp attachment = 4.
    assert len(expanded) == 4
    kinds = sorted(r.kind for r in expanded)
    assert kinds == ["Agent", "AgentMCPServer", "AgentSkill", "AgentSkill"]


def test_sort_for_apply_puts_ou_first():
    manifest = """
apiVersion: powerloom/v1
kind: Agent
metadata: { name: bot, ou_path: /dev-org/engineering }
spec:
  display_name: Bot
  model: claude-sonnet-4-6
  system_prompt: "be good"
  owner_principal_ref: user:admin@dev.local
---
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering, parent_ou_path: /dev-org }
spec: { display_name: Engineering }
"""
    resources = parse_manifest_text(manifest)
    sorted_r = sort_for_apply(resources)
    assert sorted_r[0].kind == "OU"
    assert sorted_r[-1].kind == "Agent"


def test_sort_for_apply_puts_parent_ou_before_child():
    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: backend, parent_ou_path: /dev-org/engineering }
spec: { display_name: Backend }
---
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering, parent_ou_path: /dev-org }
spec: { display_name: Engineering }
"""
    resources = parse_manifest_text(manifest)
    sorted_r = sort_for_apply(resources)
    # Parent (one level deep) should sort before grandchild (two levels deep).
    assert sorted_r[0].metadata.name == "engineering"
    assert sorted_r[1].metadata.name == "backend"


def test_apply_plan_create_calls_post():
    """apply_plan(verb=create) calls handler.create which POSTs."""
    client = MagicMock()
    # /ous/tree for AddressResolver + POST /ous for the create.
    client.get = MagicMock(return_value=[])
    client.post = MagicMock(
        return_value={"id": "new-id", "name": "engineering", "display_name": "E"}
    )
    resolver = AddressResolver(client)
    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering }
spec: { display_name: Engineering }
"""
    resources = parse_manifest_text(manifest)
    plan = plan_resources(resources, resolver)
    outcomes = apply_plan(plan, resolver, client)
    assert len(outcomes) == 1
    assert outcomes[0].status == "ok"
    client.post.assert_called_once()
    args, kwargs = client.post.call_args
    assert args[0] == "/ous"
    body = args[1]
    assert body["name"] == "engineering"


def test_apply_plan_surfaces_handler_errors_per_resource():
    """Per-resource best-effort (Q3): one failing resource doesn't
    stop the others from being applied."""
    client = MagicMock()
    client.get = MagicMock(return_value=[])
    # First POST fails, second succeeds.
    client.post = MagicMock(
        side_effect=[
            Exception("oops"),
            {"id": "y", "name": "b", "display_name": "B"},
        ]
    )
    resolver = AddressResolver(client)
    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: a }
spec: { display_name: A }
---
apiVersion: powerloom/v1
kind: OU
metadata: { name: b }
spec: { display_name: B }
"""
    resources = parse_manifest_text(manifest)
    plan = plan_resources(resources, resolver)
    outcomes = apply_plan(plan, resolver, client)
    assert len(outcomes) == 2
    assert outcomes[0].status == "failed"
    assert "oops" in (outcomes[0].error or "")
    assert outcomes[1].status == "ok"


def test_plan_destroy_reverses_sort_order():
    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering, parent_ou_path: /dev-org }
spec: { display_name: E }
---
apiVersion: powerloom/v1
kind: Agent
metadata: { name: bot, ou_path: /dev-org/engineering }
spec:
  display_name: Bot
  model: claude-sonnet-4-6
  system_prompt: "be good"
  owner_principal_ref: user:admin@dev.local
"""
    client = MagicMock()
    client.get = MagicMock(return_value=[])
    resolver = AddressResolver(client)
    resources = parse_manifest_text(manifest)
    dplan = plan_destroy_for_resources(resources, resolver)
    # Agent should appear BEFORE OU in destroy order.
    kinds = [a.resource.kind for a in dplan.actions]
    agent_idx = kinds.index("Agent")
    ou_idx = kinds.index("OU")
    assert agent_idx < ou_idx
