"""Tests for v0.6.1-rc3 — `metadata.target_ou_path` field on Compose
manifests. v057 Option D scope-driven compose gating: operators can
declare which OU a kind is published into, and the engine's approval
gate evaluates against that scope rather than the org root.

JSON Schema only validates the field's *pattern* — back-end resolution
(path → UUID) lives on the engine. These tests confirm the authoring
surface accepts/rejects the right shapes so `weave compose lint` and
`weave compose scaffold` flow it through correctly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, RefResolver

V2_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v2"
COMPOSE_PATH = V2_ROOT / "compose.schema.json"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _load(COMPOSE_PATH)
    store: dict[str, dict] = {}
    for p in V2_ROOT.rglob("*.schema.json"):
        doc = _load(p)
        store[p.resolve().as_uri()] = doc
        if "$id" in doc:
            store[doc["$id"]] = doc
    resolver = RefResolver(
        base_uri=COMPOSE_PATH.resolve().as_uri(), referrer=schema, store=store
    )
    return Draft202012Validator(schema, resolver=resolver)


def _minimal(target_ou_path: str | None = None) -> dict:
    metadata = {"name": "ContractClause"}
    if target_ou_path is not None:
        metadata["target_ou_path"] = target_ou_path
    return {
        "apiVersion": "powerloom.app/v2",
        "kind": "Compose",
        "metadata": metadata,
        "spec": {
            "compose": [{"primitive": "Entity"}],
        },
    }


def test_compose_manifest_without_target_ou_path_validates():
    """Back-compat: omitted field must be allowed (it's optional)."""
    errors = list(_validator().iter_errors(_minimal()))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize(
    "path",
    [
        "/acme",
        "/acme/eng",
        "/acme/eng/platform",
        "/a-b/c-d-e",
    ],
)
def test_well_formed_target_ou_path_validates(path: str):
    errors = list(_validator().iter_errors(_minimal(target_ou_path=path)))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize(
    "path",
    [
        "no-leading-slash",
        "/UPPER",
        "//double-slash",
        "/trailing/",
        "/a b",
    ],
)
def test_malformed_target_ou_path_rejected(path: str):
    """Pattern violations must surface at lint time so authors see
    them before `weave apply` reaches the engine."""
    errors = list(_validator().iter_errors(_minimal(target_ou_path=path)))
    assert errors, f"expected pattern violation for path {path!r}"


def test_compose_schema_documents_field():
    """The schema description must explain Option D so authors reading
    the JSON Schema understand the gate-relaxation semantics."""
    schema = _load(COMPOSE_PATH)
    field = schema["properties"]["metadata"]["properties"].get("target_ou_path")
    assert field is not None, "target_ou_path missing from compose.schema.json"
    desc = (field.get("description") or "").lower()
    assert "approval gate" in desc
    assert "back-compat" in desc or "org-root" in desc
