#!/usr/bin/env python3
"""Generate ``surface_index.json`` — the structured enumeration of every
"thing" loomcli exposes (manifest kinds + CLI commands), with state flags.

Phase 1 of the single-path-invariant arc (Powerloom thread c77b8922,
sprint 9203e7d3, child thread ed4058c4). The conformance test from
PR #84 (Phase 0) is now a consumer of this artifact rather than its
own data structure — single source of truth for "what's in this CLI."

## Usage

    python scripts/build_surface_index.py [--check]

Without ``--check``, regenerates ``surface_index.json`` in place.
With ``--check``, regenerates to memory and exits non-zero if the
result differs from the committed file (CI drift gate).

## What it walks

1. ``schema/v1/kinds/*.schema.json`` — every JSON Schema kind file
   (skips ``common.schema.json`` since it has no ``kind`` const).
2. ``loomcli.manifest.schema.KINDS`` — every kind registered in the
   Pydantic registry.
3. ``loomcli.manifest.applier._APPLY_ORDER`` — every kind with an
   explicit ordering bucket.
4. ``loomcli.manifest.handlers._HANDLERS`` — every handler that's
   been ``register()``'d at module-import time.
5. ``loomcli.cli:app`` — the root Typer instance, walked recursively
   to enumerate every leaf command (``weave thread create``,
   ``weave sprint add-thread``, etc.).

## Hand annotations

Mechanical enumeration only catches mechanical drift. For hand-
annotated gaps (parallel-path duplications, deprecations, "schema
shipped but handler intentionally deferred to thread X"), we read
``surface_known_gaps.json`` and merge its entries into the generated
baseline. Each annotation MUST reference a tracker thread so an agent
reading the index can find the work.

## Output shape

See ``docs/architecture/surface-index.md`` for the canonical schema
description. Briefly::

    {
      "$schema_version": "1",
      "repo": "loomcli",
      "manifest_kinds": [{name, schema_path, in_kinds_registry,
                          handler_class, apply_order, category,
                          known_gaps[]}],
      "cli_commands": [{path, module, summary, parent, deprecated,
                        known_gaps[]}]
    }

Generated rows are sorted (kinds by name, commands by path) so diffs
are stable across runs even if dict iteration order differs.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
SURFACE_INDEX_PATH = REPO_ROOT / "surface_index.json"
KNOWN_GAPS_PATH = REPO_ROOT / "surface_known_gaps.json"
SCHEMA_DIR = REPO_ROOT / "schema" / "v1" / "kinds"

# Force the local repo onto sys.path BEFORE any loomcli import. Without
# this, `python scripts/build_surface_index.py` puts only the scripts/
# directory at sys.path[0] and Python falls through to site-packages
# for `import loomcli` — which means the script reads from whatever
# version is pipx-installed, not the source tree being edited. That's
# exactly the kind of "looks fine, lies about reality" drift this
# index is supposed to prevent.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Manifest-kind enumeration
# ---------------------------------------------------------------------------
def _walk_schema_kinds() -> dict[str, Path]:
    """Map ``kind name -> schema file path`` for every schema in
    ``schema/v1/kinds/*.schema.json`` that declares a ``kind`` const."""
    if not SCHEMA_DIR.is_dir():
        raise SystemExit(f"schema dir not found: {SCHEMA_DIR}")
    kinds: dict[str, Path] = {}
    for f in sorted(SCHEMA_DIR.glob("*.schema.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{f.name}: invalid JSON: {e}") from e
        kind_const = (
            data.get("properties", {}).get("kind", {}).get("const")
        )
        if isinstance(kind_const, str) and kind_const:
            kinds[kind_const] = f.relative_to(REPO_ROOT)
    return kinds


def _build_manifest_kinds(known_gaps: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the ``manifest_kinds`` rows by joining the four registries.

    Importing the registries is what gives us ``_HANDLERS`` populated —
    handlers self-register at module import time via the ``register()``
    helper. Importing ``handlers`` triggers all the ``register(...)``
    calls at module scope.
    """
    # Import order matters: schema defines KINDS, applier defines _APPLY_ORDER,
    # handlers does the registrations.
    schema_mod = importlib.import_module("loomcli.manifest.schema")
    applier_mod = importlib.import_module("loomcli.manifest.applier")
    handlers_mod = importlib.import_module("loomcli.manifest.handlers")

    KINDS: dict[str, Any] = schema_mod.KINDS  # type: ignore[attr-defined]
    APPLY_ORDER: dict[str, int] = applier_mod._APPLY_ORDER  # type: ignore[attr-defined]
    HANDLERS: dict[str, Any] = handlers_mod._HANDLERS  # type: ignore[attr-defined]

    schema_kinds = _walk_schema_kinds()
    all_kind_names = sorted(set(schema_kinds) | set(KINDS) | set(HANDLERS) | set(APPLY_ORDER))

    rows: list[dict[str, Any]] = []
    kind_gaps_lookup = (known_gaps or {}).get("manifest_kinds", {})
    for name in all_kind_names:
        spec = KINDS.get(name)
        handler = HANDLERS.get(name)
        apply_order = APPLY_ORDER.get(name)
        schema_path = schema_kinds.get(name)
        gap_entry = kind_gaps_lookup.get(name) or {}
        rows.append(
            {
                "name": name,
                "schema_path": str(schema_path).replace("\\", "/") if schema_path else None,
                "in_kinds_registry": spec is not None,
                "handler_class": _qualname(handler) if handler is not None else None,
                "apply_order": apply_order,
                "category": getattr(spec, "category", None) if spec else None,
                "known_gaps": list(gap_entry.get("gaps") or []),
            }
        )
    return rows


def _qualname(obj: Any) -> str:
    cls = obj.__class__
    return f"{cls.__module__}:{cls.__qualname__}"


# ---------------------------------------------------------------------------
# CLI-command enumeration (typer introspection)
# ---------------------------------------------------------------------------
def _build_cli_commands(known_gaps: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk the root Typer instance recursively. Yields one row per
    leaf command (``weave thread create``) and one row per group
    (``weave thread``) — groups are kept so the diff reflects "this
    sub-app exists" even when none of its leaves changed."""
    cli_mod = importlib.import_module("loomcli.cli")
    app = cli_mod.app  # the root typer.Typer
    rows: list[dict[str, Any]] = []
    cmd_gaps_lookup = (known_gaps or {}).get("cli_commands", {})

    def _module_qualname(callback: Any) -> str | None:
        if callback is None:
            return None
        mod = getattr(callback, "__module__", None)
        qn = getattr(callback, "__qualname__", None) or getattr(callback, "__name__", None)
        if mod and qn:
            return f"{mod}:{qn}"
        return None

    def _summary(item: Any, callback: Any) -> str | None:
        # Typer's `help=` arg on @app.command(...) lands as item.help.
        h = getattr(item, "help", None)
        if isinstance(h, str) and h.strip():
            return h.strip()
        # Fall back to the callback's docstring first line.
        if callback is not None and callback.__doc__:
            return callback.__doc__.strip().splitlines()[0]
        return None

    def _walk(typer_app: Any, prefix: list[str]) -> None:
        # Direct commands on this typer.
        for cmd in getattr(typer_app, "registered_commands", []) or []:
            name = cmd.name or (cmd.callback.__name__ if cmd.callback else "<anon>")
            path = " ".join(prefix + [name])
            rows.append(
                {
                    "path": path,
                    "module": _module_qualname(cmd.callback),
                    "summary": _summary(cmd, cmd.callback),
                    "parent": " ".join(prefix) if prefix else None,
                    "deprecated": bool(getattr(cmd, "deprecated", False)),
                    "is_group": False,
                    "known_gaps": list((cmd_gaps_lookup.get(path) or {}).get("gaps") or []),
                }
            )
        # Sub-apps registered via app.add_typer(...).
        for group in getattr(typer_app, "registered_groups", []) or []:
            name = group.name or "<anon>"
            path = " ".join(prefix + [name])
            rows.append(
                {
                    "path": path,
                    "module": None,
                    "summary": _summary(group, None),
                    "parent": " ".join(prefix) if prefix else None,
                    "deprecated": bool(getattr(group, "deprecated", False)),
                    "is_group": True,
                    "known_gaps": list((cmd_gaps_lookup.get(path) or {}).get("gaps") or []),
                }
            )
            _walk(group.typer_instance, prefix + [name])

    _walk(app, ["weave"])
    rows.sort(key=lambda r: r["path"])
    return rows


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------
def build_surface_index() -> dict[str, Any]:
    known_gaps: dict[str, Any] = {}
    if KNOWN_GAPS_PATH.exists():
        known_gaps = json.loads(KNOWN_GAPS_PATH.read_text(encoding="utf-8"))
    return {
        "$schema_version": "1",
        "repo": "loomcli",
        "manifest_kinds": _build_manifest_kinds(known_gaps),
        "cli_commands": _build_cli_commands(known_gaps),
    }


def write_surface_index(index: dict[str, Any]) -> None:
    """Write with a stable trailing newline + 2-space indent so diffs
    are minimal."""
    text = json.dumps(index, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    SURFACE_INDEX_PATH.write_text(text, encoding="utf-8")


def check_surface_index() -> int:
    """Return 0 if the committed file matches the regenerated content,
    1 otherwise. Prints a short diff hint on failure."""
    if not SURFACE_INDEX_PATH.exists():
        print(
            f"surface_index.json missing at {SURFACE_INDEX_PATH}.\n"
            f"Run: python scripts/build_surface_index.py",
            file=sys.stderr,
        )
        return 1
    expected = json.dumps(build_surface_index(), indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    actual = SURFACE_INDEX_PATH.read_text(encoding="utf-8")
    if expected == actual:
        return 0
    print(
        "surface_index.json is out of date.\n"
        "The committed file disagrees with what the emitter generates from the\n"
        "current code. Either:\n"
        "  - Regenerate: python scripts/build_surface_index.py\n"
        "  - Or fix the underlying drift (a kind without handler, a CLI command\n"
        "    that was renamed, a known-gap that's been fixed in code but not\n"
        "    cleared from surface_known_gaps.json, etc.)",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare against the committed surface_index.json; non-zero exit on drift.",
    )
    args = parser.parse_args()
    if args.check:
        return check_surface_index()
    write_surface_index(build_surface_index())
    print(f"Wrote {SURFACE_INDEX_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
