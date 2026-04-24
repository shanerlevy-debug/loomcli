"""Validate every reference-fleet manifest against the appropriate schema.

This test ensures the fleet stays shippable — if someone edits a manifest
by hand + breaks the schema, CI catches it before a bootstrap failure
on someone's prod control plane.

Also validates SKILL.md frontmatter for each archive directory (name
regex, description length) per the skill-storage contract.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver


REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_ROOT = REPO_ROOT / "examples" / "reference-fleet"
V1_SCHEMA_ROOT = REPO_ROOT / "schema" / "v1"
V2_SCHEMA_ROOT = REPO_ROOT / "schema" / "v2"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_store(root: Path) -> dict[str, dict]:
    store: dict[str, dict] = {}
    for p in root.rglob("*.schema.json"):
        doc = _load(p)
        store[p.resolve().as_uri()] = doc
        if "$id" in doc:
            store[doc["$id"]] = doc
    return store


def _make_v1_validator(kind: str) -> Draft202012Validator:
    kind_map = {"Skill": "skill", "Agent": "agent", "OU": "ou"}
    schema_path = V1_SCHEMA_ROOT / "kinds" / f"{kind_map[kind]}.schema.json"
    schema = _load(schema_path)
    resolver = RefResolver(
        base_uri=schema_path.resolve().as_uri(),
        referrer=schema,
        store=_build_store(V1_SCHEMA_ROOT),
    )
    return Draft202012Validator(schema, resolver=resolver)


def _make_v2_validator(kind: str) -> Draft202012Validator:
    kind_map = {
        "Skill": "stdlib/skill.schema.json",
        "Agent": "stdlib/agent.schema.json",
        "OU": "stdlib/ou.schema.json",
    }
    schema_path = V2_SCHEMA_ROOT / kind_map[kind]
    schema = _load(schema_path)
    resolver = RefResolver(
        base_uri=schema_path.resolve().as_uri(),
        referrer=schema,
        store=_build_store(V2_SCHEMA_ROOT),
    )
    return Draft202012Validator(schema, resolver=resolver)


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


V1_MANIFESTS = sorted((FLEET_ROOT / "v1.2.0").rglob("*.yaml"))
V2_MANIFESTS = sorted((FLEET_ROOT / "v2.0.0").rglob("*.yaml"))


def test_fleet_discovered():
    assert V1_MANIFESTS, "no v1.2.0 fleet manifests found"
    assert V2_MANIFESTS, "no v2.0.0 fleet manifests found"
    # Expect 2 OUs + 23 skills + 20 agents per version.
    # (22 original + 1 weave-interpreter added 2026-04-24)
    assert len(V1_MANIFESTS) == 2 + 23 + 20, len(V1_MANIFESTS)
    assert len(V2_MANIFESTS) == 2 + 23 + 20, len(V2_MANIFESTS)


@pytest.mark.parametrize("manifest", V1_MANIFESTS, ids=lambda p: str(p.relative_to(FLEET_ROOT)))
def test_v1_manifest_validates(manifest: Path) -> None:
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    validator = _make_v1_validator(doc["kind"])
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"at {'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors
    )


@pytest.mark.parametrize("manifest", V2_MANIFESTS, ids=lambda p: str(p.relative_to(FLEET_ROOT)))
def test_v2_manifest_validates(manifest: Path) -> None:
    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    validator = _make_v2_validator(doc["kind"])
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"at {'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors
    )


# ---------------------------------------------------------------------------
# SKILL.md frontmatter validation
# ---------------------------------------------------------------------------


SKILL_ARCHIVE_DIRS = sorted(
    d for d in (FLEET_ROOT / "skill-archives").iterdir() if d.is_dir()
)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
RESERVED = {"anthropic", "claude"}


@pytest.mark.parametrize(
    "archive_dir", SKILL_ARCHIVE_DIRS, ids=lambda p: p.name
)
def test_skill_archive_has_valid_skillmd(archive_dir: Path) -> None:
    skill_md = archive_dir / "SKILL.md"
    assert skill_md.exists(), f"{archive_dir.name}/SKILL.md missing"
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith(
        "---"
    ), f"{archive_dir.name}/SKILL.md must start with YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, (
        f"{archive_dir.name}/SKILL.md frontmatter malformed (no closing ---)"
    )
    frontmatter = yaml.safe_load(parts[1])
    assert isinstance(frontmatter, dict), "frontmatter must be a mapping"

    name = frontmatter.get("name")
    assert name, "missing 'name' field"
    assert NAME_RE.match(name), f"name {name!r} invalid — must match {NAME_RE.pattern}"
    assert name.lower() not in RESERVED, f"name {name!r} is reserved"

    description = (frontmatter.get("description") or "").strip()
    assert description, "missing 'description' field"
    assert len(description) <= 1024, (
        f"description too long ({len(description)} chars, max 1024)"
    )


def test_skill_archive_names_match_manifests() -> None:
    """Every skill manifest's metadata.name must have a matching
    skill-archives/<name>/SKILL.md directory. No orphan manifests,
    no orphan archives."""
    manifest_names = set()
    for m in (FLEET_ROOT / "v2.0.0" / "skills").glob("*.yaml"):
        doc = yaml.safe_load(m.read_text(encoding="utf-8"))
        manifest_names.add(doc["metadata"]["name"])
    archive_names = {d.name for d in SKILL_ARCHIVE_DIRS}
    orphan_manifests = manifest_names - archive_names
    orphan_archives = archive_names - manifest_names
    assert not orphan_manifests, (
        f"manifests without archives: {sorted(orphan_manifests)}"
    )
    assert not orphan_archives, (
        f"archives without manifests: {sorted(orphan_archives)}"
    )


# ---------------------------------------------------------------------------
# Shape parity — v1.2.0 and v2.0.0 manifests should differ ONLY in apiVersion
# ---------------------------------------------------------------------------


def _matching_pair(v1_path: Path) -> Path:
    """Return the v2.0.0 counterpart for a v1.2.0 manifest path."""
    rel = v1_path.relative_to(FLEET_ROOT / "v1.2.0")
    return FLEET_ROOT / "v2.0.0" / rel


@pytest.mark.parametrize("v1_path", V1_MANIFESTS, ids=lambda p: p.name)
def test_v1_v2_shape_parity(v1_path: Path) -> None:
    """Every v1.2.0 manifest has a v2.0.0 counterpart that differs
    ONLY by apiVersion. This enforces the v056 migration story at
    the fleet-artifact level."""
    v2_path = _matching_pair(v1_path)
    assert v2_path.exists(), f"no v2 counterpart for {v1_path.name}"

    v1_doc = yaml.safe_load(v1_path.read_text(encoding="utf-8"))
    v2_doc = yaml.safe_load(v2_path.read_text(encoding="utf-8"))

    # Normalize apiVersion then compare rest-of-document
    v1_doc.pop("apiVersion", None)
    v2_doc.pop("apiVersion", None)
    assert v1_doc == v2_doc, (
        f"{v1_path.name}: v1.2.0 and v2.0.0 manifests differ beyond apiVersion"
    )
