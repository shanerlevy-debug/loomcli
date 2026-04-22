"""Pytest fixtures + helpers shared across schema tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schema" / "v1"
EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "minimal"
KIND_SCHEMAS_DIR = SCHEMA_ROOT / "kinds"


@pytest.fixture(scope="session")
def kind_schema_paths() -> list[Path]:
    return sorted(KIND_SCHEMAS_DIR.glob("*.schema.json"))


@pytest.fixture(scope="session")
def loaded_kind_schemas(kind_schema_paths: list[Path]) -> dict[str, dict]:
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in kind_schema_paths}


@pytest.fixture(scope="session")
def dialect_schema() -> dict:
    return json.loads((SCHEMA_ROOT / "powerloom-dialect.schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def common_schema() -> dict:
    return json.loads((SCHEMA_ROOT / "common.schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def bundle_schema() -> dict:
    return json.loads((SCHEMA_ROOT / "powerloom.v1.bundle.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def example_paths() -> list[Path]:
    return sorted(EXAMPLES_ROOT.glob("*.yaml"))
