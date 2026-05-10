"""Conformance: every JSON-schema kind has a matching CLI registration.

Born from the 2026-05-09 demo failure (Powerloom tracker thread
``c77b8922`` — "Single-path invariant + surface index"). The Workflow
kind shipped in ``schema/v1/kinds/workflow.schema.json`` but had no
entry in ``KINDS`` / ``_APPLY_ORDER`` / ``_HANDLERS``. ``weave apply``
errored cryptically, the deploying agent misdiagnosed it as a runtime
gap, and the demo blocked.

## Phase 0 → Phase 1 refactor (2026-05-10)

The Phase 0 version of this test (PR #84) carried its own in-test
allow-list dicts (``_KNOWN_HANDLER_GAPS``, ``_KNOWN_APPLY_ORDER_GAPS``)
to track the v1.2.0 kinds with schema+KINDS but no handler.

Phase 1 (this file) flips the source of truth: hand annotations now
live in ``surface_known_gaps.json`` and flow into ``surface_index.json``
via ``scripts/build_surface_index.py``. The test reads the index and
asserts every row is either fully wired or has matching ``known_gaps``
entries with thread references.

Same enforcement, but the data lives where humans + agents can read
it (the JSON file) instead of where only test runners look (test code).

## What the test enforces

For every entry in ``surface_index.json::manifest_kinds``:
  * If ``in_kinds_registry`` is true, then ``handler_class`` must be
    set OR a ``known_gaps`` entry of kind ``missing_handler`` exists.
  * If ``in_kinds_registry`` is true, then ``apply_order`` must be
    set OR a ``known_gaps`` entry of kind ``missing_apply_order`` exists.
  * If ``schema_path`` is set, ``in_kinds_registry`` must be true
    (otherwise ``weave apply`` errors with "kind X is schema-valid
    but not yet implemented").
  * Inverse: every handler in ``_HANDLERS`` must have a row in the
    index — orphan handlers are dead code.

These invariants used to live as direct ``set`` arithmetic on
``KINDS`` / ``_APPLY_ORDER`` / ``_HANDLERS``. They now flow through
the index, which means the index is also a load-bearing artifact —
the drift test (``test_surface_index_drift.py``) keeps it honest.

## Cross-references
  * Schema doc: ``docs/surface-index.md``
  * Hand annotations: ``surface_known_gaps.json``
  * Drift gate: ``tests/test_surface_index_drift.py``
  * Tracker thread: ed4058c4 (Phase 1)
"""
from __future__ import annotations

import json
from pathlib import Path

from loomcli.manifest.handlers import _HANDLERS
from loomcli.manifest.schema import KINDS


REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "surface_index.json"


def _load_index() -> dict:
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _gap_kinds(row: dict) -> set[str]:
    """The set of ``known_gaps[*].kind`` values on this row."""
    return {g.get("kind") for g in (row.get("known_gaps") or []) if g.get("kind")}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_index_has_at_least_ten_kinds():
    """Sanity: a near-empty index implies the emitter broke or pointed
    at the wrong code. Real loomcli has 16+ kinds today."""
    rows = _load_index()["manifest_kinds"]
    assert len(rows) >= 10, (
        f"surface_index.json::manifest_kinds has only {len(rows)} entries. "
        f"Emitter likely failed silently. Run: python scripts/build_surface_index.py"
    )


def test_schema_kinds_are_in_registry():
    """A kind with a schema file MUST be in ``KINDS``. Without it,
    ``weave apply`` errors with the cryptic 'schema-valid but not yet
    implemented' message that broke the 2026-05-09 demo."""
    rows = _load_index()["manifest_kinds"]
    bad = [
        r["name"] for r in rows
        if r["schema_path"] is not None and not r["in_kinds_registry"]
    ]
    assert not bad, (
        f"Schema kinds missing from KINDS: {sorted(bad)}.\n"
        f"To fix: add the kind to loomcli/manifest/schema.py::KINDS "
        f"and register a handler. The 2026-05-09 Workflow regression "
        f"is the canonical example of why this matters."
    )


def test_kinds_in_registry_have_handlers_or_tracked_gap():
    """A ``KINDS`` entry without a handler errors at apply-time with
    'no handler for X'. Either wire a handler OR document the gap with
    a thread in ``surface_known_gaps.json``."""
    rows = _load_index()["manifest_kinds"]
    bad: list[str] = []
    for r in rows:
        if not r["in_kinds_registry"]:
            continue
        if r["handler_class"] is None and "missing_handler" not in _gap_kinds(r):
            bad.append(r["name"])
    assert not bad, (
        f"KINDS entries without handler AND without 'missing_handler' gap: {sorted(bad)}.\n"
        f"To fix: register a handler in loomcli/manifest/handlers.py "
        f"OR add an entry to surface_known_gaps.json::manifest_kinds.<name>.gaps "
        f"with kind='missing_handler' and a tracker thread, then regenerate."
    )


def test_kinds_in_registry_have_apply_order_or_tracked_gap():
    """A ``KINDS`` entry without an ``_APPLY_ORDER`` falls into the
    fallback bucket (99) — works, but loses dependency-sort semantics.
    Force explicit ordering or a tracked gap."""
    rows = _load_index()["manifest_kinds"]
    bad: list[str] = []
    for r in rows:
        if not r["in_kinds_registry"]:
            continue
        if r["apply_order"] is None and "missing_apply_order" not in _gap_kinds(r):
            bad.append(r["name"])
    assert not bad, (
        f"KINDS entries without _APPLY_ORDER AND without 'missing_apply_order' gap: {sorted(bad)}.\n"
        f"To fix: add to loomcli/manifest/applier.py::_APPLY_ORDER "
        f"OR document the gap in surface_known_gaps.json with a thread."
    )


def test_no_orphan_handlers():
    """A handler whose kind isn't in ``KINDS`` is dead code — the
    parser rejects unknown kinds before dispatch could ever reach it."""
    orphans = set(_HANDLERS) - set(KINDS)
    assert not orphans, (
        f"Handlers registered for kinds not in KINDS: {sorted(orphans)}.\n"
        f"Either wire the kind into KINDS, or remove the handler "
        f"registration."
    )


def test_known_gaps_are_consistent_with_runtime():
    """Cross-check: a kind with ``missing_handler`` in its known_gaps
    must actually have ``handler_class=null``. Catches the failure
    mode where someone wires a handler but forgets to remove the
    surface_known_gaps.json entry."""
    rows = _load_index()["manifest_kinds"]
    inconsistent: list[str] = []
    for r in rows:
        gaps = _gap_kinds(r)
        if "missing_handler" in gaps and r["handler_class"] is not None:
            inconsistent.append(
                f"{r['name']}: gap claims missing_handler but handler is "
                f"{r['handler_class']!r}"
            )
        if "missing_apply_order" in gaps and r["apply_order"] is not None:
            inconsistent.append(
                f"{r['name']}: gap claims missing_apply_order but apply_order is "
                f"{r['apply_order']!r}"
            )
    assert not inconsistent, (
        "Stale known_gaps entries (gap claimed but not actually present):\n  "
        + "\n  ".join(inconsistent)
        + "\n\nFix: remove the resolved gap from surface_known_gaps.json, "
        + "regenerate, mark the matching tracker thread done."
    )
