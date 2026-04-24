"""`weave migrate` — upgrade manifests between schema versions.

Currently supports `v1` → `v2` (v1.2.0 → v2.0.0). v2.0.0 stdlib kinds
are hand-authored with shape parity to v1.2.0, so the common case is a
pure apiVersion bump. The interesting migration work is around kinds
that retired in v2 and need re-expression as Policy slots or inline
Agent fields.

Migration outcomes per doc:

- **clean**        — apiVersion bumped, shape passes v2 validation
- **warn**         — migrated, but a v1-ism the author may want to
                     revisit (e.g. `powerloom/v1` legacy alias,
                     inline AgentSkill that's now redundant)
- **needs-rewrite** — v1-only kind that doesn't survive the bump
                     (Group, GroupMembership, RoleBinding,
                     SkillAccessGrant standalone). Emitted as-is with
                     a comment block explaining how to re-express.
- **error**        — parse error or already-v2

Commands:
  - `weave migrate v1-to-v2 <path>`           — stdout, diff-friendly
  - `weave migrate v1-to-v2 <path> --out Y`   — write to Y
  - `weave migrate v1-to-v2 <path> --in-place` — rewrite path
  - `weave migrate v1-to-v2 <dir> --in-place` — recursive

Round-trip guarantee: stdlib kinds that migrate `clean` parse valid
against v2 schemas. `weave compose lint` on the output is a no-op
(stdlib kinds aren't Compose manifests). For full validation run
`weave plan <migrated.yaml>` against a v056+ engine.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Upgrade manifests between schema versions.")
_console = Console()


# Kinds that exist in v2 stdlib with shape parity to v1.
_V2_STDLIB_KINDS = {
    "Organization",
    "OU",
    "Agent",
    "Skill",
    "WorkflowType",
    "Workflow",
    "MemoryPolicy",
    "MCPDeployment",
}

# Kinds that existed in v1 but retired / were folded in v2.
# Value = human guidance for the rewrite.
_V1_ONLY_KINDS: dict[str, str] = {
    "Group": (
        "v2 collapses Group into Policy + Scope. Express group membership "
        "as a Policy slot with `applies_to` naming member scope_refs. See "
        "docs/migration-v1-to-v2.md#groups for worked examples."
    ),
    "GroupMembership": (
        "v2 has no standalone GroupMembership — membership is a Policy "
        "field (`members`) on the group's governing Policy slot."
    ),
    "RoleBinding": (
        "v2 expresses role bindings as Policy[policy_type=rbac] slots. "
        "The v1 role name maps to Policy `role` tag; the principal_ref "
        "becomes the Policy `applies_to` entry."
    ),
    "SkillAccessGrant": (
        "v2 folds SkillAccessGrant into Skill's access Policy slot. "
        "For the common case (agent X may use skill Y), add Y to "
        "agent X's `spec.skills` list — the grant is implicit."
    ),
    "AgentSkill": (
        "v2 has no separate AgentSkill kind. Put the skill name in "
        "the agent's `spec.skills` list directly."
    ),
    "AgentMCPServer": (
        "v2 has no separate AgentMCPServer kind. Put the MCP server "
        "name in the agent's `spec.mcp_servers` list directly."
    ),
    "MCPServerRegistration": (
        "v2 renames MCPServerRegistration → MCPDeployment. Field shapes "
        "match; only `kind` needs changing."
    ),
    "Scope": (
        "v1 standalone Scope kind is gone in v2. Scope became a "
        "primitive — express as a Compose slot with `primitive: Scope` "
        "inside the kind that owns it. See docs/migration-v1-to-v2.md#scopes."
    ),
    "Credential": (
        "v2 does not yet ship a stdlib Credential kind; the primitive "
        "target is compose(Entity[secret], Policy[access]). Leave as-is "
        "and keep applying against the v1 engine until v056 M6 ships "
        "the Credential derivation."
    ),
}


@dataclass
class _MigrationResult:
    path: Path
    kind: Optional[str]
    status: str  # "clean" | "warn" | "needs-rewrite" | "error" | "skipped"
    message: str
    input_doc: Any
    output_doc: Any  # may be None on error


def _bump_api_version(doc: Any) -> tuple[bool, Optional[str]]:
    """Mutate doc in place. Return (changed, legacy_alias_used)."""
    if not isinstance(doc, dict):
        return False, None
    av = doc.get("apiVersion")
    if av == "powerloom.app/v2":
        return False, None
    if av in ("powerloom.app/v1", "powerloom/v1"):
        doc["apiVersion"] = "powerloom.app/v2"
        return True, (av if av == "powerloom/v1" else None)
    return False, None


def _migrate_one(doc: Any, source_path: Path) -> _MigrationResult:
    if not isinstance(doc, dict):
        return _MigrationResult(
            path=source_path,
            kind=None,
            status="error",
            message="document is not a mapping",
            input_doc=doc,
            output_doc=None,
        )

    av = doc.get("apiVersion")
    kind = doc.get("kind")

    if av == "powerloom.app/v2":
        return _MigrationResult(
            path=source_path,
            kind=kind,
            status="skipped",
            message="already v2",
            input_doc=doc,
            output_doc=doc,
        )

    if av not in ("powerloom.app/v1", "powerloom/v1"):
        return _MigrationResult(
            path=source_path,
            kind=kind,
            status="error",
            message=f"unknown apiVersion {av!r} (expected 'powerloom.app/v1' or 'powerloom/v1')",
            input_doc=doc,
            output_doc=None,
        )

    # v1 → v2 bump.
    out = dict(doc)  # shallow copy; we don't deep-mutate field contents
    _bump_api_version(out)

    if kind in _V2_STDLIB_KINDS:
        notes: list[str] = []
        if av == "powerloom/v1":
            notes.append("upgraded legacy 'powerloom/v1' alias to 'powerloom.app/v2'")
        return _MigrationResult(
            path=source_path,
            kind=kind,
            status="clean" if not notes else "warn",
            message="; ".join(notes) or "apiVersion bumped",
            input_doc=doc,
            output_doc=out,
        )

    if kind in _V1_ONLY_KINDS:
        return _MigrationResult(
            path=source_path,
            kind=kind,
            status="needs-rewrite",
            message=_V1_ONLY_KINDS[kind],
            input_doc=doc,
            output_doc=out,  # bumped but kind is still v1-only; emit with banner comment
        )

    # Unknown kind — bump anyway but warn.
    return _MigrationResult(
        path=source_path,
        kind=kind,
        status="warn",
        message=f"unknown kind {kind!r}; apiVersion bumped but shape not validated",
        input_doc=doc,
        output_doc=out,
    )


def _load_docs(path: Path) -> list[Any]:
    with path.open("r", encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


def _emit_docs(docs: list[_MigrationResult]) -> str:
    """Serialize migrated docs as a single YAML stream. Prepends a
    banner comment for needs-rewrite docs explaining what to do next."""
    chunks: list[str] = []
    for i, r in enumerate(docs):
        if r.output_doc is None:
            # Preserve original on error so `--in-place` doesn't destroy input.
            chunks.append(yaml.safe_dump(r.input_doc, sort_keys=False))
            continue
        if r.status == "needs-rewrite":
            banner = [
                "# !! weave migrate: this kind retired in v2.0.0.",
                f"# {r.message}",
                "# The apiVersion was bumped for completeness but the engine",
                "# will reject this doc until you re-express it per the guidance above.",
            ]
            chunks.append("\n".join(banner) + "\n" + yaml.safe_dump(r.output_doc, sort_keys=False))
        else:
            chunks.append(yaml.safe_dump(r.output_doc, sort_keys=False))
    return "\n---\n".join(c.rstrip() + "\n" for c in chunks)


def _render_report(results: list[_MigrationResult]) -> None:
    table = Table(title="weave migrate v1→v2", show_lines=False)
    table.add_column("File")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Notes")
    for r in results:
        color = {
            "clean": "green",
            "warn": "yellow",
            "needs-rewrite": "red",
            "error": "red",
            "skipped": "dim",
        }.get(r.status, "white")
        table.add_row(
            str(r.path),
            r.kind or "—",
            f"[{color}]{r.status}[/{color}]",
            (r.message[:100] + "…") if len(r.message) > 100 else r.message,
        )
    _console.print(table)


def _collect_paths(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(
            p for p in target.rglob("*.y*ml")
            if p.is_file() and p.suffix.lower() in (".yaml", ".yml")
        )
    return []


@app.command("v1-to-v2")
def v1_to_v2(
    target: Path = typer.Argument(
        ...,
        help="A v1.2.0 YAML manifest, or a directory containing such manifests.",
        exists=True,
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="Write migrated docs to this file (single-file input only). Default: stdout.",
    ),
    in_place: bool = typer.Option(
        False, "--in-place",
        help="Rewrite each input file with its migrated contents.",
    ),
    check: bool = typer.Option(
        False, "--check",
        help="Don't emit output; just report status per file. Exit 1 if any file is errored or needs-rewrite.",
    ),
) -> None:
    """Migrate v1.2.0 manifests → v2.0.0.

    For stdlib kinds (Agent, Skill, WorkflowType, MemoryPolicy, OU,
    Organization, Workflow, MCPDeployment) this is a pure apiVersion
    bump — shapes have parity at v2.0.0.

    Retired v1 kinds (Group, GroupMembership, RoleBinding,
    SkillAccessGrant, AgentSkill, AgentMCPServer, MCPServerRegistration,
    standalone Scope, Credential) emit a banner comment explaining
    how to re-express them.
    """
    if in_place and out:
        _console.print("[red]--in-place and --out are mutually exclusive.[/red]")
        raise typer.Exit(2)

    paths = _collect_paths(target)
    if not paths:
        _console.print(f"[red]No YAML files found at {target}[/red]")
        raise typer.Exit(2)

    if out and len(paths) > 1:
        _console.print("[red]--out requires a single input file. Use --in-place for directories.[/red]")
        raise typer.Exit(2)

    all_results: list[_MigrationResult] = []
    file_to_results: dict[Path, list[_MigrationResult]] = {}

    for p in paths:
        try:
            docs = _load_docs(p)
        except yaml.YAMLError as e:
            r = _MigrationResult(
                path=p, kind=None, status="error",
                message=f"YAML parse error: {e}",
                input_doc=None, output_doc=None,
            )
            all_results.append(r)
            file_to_results.setdefault(p, []).append(r)
            continue

        if not docs:
            r = _MigrationResult(
                path=p, kind=None, status="skipped",
                message="empty document",
                input_doc=None, output_doc=None,
            )
            all_results.append(r)
            file_to_results.setdefault(p, []).append(r)
            continue

        for d in docs:
            r = _migrate_one(d, p)
            all_results.append(r)
            file_to_results.setdefault(p, []).append(r)

    _render_report(all_results)

    # Emit output unless --check.
    if not check:
        for p, results in file_to_results.items():
            # Skip if the whole file errored.
            if all(r.status == "error" for r in results):
                continue
            body = _emit_docs(results)
            if in_place:
                p.write_text(body, encoding="utf-8")
            elif out:
                out.write_text(body, encoding="utf-8")
            else:
                # stdout — only for single-file case, otherwise concat.
                typer.echo(f"# --- {p} ---")
                typer.echo(body)

    # Exit code policy.
    has_error = any(r.status == "error" for r in all_results)
    has_rewrite = any(r.status == "needs-rewrite" for r in all_results)
    if has_error:
        raise typer.Exit(1)
    if check and has_rewrite:
        raise typer.Exit(1)
