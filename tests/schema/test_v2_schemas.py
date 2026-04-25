"""Tests for the v2 schema bundle — parsing, refs, shape parity with v1.2.0.

These mirror `test_all_schemas_parse.py` + the `validate_v2_shape_parity.py`
script, run as pytest collection so CI enforces them automatically.

v056 load-bearing guarantee: every v1.2.0 manifest must validate against
the v2.0.0 stdlib derivation with nothing changed except apiVersion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

V2_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v2"
V2_SCHEMAS = list(V2_ROOT.rglob("*.schema.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_resolver(schema_path: Path) -> tuple[dict, RefResolver]:
    schema = _load(schema_path)
    store: dict[str, dict] = {}
    for p in V2_ROOT.rglob("*.schema.json"):
        doc = _load(p)
        uri = p.resolve().as_uri()
        store[uri] = doc
        if "$id" in doc:
            store[doc["$id"]] = doc
    resolver = RefResolver(
        base_uri=schema_path.resolve().as_uri(), referrer=schema, store=store
    )
    return schema, resolver


def _make_validator(schema_path: Path) -> Draft202012Validator:
    schema, resolver = _build_resolver(schema_path)
    return Draft202012Validator(schema, resolver=resolver)


# ---------------------------------------------------------------------------
# Parse + meta-validation
# ---------------------------------------------------------------------------


def test_v2_schemas_discovered() -> None:
    assert V2_SCHEMAS, "no v2 schemas found under schema/v2/"
    # Sanity: 6 primitives + 9 stdlib + compose + bundle + common = 18
    # (9th stdlib is FailureRecoveryFrame, added in v057.)
    assert len(V2_SCHEMAS) >= 17, f"expected at least 17 v2 schemas, got {len(V2_SCHEMAS)}"


@pytest.mark.parametrize("schema_path", V2_SCHEMAS, ids=lambda p: p.name)
def test_v2_schema_is_valid_json(schema_path: Path) -> None:
    data = _load(schema_path)
    assert isinstance(data, dict)
    assert "$id" in data
    assert data["$schema"].startswith("https://json-schema.org/draft/2020-12")


@pytest.mark.parametrize("schema_path", V2_SCHEMAS, ids=lambda p: p.name)
def test_v2_schema_meta_validates(schema_path: Path) -> None:
    data = _load(schema_path)
    Draft202012Validator.check_schema(data)


def test_v2_version_file_present() -> None:
    version = (V2_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    # Draft semver — accept X.Y.Z or X.Y.Z-<pre>
    parts = version.split("-", 1)
    nums = parts[0].split(".")
    assert len(nums) == 3 and all(n.isdigit() for n in nums), (
        f"VERSION not semver: {version!r}"
    )


# ---------------------------------------------------------------------------
# Cross-file ref resolution
# ---------------------------------------------------------------------------


def _iter_refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for v in node.values():
            yield from _iter_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_refs(v)


@pytest.mark.parametrize("schema_path", V2_SCHEMAS, ids=lambda p: p.name)
def test_v2_refs_resolve(schema_path: Path) -> None:
    doc = _load(schema_path)
    local_defs = set(doc.get("$defs", {}).keys())
    unresolved: list[str] = []
    for ref in _iter_refs(doc):
        if ref.startswith("#/$defs/"):
            name = ref[len("#/$defs/"):]
            if name not in local_defs:
                unresolved.append(f"internal ref {ref}")
            continue
        file_part, _, frag = ref.partition("#")
        abs_target = (schema_path.parent / file_part).resolve()
        if not abs_target.exists():
            unresolved.append(f"file ref {ref} (target={abs_target})")
            continue
        if frag and frag.startswith("/$defs/"):
            target_doc = _load(abs_target)
            name = frag[len("/$defs/"):]
            if name not in target_doc.get("$defs", {}):
                unresolved.append(f"cross-file ref {ref} — def {name!r} missing")
    assert not unresolved, f"Unresolved refs in {schema_path.name}: {unresolved}"


# ---------------------------------------------------------------------------
# Shape parity — v1.2.0 manifests validate against v2.0.0 stdlib after apiVersion bump
# ---------------------------------------------------------------------------


def _bump_api_version(doc: dict) -> dict:
    out = dict(doc)
    out["apiVersion"] = "powerloom.app/v2"
    return out


SHAPE_PARITY_FIXTURES = [
    # (v1 manifest dict or None if using file, path-or-file-key, schema path, id)
    (
        None,
        "examples/minimal/memory-policy.yaml",
        "schema/v2/stdlib/memory-policy.schema.json",
        "memory_policy_example",
    ),
    (
        None,
        "examples/minimal/workflow-type.yaml",
        "schema/v2/stdlib/workflow-type.schema.json",
        "workflow_type_example",
    ),
    (
        None,
        "examples/minimal/scope.yaml",
        "schema/v2/primitives/scope.schema.json",
        "scope_example",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Agent",
            "metadata": {"name": "test-agent", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Test Agent",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a test agent.",
                "owner_principal_ref": "user:test@acme.com",
            },
        },
        None,
        "schema/v2/stdlib/agent.schema.json",
        "agent_minimal",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Agent",
            "metadata": {"name": "coord", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Coordinator",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You coordinate.",
                "owner_principal_ref": "user:test@acme.com",
                "coordinator_role": True,
                "task_kinds": ["coordination", "routing"],
                "memory_permissions": ["home.research"],
                "reranker_model": "claude-haiku-3-5",
            },
        },
        None,
        "schema/v2/stdlib/agent.schema.json",
        "agent_with_1_2_extensions",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Skill",
            "metadata": {"name": "test-skill", "ou_path": "/acme/eng"},
            "spec": {"display_name": "Test Skill"},
        },
        None,
        "schema/v2/stdlib/skill.schema.json",
        "skill_minimal",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Skill",
            "metadata": {"name": "sys-skill", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "System Skill",
                "system": True,
                "auto_attach_to": {
                    "agent_kinds": ["user"],
                    "task_kinds": ["qa"],
                    "coordinator_role_required": False,
                },
            },
        },
        None,
        "schema/v2/stdlib/skill.schema.json",
        "skill_system_autoattach",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "MCPDeployment",
            "metadata": {"name": "team-files", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Team Files",
                "template_kind": "files",
                "isolation_mode": "shared",
                "config": {"bucket_prefix": "team-files"},
            },
        },
        None,
        "schema/v2/stdlib/mcp-deployment.schema.json",
        "mcp_deployment",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Workflow",
            "metadata": {"name": "support-triage", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Support Triage",
                "status": "draft",
                "nodes": [
                    {"id": "t1", "kind": "trigger", "trigger_kind": "manual"},
                    {"id": "a1", "kind": "agent", "agent_ref": "triage-agent"},
                ],
                "edges": [{"from": "t1", "to": "a1"}],
            },
        },
        None,
        "schema/v2/stdlib/workflow.schema.json",
        "workflow_minimal",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "OU",
            "metadata": {"name": "engineering", "parent_ou_path": "/acme"},
            "spec": {"display_name": "Engineering"},
        },
        None,
        "schema/v2/stdlib/ou.schema.json",
        "ou_minimal",
    ),
]


@pytest.mark.parametrize(
    "inline_dict,file_key,schema_rel,case_id",
    SHAPE_PARITY_FIXTURES,
    ids=[c[3] for c in SHAPE_PARITY_FIXTURES],
)
def test_v1_2_manifest_validates_against_v2_stdlib(
    inline_dict: dict | None,
    file_key: str | None,
    schema_rel: str,
    case_id: str,
) -> None:
    """A v1.2.0 manifest must validate against the v2.0.0 stdlib schema
    when ONLY the apiVersion is changed. This proves the shape-parity
    claim at the heart of v056's migration story."""
    repo_root = Path(__file__).resolve().parents[2]
    if inline_dict is not None:
        v1_doc = inline_dict
    else:
        v1_doc = yaml.safe_load((repo_root / file_key).read_text(encoding="utf-8"))

    v2_doc = _bump_api_version(v1_doc)
    schema_path = repo_root / schema_rel
    validator = _make_validator(schema_path)
    errors = sorted(validator.iter_errors(v2_doc), key=lambda e: list(e.path))
    assert not errors, (
        f"shape parity violated for case={case_id}: "
        + "; ".join(
            f"at {'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors
        )
    )
