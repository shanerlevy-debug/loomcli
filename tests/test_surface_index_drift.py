"""CI drift gate for ``surface_index.json``.

Runs the emitter in ``--check`` mode and fails if the committed
``surface_index.json`` disagrees with what the current code produces.

Plus a few sanity checks on the index shape itself so a malformed or
truncated file fails loudly here instead of confusing downstream
consumers (e.g. ``test_kind_registry_conformance.py``, agents reading
the file at session start).

Cross-references:
  * The emitter: ``scripts/build_surface_index.py``
  * The schema doc: ``docs/surface-index.md``
  * Hand annotations: ``surface_known_gaps.json``
  * Tracker thread: ed4058c4 (Phase 1 of single-path-invariant arc)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "surface_index.json"
EMITTER_PATH = REPO_ROOT / "scripts" / "build_surface_index.py"


def test_surface_index_committed():
    """The committed file must exist. Otherwise downstream tests +
    agents reading it produce confusing FileNotFoundError stacks."""
    assert INDEX_PATH.exists(), (
        f"surface_index.json missing at {INDEX_PATH}. "
        f"Run: python scripts/build_surface_index.py"
    )


def test_surface_index_no_drift():
    """Re-emit + diff. Fails on any drift between committed file and
    what the current code generates."""
    result = subprocess.run(
        [sys.executable, str(EMITTER_PATH), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"surface_index.json drift detected.\n"
        f"--- emitter stderr ---\n{result.stderr}\n"
        f"--- fix ---\n"
        f"  Run: python scripts/build_surface_index.py\n"
        f"  Commit the regenerated file.\n"
        f"  Investigate the underlying code change that caused drift "
        f"(usually: a kind / command was added without the matching "
        f"surface_known_gaps.json entry, or a known-gap kind was wired "
        f"in code without removing its surface_known_gaps.json entry)."
    )


def test_surface_index_schema_shape():
    """Top-level keys exist and match the documented schema."""
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    assert data.get("$schema_version") == "1", (
        f"surface_index.json $schema_version must be '1'; got {data.get('$schema_version')!r}. "
        f"If the shape evolved, bump the version + update docs/surface-index.md."
    )
    assert data.get("repo") == "loomcli"
    assert isinstance(data.get("manifest_kinds"), list)
    assert isinstance(data.get("cli_commands"), list)


def test_manifest_kinds_required_fields():
    """Every manifest_kind row has the documented field set."""
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    expected_keys = {
        "name", "schema_path", "in_kinds_registry", "handler_class",
        "apply_order", "category", "known_gaps",
    }
    for row in data["manifest_kinds"]:
        missing = expected_keys - set(row.keys())
        assert not missing, (
            f"manifest_kinds[{row.get('name')!r}] missing keys: {sorted(missing)}.\n"
            f"Row: {row}"
        )


def test_cli_commands_required_fields():
    """Every cli_commands row has the documented field set."""
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    expected_keys = {
        "path", "module", "summary", "parent", "deprecated",
        "is_group", "known_gaps",
    }
    for row in data["cli_commands"]:
        missing = expected_keys - set(row.keys())
        assert not missing, (
            f"cli_commands[{row.get('path')!r}] missing keys: {sorted(missing)}.\n"
            f"Row: {row}"
        )


def test_known_gaps_have_thread_references():
    """Every entry in any row's ``known_gaps`` must reference a
    tracker thread. Drift without a tracker thread is just hidden
    drift — the whole point of the index is to make work findable."""
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    bad: list[str] = []
    for row in data["manifest_kinds"] + data["cli_commands"]:
        for gap in row.get("known_gaps") or []:
            if not gap.get("thread"):
                key = row.get("name") or row.get("path")
                bad.append(f"{key} → gap {gap.get('kind')!r} has no thread reference")
    assert not bad, (
        "known_gaps entries without thread references:\n  "
        + "\n  ".join(bad)
        + "\n\nFix: add the thread ID to the entry in surface_known_gaps.json "
        + "(file a tracker thread first if one doesn't exist), then "
        + "regenerate."
    )


def test_known_gap_kinds_are_recognized():
    """Catches typos in `kind` field (e.g. `missing_hadnler`). Allowed
    values are documented in docs/surface-index.md."""
    allowed = {
        "missing_handler",
        "missing_apply_order",
        "deprecated_pending_removal",
        "parallel_path",
    }
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    bad: list[str] = []
    for row in data["manifest_kinds"] + data["cli_commands"]:
        for gap in row.get("known_gaps") or []:
            kind = gap.get("kind")
            if kind not in allowed:
                key = row.get("name") or row.get("path")
                bad.append(f"{key} → unknown gap kind {kind!r}")
    assert not bad, (
        "known_gaps entries with unrecognized 'kind' field:\n  "
        + "\n  ".join(bad)
        + f"\n\nAllowed: {sorted(allowed)}. "
        + "If a new kind is genuinely needed, add it to the allowed "
        + "set in this test AND document it in docs/surface-index.md."
    )
