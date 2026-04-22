"""Every .schema.json file must be syntactically valid JSON Schema Draft 2020-12."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v1"
ALL_SCHEMA_FILES = list(SCHEMA_ROOT.rglob("*.schema.json"))


def test_at_least_one_schema_discovered() -> None:
    assert ALL_SCHEMA_FILES, "no *.schema.json files found under schema/v1/"


@pytest.mark.parametrize("schema_path", ALL_SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_is_valid_json(schema_path: Path) -> None:
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "$id" in data
    assert data["$schema"].startswith("https://json-schema.org/draft/2020-12")


@pytest.mark.parametrize("schema_path", ALL_SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_meta_validates(schema_path: Path) -> None:
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(data)


def test_version_file_is_semver() -> None:
    version = (SCHEMA_ROOT.parent / "VERSION").read_text(encoding="utf-8").strip()
    parts = version.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), f"VERSION not semver: {version!r}"
