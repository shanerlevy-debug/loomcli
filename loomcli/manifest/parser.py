"""Manifest parser — YAML → typed Resource list.

Accepts either:
  - a single YAML file (multi-document via `---` supported)
  - a directory (walked recursively for *.yaml / *.yml files)
  - a list of files / directories / "-" for stdin

Output: a list of `Resource`s, preserving document order. The order
matters: `apply` uses it as a tie-break when dependency ordering is
ambiguous (e.g., two OUs with no explicit parent/child relationship).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from loomcli.manifest.jsonschema_validator import (
    SchemaValidationError,
    validate_doc,
)
from loomcli.manifest.schema import API_VERSION, KINDS, Resource


class ManifestParseError(Exception):
    """Raised on any YAML / schema error. Carries a human-readable
    location string so CLI output points admins at the offending
    document."""

    def __init__(self, message: str, *, source: str | None = None):
        super().__init__(f"{source}: {message}" if source else message)
        self.source = source


def parse_manifest_text(text: str, *, source: str = "<inline>") -> list[Resource]:
    """Parse a single YAML string (potentially multi-doc). Used by
    tests + by the CLI's `apply -f -` stdin path."""
    resources: list[Resource] = []
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        raise ManifestParseError(f"YAML parse error: {e}", source=source) from e

    for idx, doc in enumerate(docs, start=1):
        if doc is None:
            # Empty document (trailing `---`, whitespace-only block) —
            # skip silently. Matches kubectl behavior.
            continue
        res = _parse_one_doc(doc, source=source, doc_index=idx)
        resources.append(res)
    return resources


def parse_manifest_paths(paths: Iterable[str | Path]) -> list[Resource]:
    """Parse one or more files / directories.

    Semantics:
      - "-" reads from stdin
      - a directory is walked recursively for *.yaml, *.yml
      - a file is parsed directly
    """
    resources: list[Resource] = []
    for p in paths:
        if str(p) == "-":
            resources.extend(parse_manifest_text(sys.stdin.read(), source="<stdin>"))
            continue
        path = Path(p)
        if path.is_dir():
            for file in sorted(_walk_yaml(path)):
                resources.extend(parse_manifest_text(file.read_text(encoding="utf-8"), source=str(file)))
        elif path.is_file():
            resources.extend(parse_manifest_text(path.read_text(encoding="utf-8"), source=str(path)))
        else:
            raise ManifestParseError(
                f"path does not exist or is not a file/directory: {path}",
            )
    return resources


def _walk_yaml(root: Path) -> list[Path]:
    out: list[Path] = []
    for ext in ("*.yaml", "*.yml"):
        out.extend(root.rglob(ext))
    return out


def _parse_one_doc(doc: dict, *, source: str, doc_index: int) -> Resource:
    if not isinstance(doc, dict):
        raise ManifestParseError(
            f"doc {doc_index}: top-level must be a mapping, got {type(doc).__name__}",
            source=source,
        )

    # Cheap structural preconditions for a legible error message before we
    # hand off to the schema validator.
    kind = doc.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ManifestParseError(
            f"doc {doc_index}: missing or empty `kind`",
            source=source,
        )

    # First pass: authoritative JSON Schema validation against the loomcli
    # submodule. This is the source-of-truth for wire-format correctness.
    try:
        validate_doc(doc, kind=kind)
    except SchemaValidationError as e:
        raise ManifestParseError(
            f"doc {doc_index} ({kind}): schema validation failed:\n  "
            + "\n  ".join(e.errors),
            source=source,
        ) from e

    # Second pass: Pydantic conversion for typed downstream access. Schema
    # validation already caught wire-format issues, so Pydantic failures here
    # indicate Pydantic drift from the schema (should be caught in CI).
    spec_wiring = KINDS.get(kind)
    if spec_wiring is None:
        # Kind is in the schema but not in the CLI's KINDS registry — e.g.
        # Workflow is schema-defined ahead of CLI implementation.
        raise ManifestParseError(
            f"doc {doc_index}: kind {kind!r} is schema-valid but not yet "
            f"implemented in the CLI. Known: {sorted(KINDS)}",
            source=source,
        )

    metadata_raw = doc.get("metadata") or {}
    spec_raw = doc.get("spec") or {}

    try:
        metadata = spec_wiring.metadata_model.model_validate(metadata_raw)
    except ValidationError as e:
        raise ManifestParseError(
            f"doc {doc_index} ({kind}): pydantic metadata drift (bug — schema "
            f"passed but model rejected): {_format_pydantic_errors(e)}",
            source=source,
        ) from e
    try:
        spec = spec_wiring.spec_model.model_validate(spec_raw)
    except ValidationError as e:
        raise ManifestParseError(
            f"doc {doc_index} ({kind}): pydantic spec drift (bug — schema "
            f"passed but model rejected): {_format_pydantic_errors(e)}",
            source=source,
        ) from e

    return Resource(
        kind=kind,
        metadata=metadata,
        spec=spec,
        source_file=source,
        doc_index=doc_index,
    )


def _format_pydantic_errors(exc: ValidationError) -> str:
    """Flatten Pydantic's nested error list into a readable one-liner."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def dump_resource_to_yaml(r: Resource) -> str:
    """Inverse of parse_manifest_text for a single Resource. Used by
    `weave import` to emit a round-trippable manifest fragment.
    """
    return yaml.safe_dump(
        {
            "apiVersion": API_VERSION,
            "kind": r.kind,
            "metadata": r.metadata.model_dump(exclude_none=False),
            "spec": r.spec.model_dump(exclude_none=False),
        },
        sort_keys=False,
    )


# Placate tools that complain about unused `io` import if we drop stdin.
_ = io
