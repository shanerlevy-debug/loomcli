"""Runtime JSON Schema validation against the authoritative loomcli
schema (inline at `schema/v1/` in this repo).

This is the first validation pass in the manifest parser. Pydantic
models in `schema.py` are downstream of this — they exist for typed
access in the planner/applier, not as the validation source-of-truth.

Why jsonschema here: the schema is co-located with the CLI in this
repo and consumed by the server-side Pydantic codegen (Powerloom
monorepo), IDE yaml-language-server config, and LLM-authored manifest
generation. Using the same schema at CLI runtime guarantees a single
source of truth across all four — drift-impossible by construction.

Industry-standard pattern: kubectl, Helm, Argo, and Flux all validate
user-submitted manifests against a published JSON Schema (or CRD) at
runtime. Pydantic/struct-tag validation is an implementation detail
downstream of the wire-format schema.
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


# ---------------------------------------------------------------------------
# Kind → schema filename mapping. Not pure kebab-casing (MCP stays "mcp",
# AgentMCPServer → "agent-mcp-server"), so it's explicit.
# ---------------------------------------------------------------------------
KIND_TO_SCHEMA_FILE = {
    "OU": "ou.schema.json",
    "Group": "group.schema.json",
    "GroupMembership": "group-membership.schema.json",
    "RoleBinding": "role-binding.schema.json",
    "Skill": "skill.schema.json",
    "SkillAccessGrant": "skill-access-grant.schema.json",
    "Credential": "credential.schema.json",
    "MCPServerRegistration": "mcp-server-registration.schema.json",
    "MCPDeployment": "mcp-deployment.schema.json",
    "Agent": "agent.schema.json",
    "AgentSkill": "agent-skill.schema.json",
    "AgentMCPServer": "agent-mcp-server.schema.json",
    "Workflow": "workflow.schema.json",  # Phase 14 kind — schema ships ahead of CLI impl.
}


class SchemaNotFoundError(RuntimeError):
    """Raised when the schema directory can't be located."""


class SchemaValidationError(Exception):
    """Raised by `validate_doc` when a document fails JSON Schema validation.
    Carries a human-readable list of error locations."""

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------
def _find_schema_dir() -> Path:
    """Locate the `schema/v1/` directory at runtime.

    Precedence:
      1. `$WEAVE_SCHEMA_DIR` override (for tests / non-standard layouts).
      2. PyInstaller bundle: `sys._MEIPASS/schema/v1`.
      3. Installed wheel: `loomcli/_bundled_schema/v1` (force-included by
         pyproject.toml's `[tool.hatch.build.targets.wheel.force-include]`).
      4. Dev path: `<repo>/schema/v1` resolved relative to this file
         (`loomcli/manifest/<file>` → parents[2] = repo root).
    """
    override = os.environ.get("WEAVE_SCHEMA_DIR")
    if override:
        p = Path(override)
        if p.is_dir():
            return p
        raise SchemaNotFoundError(
            f"WEAVE_SCHEMA_DIR={override!r} does not exist or is not a directory."
        )

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "schema" / "v1"
        if p.is_dir():
            return p

    # Installed wheel case: the pyproject force-include drops the schema under
    # loomcli/_bundled_schema/v1. For an editable install this directory
    # won't exist (falls through to the dev path below).
    here = Path(__file__).resolve().parent
    bundled = here.parent / "_bundled_schema" / "v1"
    if bundled.is_dir():
        return bundled

    # Dev path: loomcli/manifest/<this file> → parents[2] == repo root.
    repo = Path(__file__).resolve().parents[2]
    p = repo / "schema" / "v1"
    if p.is_dir():
        return p

    raise SchemaNotFoundError(
        "loomcli schema not found. The JSON Schema should live at "
        "`schema/v1/` in the repo root, or be installed alongside the "
        "`loomcli` wheel. For an editable install from source, ensure "
        "the `schema/v1/` directory exists in the repo. Alternatively, "
        "set WEAVE_SCHEMA_DIR to an alternate schema/v1 directory."
    )


# ---------------------------------------------------------------------------
# Registry + validator caching
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _build_registry() -> tuple[Registry, Path]:
    """Load every schema file into a referencing.Registry so `$ref`
    resolution works across files. Cached — built once per process.

    Every schema has an absolute `$id` like
    `https://schema.powerloom.app/v1/common.schema.json`. Per-kind schemas
    reference common defs via `../common.schema.json#/$defs/…`, which
    resolves against the kind's own `$id` to an absolute URL that finds
    the registered common schema in the registry.
    """
    schema_dir = _find_schema_dir()
    registry = Registry()
    common = json.loads((schema_dir / "common.schema.json").read_text(encoding="utf-8"))
    registry = registry.with_resource(
        common["$id"],
        Resource.from_contents(common, default_specification=DRAFT202012),
    )
    for name in KIND_TO_SCHEMA_FILE.values():
        doc = json.loads((schema_dir / "kinds" / name).read_text(encoding="utf-8"))
        registry = registry.with_resource(
            doc["$id"],
            Resource.from_contents(doc, default_specification=DRAFT202012),
        )
    return registry, schema_dir


@lru_cache(maxsize=32)
def _validator_for(kind: str) -> jsonschema.Draft202012Validator:
    registry, schema_dir = _build_registry()
    fname = KIND_TO_SCHEMA_FILE.get(kind)
    if fname is None:
        raise SchemaValidationError([f"unknown kind: {kind!r}"])
    schema = json.loads(
        (schema_dir / "kinds" / fname).read_text(encoding="utf-8")
    )
    return jsonschema.Draft202012Validator(schema, registry=registry)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def validate_doc(doc: dict, *, kind: str) -> None:
    """Validate a parsed manifest document against the authoritative
    JSON Schema for its kind. Raises `SchemaValidationError` on failure.

    Called by `parser._parse_one_doc` before the Pydantic pass. If this
    succeeds, the document is guaranteed to match the wire-format schema
    — the only remaining work is Pydantic type conversion for downstream
    typed access."""
    validator = _validator_for(kind)
    errs = sorted(validator.iter_errors(doc), key=lambda e: e.absolute_path)
    if not errs:
        return
    messages = []
    for e in errs:
        loc = ".".join(str(p) for p in e.absolute_path) or "<root>"
        messages.append(f"{loc}: {e.message}")
    raise SchemaValidationError(messages)


def schema_source_info() -> str:
    """One-line human-readable description of where the schema was
    loaded from. Useful for `weave --version` or diagnostic output."""
    try:
        _, schema_dir = _build_registry()
        return f"schema: {schema_dir}"
    except SchemaNotFoundError as e:
        return f"schema: NOT FOUND ({e})"
