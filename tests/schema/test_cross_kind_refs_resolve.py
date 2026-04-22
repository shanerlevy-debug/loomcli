"""Every x-powerloom-ref `kind` target must be a known kind (present in kinds/*.schema.json or an enum member the dialect meta-schema enumerates as a valid ref target)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v1"

# Kinds that don't ship as their own *.schema.json in v1.0.0 but are
# valid ref targets (e.g. Principal is a virtual kind that resolves to
# User/Group/Agent; CRD/AuxiliaryClass/AttributeDefinition land in
# Phase 15.4–15.5 but refs to them are allowed in v1 schemas now).
VIRTUAL_KINDS = {"Principal", "CustomResourceDefinition", "AuxiliaryClass", "AttributeDefinition"}


def _walk(node: Any) -> Iterator[tuple[list[str], dict]]:
    stack: list[tuple[list[str], Any]] = [([], node)]
    while stack:
        path, cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k == "x-powerloom-ref" and isinstance(v, dict):
                    yield path + [k], v
                stack.append((path + [str(k)], v))
        elif isinstance(cur, list):
            for i, item in enumerate(cur):
                stack.append((path + [str(i)], item))


def test_every_ref_target_is_known() -> None:
    concrete_kinds = set()
    for p in (SCHEMA_ROOT / "kinds").glob("*.schema.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        kind_const = data.get("properties", {}).get("kind", {}).get("const")
        if kind_const:
            concrete_kinds.add(kind_const)

    valid_targets = concrete_kinds | VIRTUAL_KINDS

    problems: list[str] = []
    for schema_path in (SCHEMA_ROOT / "kinds").glob("*.schema.json"):
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        for path, ref in _walk(data):
            target = ref.get("kind")
            if target and target not in valid_targets:
                problems.append(f"{schema_path.name} at {'/'.join(path)}: unknown ref target {target!r}")

    assert not problems, "Unknown x-powerloom-ref targets:\n  " + "\n  ".join(problems)


def test_bundle_oneof_covers_every_kind_schema() -> None:
    bundle = json.loads((SCHEMA_ROOT / "powerloom.v1.bundle.json").read_text(encoding="utf-8"))
    referenced = {item["$ref"].rsplit("/", 1)[-1] for item in bundle["oneOf"]}
    kind_files = {p.name for p in (SCHEMA_ROOT / "kinds").glob("*.schema.json")}
    assert referenced == kind_files, f"bundle oneOf drift — missing: {kind_files - referenced}, extra: {referenced - kind_files}"
