"""Tests for the TypeDefinition v2 stdlib derivation (added v061).

A TypeDefinition declares a named type the engine accumulates memory
around — grammar (how it composes with others) + lexicon (specific
instances + outcome signals). The manifest itself is JSON-Schema
validated here; engine-side persistence + the consolidation pipeline
that fills the cells lives in v062's PR.

Derivation: compose(Entity[type_identity], Policy[memory_governance])
per `docs/memory-evolution/four-part-api-design.md` §1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

V2_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v2"
SCHEMA_PATH = V2_ROOT / "stdlib" / "type-definition.schema.json"
EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "minimal"
    / "type-definition.yaml"
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
        "display_name": "ContractClause",
        "type_kind": "domain",
        "applies_to_scope_ref": "legal.contracts",
    }
    base_spec.update(spec_overrides)
    return {
        "apiVersion": "powerloom.app/v2",
        "kind": "TypeDefinition",
        "metadata": {"name": "contract-clause", "ou_path": "/acme/legal"},
        "spec": base_spec,
    }


def test_minimal_typedefinition_validates():
    errors = list(_validator().iter_errors(_minimal()))
    assert not errors, [e.message for e in errors]


def test_example_manifest_validates():
    doc = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    errors = list(_validator().iter_errors(doc))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize(
    "missing_field",
    ["display_name", "type_kind", "applies_to_scope_ref"],
)
def test_missing_required_field_rejected(missing_field: str):
    doc = _minimal()
    doc["spec"].pop(missing_field)
    errors = list(_validator().iter_errors(doc))
    assert any(missing_field in e.message for e in errors), (
        f"expected error mentioning {missing_field!r}, "
        f"got {[e.message for e in errors]}"
    )


@pytest.mark.parametrize(
    "type_kind", ["domain", "process", "event", "relation"]
)
def test_type_kind_enum_accepts_canonical_values(type_kind: str):
    errors = list(_validator().iter_errors(_minimal(type_kind=type_kind)))
    assert not errors, [e.message for e in errors]


def test_unknown_type_kind_rejected():
    errors = list(_validator().iter_errors(_minimal(type_kind="metaphysical")))
    assert errors


def test_memory_block_optional():
    """Manifest with no memory block is valid; engine applies defaults."""
    doc = _minimal()
    assert "memory" not in doc["spec"]
    errors = list(_validator().iter_errors(doc))
    assert not errors


def test_memory_grammar_decay_bounds():
    too_short = _minimal(memory={"grammar": {"decay_half_life_days": 0}})
    too_long = _minimal(memory={"grammar": {"decay_half_life_days": 99999}})
    ok = _minimal(memory={"grammar": {"decay_half_life_days": 365}})
    assert list(_validator().iter_errors(too_short))
    assert list(_validator().iter_errors(too_long))
    assert not list(_validator().iter_errors(ok))


def test_memory_reinforcement_outcome_bounds():
    too_low = _minimal(
        memory={"reinforcement": {"min_outcome_signal": -1.5}}
    )
    too_high = _minimal(
        memory={"reinforcement": {"min_outcome_signal": 1.5}}
    )
    ok = _minimal(memory={"reinforcement": {"min_outcome_signal": 0.7}})
    assert list(_validator().iter_errors(too_low))
    assert list(_validator().iter_errors(too_high))
    assert not list(_validator().iter_errors(ok))


def test_concept_stabilization_block_present_but_disabled_by_default():
    """The block exists for v063 forward-compat but defaults to disabled
    so v061/v062 deployments get no behavior change."""
    schema = _load(SCHEMA_PATH)
    block = schema["$defs"]["memory_block"]["properties"]["concept_stabilization"]
    assert block["properties"]["enabled"]["default"] is False


def test_extends_type_ref_optional_and_nullable():
    base = _minimal(extends_type_ref=None)
    pointed = _minimal(extends_type_ref="legal-document")
    assert not list(_validator().iter_errors(base))
    assert not list(_validator().iter_errors(pointed))


def test_namespace_pattern_enforced():
    bad = _minimal(type_namespace="UPPER")
    assert list(_validator().iter_errors(bad))
    good = _minimal(type_namespace="acme.legal.contracts")
    assert not list(_validator().iter_errors(good))


def test_derivation_metadata_declares_entity_plus_policy():
    schema = _load(SCHEMA_PATH)
    der = schema["x-powerloom-derivation"]
    assert set(der["of"]) == {"Entity", "Policy"}
    assert der["roles"]["Entity"] == "type_identity"
    assert der["roles"]["Policy"] == "memory_governance"


def test_kind_const_is_type_definition():
    schema = _load(SCHEMA_PATH)
    assert schema["properties"]["kind"]["const"] == "TypeDefinition"


def test_additional_properties_locked_at_root_metadata_spec_and_memory():
    schema = _load(SCHEMA_PATH)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["spec"]["additionalProperties"] is False
    assert schema["properties"]["metadata"]["additionalProperties"] is False
    assert schema["$defs"]["memory_block"]["additionalProperties"] is False
