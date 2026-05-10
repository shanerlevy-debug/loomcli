"""Conformance: every JSON-schema kind has a matching CLI registration.

Born from the 2026-05-09 demo failure (Powerloom tracker thread
`c77b8922` — "Single-path invariant + surface index"). The Workflow
kind shipped in `schema/v1/kinds/workflow.schema.json` (with
`x-powerloom-apply-order: 100`) but had no entry in `KINDS` /
`_APPLY_ORDER` / `_HANDLERS`. `weave apply` errored cryptically with
"kind 'Workflow' is schema-valid but not yet implemented in the CLI",
the deploying agent misdiagnosed it as "Phase 14 runtime not shipped"
(an inference about the server when the error was about the client),
and the demo blocked.

This test makes that drift impossible to ship by accident — if a
future schema kind lands without a handler, CI fails immediately
rather than at demo time.

The asymmetry it enforces:
  * Every kind in `schema/v1/kinds/*.schema.json` MUST have a
    `KINDS` entry (or be listed in `_SCHEMA_ONLY_KINDS` with a
    thread link explaining why it's intentionally CLI-omitted).
  * Every `KINDS` entry MUST have an `_APPLY_ORDER` and a
    registered `Handler` (orphan registrations would never run).
  * Every registered handler MUST have a `KINDS` entry (orphan
    handlers — code that's compiled but unreachable).

Known gaps (kinds in KINDS but missing handler/apply-order) are
allow-listed below WITH thread links — new drift still fails, but
existing drift doesn't block CI while it's tracked.

Cross-reference:
  * Phase 0 = this test.
  * Phase 1 = surface_index.json emitter using the same logic
    (separate PR; not yet shipped).
"""
from __future__ import annotations

import json
from pathlib import Path

from loomcli.manifest.applier import _APPLY_ORDER
from loomcli.manifest.handlers import _HANDLERS
from loomcli.manifest.schema import KINDS


# ---------------------------------------------------------------------------
# Allow-lists — known gaps tracked elsewhere.
#
# Format: { "KindName": "thread:<thread_id> — short rationale" }
#
# The test EXEMPTS allow-listed kinds from the corresponding check but
# still fails for any new drift. Removing an entry from a list once
# the gap is fixed is part of that gap's PR.
# ---------------------------------------------------------------------------

# Kinds intentionally in the JSON schema but NOT wired through the CLI's
# manifest framework (e.g., server-side-only resource types that ship
# their own dedicated CLI command instead). Empty for now.
_SCHEMA_ONLY_KINDS: dict[str, str] = {}

# Kinds in `KINDS` but missing a registered handler. Apply-time would
# fail with "no handler for <kind>". Same shape as the Workflow gap
# that broke the 2026-05-09 demo — these three were surfaced by this
# test's first run on 2026-05-10.
_KNOWN_HANDLER_GAPS: dict[str, str] = {
    "WorkflowType": "thread:e1518478 — v1.2.0 kind, schema+KINDS shipped, handler not wired",
    "MemoryPolicy": "thread:e1518478 — v1.2.0 kind, schema+KINDS shipped, handler not wired",
    "Scope": "thread:e1518478 — v1.2.0 kind, schema+KINDS shipped, handler not wired",
}

# Kinds in `KINDS` but missing an `_APPLY_ORDER` entry. Same set as
# the handler gap today — fixing the handler in thread `e1518478`
# also adds the apply-order line.
_KNOWN_APPLY_ORDER_GAPS: dict[str, str] = {
    "WorkflowType": "thread:e1518478 — paired with handler wire-up",
    "MemoryPolicy": "thread:e1518478 — paired with handler wire-up",
    "Scope": "thread:e1518478 — paired with handler wire-up",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _schema_kinds() -> set[str]:
    """Walk schema/v1/kinds/*.schema.json and extract each schema's
    declared `kind` const value (e.g. {"properties": {"kind": {"const":
    "Workflow"}}} → "Workflow").

    `common.schema.json` is the shared $defs file with no `kind`
    const — skipped by lookup since `properties.kind.const` is absent.
    """
    schema_dir = Path(__file__).resolve().parent.parent / "schema" / "v1" / "kinds"
    if not schema_dir.is_dir():
        # Schema dir lives at repo root; if the test ever moves this
        # check fails loudly rather than silently passing on an empty set.
        raise AssertionError(
            f"Schema directory not found at {schema_dir}. The conformance "
            f"test resolves it relative to this file — update the path "
            f"if the schema layout has been refactored."
        )
    kinds: set[str] = set()
    for f in sorted(schema_dir.glob("*.schema.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"Schema file {f.name} is not valid JSON: {e}"
            ) from e
        kind_const = (
            data.get("properties", {}).get("kind", {}).get("const")
        )
        if isinstance(kind_const, str) and kind_const:
            kinds.add(kind_const)
    return kinds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_schema_kinds_directory_is_non_empty():
    """Sanity check — if the schema dir is empty or unreadable, every
    other test in this file would silently pass on an empty set. Fail
    loudly here so the failure mode is unambiguous."""
    kinds = _schema_kinds()
    assert len(kinds) >= 10, (
        f"Expected at least 10 schema kinds (Agent, OU, Skill, ...); "
        f"found {len(kinds)}: {sorted(kinds)}. The schema layout may "
        f"have changed."
    )


def test_every_schema_kind_has_a_KINDS_entry():
    """The 2026-05-09 Workflow regression in single-test form. If a
    schema kind ships without a `KINDS` entry, fail."""
    schema_kinds = _schema_kinds() - set(_SCHEMA_ONLY_KINDS)
    missing = schema_kinds - set(KINDS)
    assert not missing, (
        f"Schema kinds without a `KINDS` entry: {sorted(missing)}.\n"
        f"To fix: add the kind to `loomcli/manifest/schema.py::KINDS` "
        f"and register a handler in `handlers.py`. If the kind is "
        f"intentionally not CLI-wired, add it to "
        f"`tests/test_kind_registry_conformance.py::_SCHEMA_ONLY_KINDS` "
        f"with a Powerloom tracker thread explaining why.\n"
        f"This test was added 2026-05-10 after the Workflow kind drift "
        f"caused a demo failure — see thread `c77b8922`."
    )


def test_every_KINDS_entry_has_apply_order():
    """A `KINDS` entry without an `_APPLY_ORDER` falls into the
    fallback bucket (99) — works, but loses dependency-sort
    semantics (e.g., parent OUs landing before children). Force
    explicit ordering for every kind."""
    missing = (set(KINDS) - set(_APPLY_ORDER)) - set(_KNOWN_APPLY_ORDER_GAPS)
    assert not missing, (
        f"`KINDS` entries without `_APPLY_ORDER`: {sorted(missing)}.\n"
        f"To fix: add an entry in `loomcli/manifest/applier.py::_APPLY_ORDER`. "
        f"Lower number = earlier in apply order. Use the existing entries "
        f"as a guide (OU=10 → Agent=40 → AgentSkill=50 → Workflow=90).\n"
        f"If the gap is intentional + tracked, add to "
        f"`_KNOWN_APPLY_ORDER_GAPS` with a thread link."
    )


def test_every_KINDS_entry_has_a_handler():
    """A `KINDS` entry without a registered handler would be parsed
    fine but would error at apply-time with `no handler for <kind>`.
    Catch it at test-time instead."""
    missing = (set(KINDS) - set(_HANDLERS)) - set(_KNOWN_HANDLER_GAPS)
    assert not missing, (
        f"`KINDS` entries without a registered handler: {sorted(missing)}.\n"
        f"To fix: add a handler class in `loomcli/manifest/handlers.py` "
        f"and call `register(YourHandler())` at module scope.\n"
        f"If the gap is intentional + tracked, add to "
        f"`_KNOWN_HANDLER_GAPS` with a thread link."
    )


def test_no_orphan_handlers():
    """Inverse of the above — a handler registered for a kind that
    isn't in `KINDS` would never be reachable (parser rejects unknown
    kinds before dispatch). Surface the dead code."""
    orphans = set(_HANDLERS) - set(KINDS)
    assert not orphans, (
        f"Handlers registered for kinds not in `KINDS`: {sorted(orphans)}.\n"
        f"This handler is unreachable. Either add the kind to `KINDS` "
        f"(if it's meant to be CLI-wired) or remove the handler "
        f"registration."
    )


def test_no_orphan_apply_order_entries():
    """An `_APPLY_ORDER` entry for a kind not in `KINDS` is dead
    config — it won't ever be looked up. Drop the line or wire the
    kind."""
    orphans = set(_APPLY_ORDER) - set(KINDS)
    assert not orphans, (
        f"`_APPLY_ORDER` entries for kinds not in `KINDS`: {sorted(orphans)}.\n"
        f"Either wire the kind into `KINDS` + handler, or remove the "
        f"`_APPLY_ORDER` line."
    )


def test_known_gap_allow_lists_reference_real_threads():
    """Each entry in `_KNOWN_HANDLER_GAPS` and `_KNOWN_APPLY_ORDER_GAPS`
    must reference a tracker thread (`thread:<id>`) so future readers
    can find the work. Catches the failure mode where someone allow-
    lists a kind to make CI green and forgets to file the follow-up."""
    for label, allow_list in (
        ("_KNOWN_HANDLER_GAPS", _KNOWN_HANDLER_GAPS),
        ("_KNOWN_APPLY_ORDER_GAPS", _KNOWN_APPLY_ORDER_GAPS),
    ):
        for kind, rationale in allow_list.items():
            assert "thread:" in rationale, (
                f"{label}[{kind!r}] must reference a tracker thread "
                f"(`thread:<id>`) in its rationale; got {rationale!r}.\n"
                f"File a thread (e.g. `weave thread create --project "
                f"powerloom --title 'Wire <Kind> handler' ...`) and "
                f"include the returned id."
            )


def test_known_gap_allow_lists_only_contain_real_kinds():
    """Stale allow-list entries (referencing kinds that no longer exist
    in `KINDS`) silently exempt nothing and clutter future debugging.
    Force a cleanup pass when a kind is removed."""
    for label, allow_list in (
        ("_SCHEMA_ONLY_KINDS", _SCHEMA_ONLY_KINDS),
        ("_KNOWN_HANDLER_GAPS", _KNOWN_HANDLER_GAPS),
        ("_KNOWN_APPLY_ORDER_GAPS", _KNOWN_APPLY_ORDER_GAPS),
    ):
        # _SCHEMA_ONLY_KINDS is the special case: those kinds should NOT
        # be in `KINDS` (that's the whole point). The other two should be.
        if label == "_SCHEMA_ONLY_KINDS":
            stale = set(allow_list) & set(KINDS)
            msg = "are now in KINDS; remove from _SCHEMA_ONLY_KINDS"
        else:
            stale = set(allow_list) - set(KINDS)
            msg = f"are not in KINDS; either restore them or drop from {label}"
        assert not stale, f"Allow-list {label!r}: kinds {sorted(stale)} {msg}."
