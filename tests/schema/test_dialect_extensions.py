"""x-powerloom-* keywords used in kind schemas must conform to the dialect meta-schema."""
from __future__ import annotations

from typing import Any, Iterator

import pytest
from jsonschema import Draft202012Validator


DIALECT_KEYWORDS = {
    "x-powerloom-ref",
    "x-powerloom-server-field",
    "x-powerloom-immutable",
    "x-powerloom-reconciler-hint",
    "x-powerloom-secret-ref",
    "x-powerloom-default-from-server",
    "x-powerloom-auxiliary",
    "x-powerloom-example",
    "x-powerloom-tier-availability",
    "x-powerloom-apply-order",
}


def _walk(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_dialect_schema_is_valid(dialect_schema: dict) -> None:
    Draft202012Validator.check_schema(dialect_schema)


def test_every_dialect_keyword_used_is_defined(loaded_kind_schemas: dict[str, dict]) -> None:
    used: set[str] = set()
    for schema in loaded_kind_schemas.values():
        for k, _ in _walk(schema):
            if isinstance(k, str) and k.startswith("x-powerloom-"):
                used.add(k)
    undefined = used - DIALECT_KEYWORDS
    assert not undefined, f"unknown x-powerloom-* keywords used in kind schemas: {sorted(undefined)}"


def test_dialect_keyword_usage_validates(dialect_schema: dict, loaded_kind_schemas: dict[str, dict]) -> None:
    """Each x-powerloom-* keyword's value must validate against its $defs entry in the dialect meta-schema."""
    defs = dialect_schema["$defs"]
    for schema_name, schema in loaded_kind_schemas.items():
        for k, v in _walk(schema):
            if not (isinstance(k, str) and k.startswith("x-powerloom-")):
                continue
            if k not in defs:
                continue
            validator = Draft202012Validator(defs[k])
            errors = list(validator.iter_errors(v))
            assert not errors, f"{schema_name}: {k} = {v!r} failed dialect validation: {[e.message for e in errors]}"


@pytest.mark.parametrize("kw", sorted(DIALECT_KEYWORDS))
def test_each_dialect_keyword_documented(kw: str) -> None:
    from pathlib import Path

    docs = (Path(__file__).resolve().parents[2] / "schema" / "v1" / "dialect-docs.md").read_text(encoding="utf-8")
    assert f"`{kw}`" in docs, f"{kw} not documented in dialect-docs.md"
