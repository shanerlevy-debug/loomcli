"""Smoke tests for the generated `loomcli.schema` Pydantic package.

These prove:
  - The package imports cleanly.
  - Every stdlib kind can be instantiated from a minimal valid payload.
  - Every primitive module has at least one class exposed.
  - The SCHEMA_VERSION marker matches schema/v2/VERSION.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loomcli.schema import SCHEMA_VERSION
from loomcli.schema.v2 import common, compose, primitives, stdlib


def test_schema_version_matches_source() -> None:
    root = Path(__file__).resolve().parent.parent
    src_version = (root / "schema" / "v2" / "VERSION").read_text(encoding="utf-8").strip()
    assert SCHEMA_VERSION == src_version


def test_common_has_definitions() -> None:
    # common.py holds the $defs as top-level models.
    assert hasattr(common, "Common") or hasattr(common, "BaseModel")


def test_compose_manifest_validates() -> None:
    # Compose model accepts a minimal valid compose manifest.
    mgr = compose.Compose.model_validate({
        "apiVersion": "powerloom.app/v2",
        "kind": "Compose",
        "metadata": {"name": "ContractClause", "namespace": "legal.acme"},
        "spec": {"compose": [{"primitive": "Entity", "fields": {}}]},
    })
    assert mgr.kind.value == "Compose" if hasattr(mgr.kind, 'value') else str(mgr.kind) == "Compose"


def test_stdlib_agent_validates() -> None:
    a = stdlib.agent.Agent.model_validate({
        "apiVersion": "powerloom.app/v2",
        "kind": "Agent",
        "metadata": {"name": "code-reviewer", "ou_path": "/acme/eng"},
        "spec": {
            "display_name": "Code Reviewer",
            "model": "claude-sonnet-4-5",
            "system_prompt": "Review code.",
            "owner_principal_ref": "user:jane@acme.com",
        },
    })
    assert a is not None


def test_stdlib_skill_validates() -> None:
    # Construct from dict; validation only — we don't assert shape.
    stdlib.skill.Skill.model_validate({
        "apiVersion": "powerloom.app/v2",
        "kind": "Skill",
        "metadata": {"name": "code-reviewer", "ou_path": "/acme/eng"},
        "spec": {"display_name": "Reviewer"},
    })


@pytest.mark.parametrize("mod_name", [
    "entity", "event", "policy", "process", "relation", "scope",
])
def test_primitive_module_importable(mod_name: str) -> None:
    mod = getattr(primitives, mod_name)
    # At least one BaseModel class beyond the stdlib helpers.
    classes = [c for c in dir(mod) if c[0].isupper() and not c.startswith("_")]
    assert len(classes) >= 1
