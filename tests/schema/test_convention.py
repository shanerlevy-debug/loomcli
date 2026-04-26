"""Tests for the Convention v2 stdlib derivation (added v064).

Conventions are top-down authored organizational rules — distinct
from procedural memory templates (which are bottom-up reinforced
from observed runs). Derivation: compose(Policy[intent],
Scope[applies_to]). Per the Q6 procedural-memory storage decision,
Conventions and procedural memory live in separate dedicated tables
on the engine; the manifest itself ships here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

V2_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v2"
SCHEMA_PATH = V2_ROOT / "stdlib" / "convention.schema.json"
EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "minimal"
    / "convention.yaml"
)


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _load(SCHEMA_PATH)
    store: dict[str, dict] = {}
    for p in V2_ROOT.rglob("*.schema.json"):
        doc = _load(p)
        store[p.resolve().as_uri()] = doc
        if "$id" in doc:
            store[doc["$id"]] = doc
    resolver = RefResolver(
        base_uri=SCHEMA_PATH.resolve().as_uri(), referrer=schema, store=store
    )
    return Draft202012Validator(schema, resolver=resolver)


def _minimal(**spec_overrides) -> dict:
    base_spec = {
        "display_name": "Code Review Checklist",
        "applies_to_scope_ref": "engineering.code-reviews",
        "body": {"summary": "Tests must pass before merge."},
    }
    base_spec.update(spec_overrides)
    return {
        "apiVersion": "powerloom.app/v2",
        "kind": "Convention",
        "metadata": {
            "name": "code-review-checklist",
            "ou_path": "/acme/eng",
        },
        "spec": base_spec,
    }


def test_minimal_convention_validates():
    errors = list(_validator().iter_errors(_minimal()))
    assert not errors, [e.message for e in errors]


def test_example_manifest_validates():
    doc = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    errors = list(_validator().iter_errors(doc))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize(
    "missing", ["display_name", "applies_to_scope_ref", "body"]
)
def test_missing_required_field_rejected(missing: str):
    doc = _minimal()
    doc["spec"].pop(missing)
    errors = list(_validator().iter_errors(doc))
    assert any(missing in e.message for e in errors), (
        f"expected error mentioning {missing!r}, "
        f"got {[e.message for e in errors]}"
    )


def test_body_summary_required():
    doc = _minimal(body={})
    errors = list(_validator().iter_errors(doc))
    assert errors


def test_body_items_optional_but_validated():
    doc = _minimal(body={"summary": "x", "items": ["a", "b", "c"]})
    assert not list(_validator().iter_errors(doc))


def test_body_items_unique():
    doc = _minimal(body={"summary": "x", "items": ["a", "a"]})
    errors = list(_validator().iter_errors(doc))
    assert any("unique" in e.message.lower() for e in errors)


@pytest.mark.parametrize(
    "mode", ["advisory", "warn", "enforce"]
)
def test_enforcement_mode_enum_accepts_canonical(mode: str):
    errors = list(_validator().iter_errors(_minimal(enforcement_mode=mode)))
    assert not errors


def test_enforcement_mode_default_is_advisory():
    schema = _load(SCHEMA_PATH)
    field = schema["properties"]["spec"]["properties"]["enforcement_mode"]
    # Schema field is a $ref + default; the default is at the property
    # level (not inside $defs).
    assert field.get("default") == "advisory"


def test_enforcement_mode_unknown_rejected():
    errors = list(_validator().iter_errors(_minimal(enforcement_mode="strict")))
    assert errors


@pytest.mark.parametrize("status", ["active", "archived"])
def test_status_enum(status: str):
    assert not list(_validator().iter_errors(_minimal(status=status)))


def test_status_default_is_active():
    schema = _load(SCHEMA_PATH)
    field = schema["properties"]["spec"]["properties"]["status"]
    assert field.get("default") == "active"


def test_references_optional():
    base = _minimal()
    assert "references" not in base["spec"]
    assert not list(_validator().iter_errors(base))


def test_references_concept_kind_with_uuid_validates():
    refs = [
        {
            "kind": "concept",
            "id": "c0ffee00-0000-0000-0000-000000000001",
        }
    ]
    assert not list(_validator().iter_errors(_minimal(references=refs)))


def test_references_stdlib_kind_with_name_validates():
    refs = [{"kind": "stdlib", "name": "FailureRecoveryFrame"}]
    assert not list(_validator().iter_errors(_minimal(references=refs)))


def test_references_unknown_kind_rejected():
    refs = [{"kind": "made_up", "name": "Foo"}]
    assert list(_validator().iter_errors(_minimal(references=refs)))


def test_additional_scope_refs_optional():
    multi = _minimal(
        additional_scope_refs=["engineering.qa", "engineering.platform"]
    )
    assert not list(_validator().iter_errors(multi))


def test_derivation_metadata_declares_policy_plus_scope():
    schema = _load(SCHEMA_PATH)
    der = schema["x-powerloom-derivation"]
    assert set(der["of"]) == {"Policy", "Scope"}
    assert der["roles"]["Policy"] == "intent"
    assert der["roles"]["Scope"] == "applies_to"


def test_kind_const_is_convention():
    schema = _load(SCHEMA_PATH)
    assert schema["properties"]["kind"]["const"] == "Convention"


def test_additional_properties_locked_at_root_metadata_spec_and_body():
    schema = _load(SCHEMA_PATH)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["spec"]["additionalProperties"] is False
    assert schema["properties"]["metadata"]["additionalProperties"] is False
    assert schema["$defs"]["convention_body"]["additionalProperties"] is False


def test_summary_length_capped():
    long_summary = "x" * 5000
    doc = _minimal(body={"summary": long_summary})
    assert list(_validator().iter_errors(doc))
