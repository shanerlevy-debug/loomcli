"""Planner tests — mock the API client and check action verbs + diffs."""
from __future__ import annotations

from unittest.mock import MagicMock

from loomcli.manifest.addressing import AddressResolver
from loomcli.manifest.parser import parse_manifest_text
from loomcli.manifest.planner import plan_resources


def _setup(fake_ou_tree, responses):
    client = MagicMock()

    def _get(path, **kwargs):
        if path == "/ous/tree":
            return fake_ou_tree
        # Match exact paths first, then fall back to prefix-list responses.
        if path in responses:
            return responses[path]
        # list_path with ou_id param — return the list for that path.
        if path.startswith("/"):
            return responses.get(path, [])
        return []

    client.get = MagicMock(side_effect=_get)
    return AddressResolver(client)


def test_plan_new_ou_is_create(fake_ou_tree):
    """OU that doesn't exist on server → action verb == 'create'."""
    resolver = _setup(fake_ou_tree, {})
    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: backend, parent_ou_path: /dev-org/engineering }
spec: { display_name: Backend }
"""
    resources = parse_manifest_text(manifest)
    plan = plan_resources(resources, resolver)
    assert len(plan.actions) == 1
    assert plan.actions[0].verb == "create"


def test_plan_existing_ou_with_same_display_is_noop(fake_ou_tree):
    client = MagicMock()
    client.get = MagicMock(side_effect=lambda path, **kw: {
        "/ous/tree": fake_ou_tree,
        "/ous/00000000-0000-0000-0000-0000000000aa": {
            "id": "00000000-0000-0000-0000-0000000000aa",
            "name": "engineering",
            "display_name": "Engineering",
            "parent_id": "00000000-0000-0000-0000-00000000dddd",
        },
    }.get(path, []))
    resolver = AddressResolver(client)

    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering, parent_ou_path: /dev-org }
spec: { display_name: Engineering }
"""
    resources = parse_manifest_text(manifest)
    plan = plan_resources(resources, resolver)
    assert plan.actions[0].verb == "noop", plan.actions[0].reason


def test_plan_existing_ou_with_different_display_is_update(fake_ou_tree):
    client = MagicMock()
    client.get = MagicMock(side_effect=lambda path, **kw: {
        "/ous/tree": fake_ou_tree,
        "/ous/00000000-0000-0000-0000-0000000000aa": {
            "id": "00000000-0000-0000-0000-0000000000aa",
            "name": "engineering",
            "display_name": "Old Name",
            "parent_id": "00000000-0000-0000-0000-00000000dddd",
        },
    }.get(path, []))
    resolver = AddressResolver(client)
    manifest = """
apiVersion: powerloom/v1
kind: OU
metadata: { name: engineering, parent_ou_path: /dev-org }
spec: { display_name: Engineering Team }
"""
    resources = parse_manifest_text(manifest)
    plan = plan_resources(resources, resolver)
    action = plan.actions[0]
    assert action.verb == "update"
    assert any(d.field == "display_name" for d in action.changed_fields)


def test_plan_ou_with_missing_parent_is_unknown():
    """Parent OU doesn't exist yet → action verb == 'unknown' (deferred)."""
    client = MagicMock()
    client.get = MagicMock(return_value=[])
    resolver = AddressResolver(client)
    manifest = """
apiVersion: powerloom/v1
kind: Skill
metadata: { name: python-lint, ou_path: /ghost-ou }
spec:
  display_name: Python lint
  description: d
"""
    resources = parse_manifest_text(manifest)
    plan = plan_resources(resources, resolver)
    assert plan.actions[0].verb == "unknown"
    assert "ghost-ou" in (plan.actions[0].reason or "")


def test_summary_counts():
    """summary_counts sums verbs correctly."""
    client = MagicMock()
    client.get = MagicMock(return_value=[])
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
    # Note: no /ous/tree response → both will be create (parent_ou_path None → no parent).
    plan = plan_resources(parse_manifest_text(manifest), resolver)
    counts = plan.summary_counts()
    assert counts.get("create", 0) == 2
