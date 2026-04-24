"""Validate every generated reference-fleet manifest against the
appropriate schema (v1 kind schemas for v1.2.0 manifests, v2 stdlib
schemas for v2.0.0 manifests).

Run from loomcli repo root:
    python scripts/validate_reference_fleet.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, RefResolver


REPO_ROOT = Path(__file__).resolve().parents[1]
FLEET_ROOT = REPO_ROOT / "examples" / "reference-fleet"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_validator_v1(kind: str) -> Draft202012Validator:
    kind_map = {"Skill": "skill", "Agent": "agent", "OU": "ou"}
    fname = f"{kind_map[kind]}.schema.json"
    schema_path = REPO_ROOT / "schema" / "v1" / "kinds" / fname
    schema = _load(schema_path)
    store = {}
    for p in (REPO_ROOT / "schema" / "v1").rglob("*.schema.json"):
        d = _load(p)
        store[p.resolve().as_uri()] = d
        if "$id" in d:
            store[d["$id"]] = d
    resolver = RefResolver(
        base_uri=schema_path.resolve().as_uri(), referrer=schema, store=store
    )
    return Draft202012Validator(schema, resolver=resolver)


def _make_validator_v2(kind: str) -> Draft202012Validator:
    kind_map = {
        "Skill": "stdlib/skill.schema.json",
        "Agent": "stdlib/agent.schema.json",
        "OU": "stdlib/ou.schema.json",
    }
    schema_path = REPO_ROOT / "schema" / "v2" / kind_map[kind]
    schema = _load(schema_path)
    store = {}
    for p in (REPO_ROOT / "schema" / "v2").rglob("*.schema.json"):
        d = _load(p)
        store[p.resolve().as_uri()] = d
        if "$id" in d:
            store[d["$id"]] = d
    resolver = RefResolver(
        base_uri=schema_path.resolve().as_uri(), referrer=schema, store=store
    )
    return Draft202012Validator(schema, resolver=resolver)


def main() -> int:
    os.chdir(REPO_ROOT)
    total = 0
    failures: list[str] = []

    for version_dir, make_validator in (
        ("v1.2.0", _make_validator_v1),
        ("v2.0.0", _make_validator_v2),
    ):
        for subdir in ("ous", "skills", "agents"):
            for yaml_path in sorted(
                (FLEET_ROOT / version_dir / subdir).glob("*.yaml")
            ):
                doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                kind = doc["kind"]
                try:
                    validator = make_validator(kind)
                except KeyError:
                    failures.append(
                        f"{yaml_path.relative_to(REPO_ROOT)}: unknown kind {kind!r}"
                    )
                    continue
                errors = sorted(
                    validator.iter_errors(doc), key=lambda e: list(e.path)
                )
                total += 1
                if errors:
                    rel = yaml_path.relative_to(REPO_ROOT)
                    failures.append(f"FAIL {rel}:")
                    for err in errors:
                        loc = ".".join(str(p) for p in err.path) or "<root>"
                        failures.append(f"    at {loc}: {err.message}")

    if failures:
        print(f"Validated {total} manifests; {len([f for f in failures if f.startswith('FAIL')])} failures:")
        for f in failures:
            print(f)
        return 1

    print(f"All {total} reference-fleet manifests validate cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
