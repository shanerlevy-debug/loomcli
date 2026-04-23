"""Every minimal example YAML must validate against its matching kind schema and the bundle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v1"
EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "minimal"


def _build_registry() -> Registry:
    registry: Registry = Registry()
    for schema_path in SCHEMA_ROOT.rglob("*.schema.json"):
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        resource = Resource(contents=data, specification=DRAFT202012)
        registry = registry.with_resource(uri=data["$id"], resource=resource)
    return registry


REGISTRY = _build_registry()

EXAMPLE_TO_KIND_SCHEMA = {
    "ou.yaml": "ou.schema.json",
    "group.yaml": "group.schema.json",
    "group-membership.yaml": "group-membership.schema.json",
    "role-binding.yaml": "role-binding.schema.json",
    "skill.yaml": "skill.schema.json",
    "skill-access-grant.yaml": "skill-access-grant.schema.json",
    "credential.yaml": "credential.schema.json",
    "mcp-server-registration.yaml": "mcp-server-registration.schema.json",
    "mcp-deployment.yaml": "mcp-deployment.schema.json",
    "agent.yaml": "agent.schema.json",
    "agent-skill.yaml": "agent-skill.schema.json",
    "agent-mcp-server.yaml": "agent-mcp-server.schema.json",
    "workflow.yaml": "workflow.schema.json",
    # v1.2.0 additions (memory system + loomcli schema evolution)
    "workflow-type.yaml": "workflow-type.schema.json",
    "memory-policy.yaml": "memory-policy.schema.json",
    "scope.yaml": "scope.schema.json",
}


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("example_filename,schema_filename", sorted(EXAMPLE_TO_KIND_SCHEMA.items()))
def test_example_validates_against_kind_schema(example_filename: str, schema_filename: str) -> None:
    example = _load_yaml(EXAMPLES_ROOT / example_filename)
    schema = json.loads((SCHEMA_ROOT / "kinds" / schema_filename).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=REGISTRY)
    errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
    assert not errors, (
        f"{example_filename} failed validation:\n" + "\n".join(f"  {list(e.path)}: {e.message}" for e in errors)
    )


def test_every_kind_has_a_minimal_example() -> None:
    kind_files = {p.name for p in (SCHEMA_ROOT / "kinds").glob("*.schema.json")}
    examples = set(EXAMPLE_TO_KIND_SCHEMA.values())
    missing = kind_files - examples
    assert not missing, f"kinds without an examples/minimal/*.yaml: {sorted(missing)}"


def test_example_validates_against_bundle() -> None:
    bundle = json.loads((SCHEMA_ROOT / "powerloom.v1.bundle.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(bundle, registry=REGISTRY)
    for filename in EXAMPLE_TO_KIND_SCHEMA:
        example = _load_yaml(EXAMPLES_ROOT / filename)
        errors = list(validator.iter_errors(example))
        assert not errors, f"{filename} failed bundle validation: {[e.message for e in errors]}"
