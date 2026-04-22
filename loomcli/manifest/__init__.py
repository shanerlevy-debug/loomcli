"""Powerloom manifest subsystem.

Manifest format: kubectl-style YAML. Each document has:

    apiVersion: powerloom/v1
    kind: OU | Group | Skill | ...
    metadata:
        name: <resource-local name>
        ou_path: /dev-org/engineering   # for OU-scoped resources
        ...                              # kind-specific addressing
    spec:
        ...                              # kind-specific fields

Multi-document files (`---`-separated) and directories of YAML files
are both accepted.

This package owns:
  - `parser` — YAML → typed Resource list
  - `schema` — Pydantic models per kind + kind registry
  - `addressing` — OU-path resolution (/dev-org/engineering → uuid)
  - `planner` — current-state fetch + per-field diff
  - `applier` — dispatch table: plan action → API calls
"""
from loomcli.manifest.parser import (
    ManifestParseError,
    parse_manifest_text,
    parse_manifest_paths,
)
from loomcli.manifest.schema import (
    KINDS,
    Resource,
    get_kind_spec,
)

__all__ = [
    "ManifestParseError",
    "parse_manifest_text",
    "parse_manifest_paths",
    "KINDS",
    "Resource",
    "get_kind_spec",
]
