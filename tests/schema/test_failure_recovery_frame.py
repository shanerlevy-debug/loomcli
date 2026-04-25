"""Tests for the FailureRecoveryFrame v2 stdlib derivation (added v057).

Frame Semantics derivation: compose(Process[recovery_procedure],
Policy[trigger_conditions], Scope[applicable_scope]). Canonical four
frame elements per Fillmore: Action_Attempted / Error_Type /
Corrective_Action / Final_Outcome.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

V2_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v2"
SCHEMA_PATH = V2_ROOT / "stdlib" / "failure-recovery-frame.schema.json"
EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "minimal"
    / "failure-recovery-frame.yaml"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _minimal_frame(**overrides) -> dict:
    """Return a minimum-required FailureRecoveryFrame manifest."""
    base = {
        "apiVersion": "powerloom.app/v2",
        "kind": "FailureRecoveryFrame",
        "metadata": {"name": "rate-limit-retry", "ou_path": "/acme/eng"},
        "spec": {
            "display_name": "Rate-Limit Retry",
            "applicable_scope_ref": "home.runtime.llm",
            "action_attempted": {"summary": "LLM tool call"},
            "error_type": {"category": "rate_limit"},
            "corrective_action": {"summary": "Sleep and retry"},
            "final_outcome": "recovered",
        },
    }
    for k, v in overrides.items():
        base["spec"][k] = v
    return base


def test_minimal_frame_validates() -> None:
    errors = sorted(_validator().iter_errors(_minimal_frame()), key=lambda e: list(e.path))
    assert not errors, [e.message for e in errors]


def test_example_manifest_validates() -> None:
    """The shipped example file under examples/minimal/ must validate."""
    doc = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize(
    "missing_field",
    [
        "display_name",
        "applicable_scope_ref",
        "action_attempted",
        "error_type",
        "corrective_action",
        "final_outcome",
    ],
)
def test_missing_required_spec_field_rejected(missing_field: str) -> None:
    doc = _minimal_frame()
    doc["spec"].pop(missing_field)
    errors = list(_validator().iter_errors(doc))
    assert any(missing_field in e.message for e in errors), (
        f"expected validation error mentioning {missing_field!r}, "
        f"got: {[e.message for e in errors]}"
    )


@pytest.mark.parametrize(
    "category",
    [
        "rate_limit",
        "timeout",
        "tool_call_failure",
        "context_overflow",
        "approval_denied",
        "validation_error",
        "external_dependency_failure",
        "permission_denied",
    ],
)
def test_error_categories_accepted(category: str) -> None:
    doc = _minimal_frame(error_type={"category": category})
    errors = list(_validator().iter_errors(doc))
    assert not errors, [e.message for e in errors]


def test_error_category_other_requires_signature() -> None:
    """category='other' must populate error_type.signature for disambiguation."""
    doc = _minimal_frame(error_type={"category": "other"})
    errors = list(_validator().iter_errors(doc))
    assert errors, "expected validation error when category='other' has no signature"

    # And: 'other' WITH a signature is fine.
    doc = _minimal_frame(
        error_type={"category": "other", "signature": "weird vendor-specific message"}
    )
    errors = list(_validator().iter_errors(doc))
    assert not errors, [e.message for e in errors]


def test_unknown_error_category_rejected() -> None:
    doc = _minimal_frame(error_type={"category": "made_up"})
    errors = list(_validator().iter_errors(doc))
    assert any("made_up" in e.message or "enum" in e.message for e in errors)


@pytest.mark.parametrize(
    "outcome",
    ["recovered", "partially_recovered", "escalated", "aborted"],
)
def test_final_outcomes_accepted(outcome: str) -> None:
    doc = _minimal_frame(final_outcome=outcome)
    errors = list(_validator().iter_errors(doc))
    assert not errors, [e.message for e in errors]


def test_unknown_final_outcome_rejected() -> None:
    doc = _minimal_frame(final_outcome="celebrated")
    errors = list(_validator().iter_errors(doc))
    assert any("celebrated" in e.message or "enum" in e.message for e in errors)


def test_max_attempts_bounds() -> None:
    too_low = _minimal_frame(corrective_action={"summary": "x", "max_attempts": 0})
    too_high = _minimal_frame(corrective_action={"summary": "x", "max_attempts": 101})
    ok = _minimal_frame(corrective_action={"summary": "x", "max_attempts": 50})
    assert list(_validator().iter_errors(too_low))
    assert list(_validator().iter_errors(too_high))
    assert not list(_validator().iter_errors(ok))


def test_provenance_default_and_enum() -> None:
    """provenance defaults to operator_authored; non-enum values rejected."""
    schema = _load(SCHEMA_PATH)
    prov = schema["properties"]["spec"]["properties"]["provenance"]
    assert prov["default"] == "operator_authored"
    assert set(prov["enum"]) == {
        "operator_authored",
        "distilled_from_episodic",
        "imported_template",
    }

    doc = _minimal_frame(provenance="invented_on_the_spot")
    assert list(_validator().iter_errors(doc))


def test_derivation_metadata_declares_three_primitive_slots() -> None:
    """The frame is compose(Process, Policy, Scope) per the Frame Semantics
    section in docs/memory-evolution/README.md (line 115 lock)."""
    schema = _load(SCHEMA_PATH)
    der = schema["x-powerloom-derivation"]
    assert set(der["of"]) == {"Process", "Policy", "Scope"}
    assert der["roles"]["Process"] == "recovery_procedure"
    assert der["roles"]["Policy"] == "trigger_conditions"
    assert der["roles"]["Scope"] == "applicable_scope"


def test_kind_const_is_failure_recovery_frame() -> None:
    schema = _load(SCHEMA_PATH)
    assert schema["properties"]["kind"]["const"] == "FailureRecoveryFrame"


def test_additional_properties_locked_at_root_and_spec() -> None:
    """Same hardness as other v2 stdlib derivations — typos rejected."""
    schema = _load(SCHEMA_PATH)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["spec"]["additionalProperties"] is False
    assert schema["properties"]["metadata"]["additionalProperties"] is False
