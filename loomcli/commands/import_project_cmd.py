"""`weave import-project` — import a Powerloom-shaped repo into the tracker.

v059 Self-Import MVP, slice 6 CLI shim. Walks the source files in a
local checkout (Project.md, docs/phases/*.md, KNOWN_ISSUES.md,
docs/out-of-scope.md, docs/handoffs/*.md, ReadMe.md), uploads the
contents to the engine via POST /projects/import/from-source, and
prints the apply-result counters.

Why upload-not-parse:
    The parser lives in powerloom_api.services.project_importer. The
    CLI is a thin client and shouldn't import the engine package
    (would be a heavyweight dep with FastAPI / SQLAlchemy / pgvector
    pulled in transitively). Engine-side parsing keeps the parser as
    one source of truth and lets every client (this CLI, the
    Antigravity skill, future browser-side previews) hit the same
    code path.

Usage:

    # Dry run — see what WOULD change without committing
    weave import-project /path/to/checkout --dry-run

    # Apply against the default 'powerloom' tracker project
    weave import-project /path/to/checkout

    # Override the tracker project slug (e.g. import into a sandbox slug)
    weave import-project /path/to/checkout --slug-override powerloom-sandbox

    # Custom project slug + name (importing a non-Powerloom project)
    weave import-project /path/to/checkout --slug acme --name "Acme Roadmap"

The set of source paths walked is hardcoded to match the engine
parser's expectations. If the engine grows new parsers, the path list
here grows in lockstep.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


_console = Console()

# Source paths the engine parser knows how to read. Kept in sync with
# build_import_plan() in powerloom_api/services/project_importer.py.
# Order doesn't matter — engine sorts deterministically. Globs expand
# to all matching files at the time of upload.
_TOP_LEVEL_FILES = ("Project.md", "KNOWN_ISSUES.md", "ReadMe.md")
_TOP_LEVEL_OPTIONAL_FILES = ("docs/out-of-scope.md",)
_GLOB_DIRS = (
    "docs/phases",  # phase-*.md
    "docs/handoffs",  # *.md
)
# Cap matches the engine's MAX_SOURCE_FILES_BYTES (5 MB). Enforced
# here too so we fail fast with a clearer error before the round-trip.
_MAX_TOTAL_BYTES = 5 * 1024 * 1024


def _git_short_sha(repo_root: Path) -> Optional[str]:
    """Best-effort short SHA of the checkout. Returns None if the
    directory isn't a git repo — the engine then falls back to its
    'client-supplied' default which becomes the provenance value."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            return sha or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None


def _collect_source_files(repo_root: Path) -> dict[str, str]:
    """Walk repo_root and gather the files the engine parser knows about.

    Returns a `{repo_relative_path: utf8_content}` dict in the shape
    POST /projects/import/from-source expects. Skips files that don't
    exist — the engine handles missing files gracefully (each parser
    is conditional on its source existing).

    Paths are emitted with forward slashes for cross-platform stability;
    the engine normalizes either separator on the receiving side, but
    forward-slash is the canonical wire form.
    """
    out: dict[str, str] = {}

    for rel in _TOP_LEVEL_FILES:
        path = repo_root / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8")

    for rel in _TOP_LEVEL_OPTIONAL_FILES:
        path = repo_root / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8")

    for dir_rel in _GLOB_DIRS:
        dir_path = repo_root / dir_rel
        if not dir_path.is_dir():
            continue
        for md_path in sorted(dir_path.glob("*.md")):
            rel = f"{dir_rel}/{md_path.name}"
            out[rel] = md_path.read_text(encoding="utf-8")

    return out


def import_project_command(
    repo_path: Annotated[
        Path,
        typer.Argument(
            ...,
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Path to the Powerloom-shaped checkout to import.",
        ),
    ],
    slug: Annotated[
        str,
        typer.Option(
            "--slug",
            help="Project slug to use when creating the tracker project.",
        ),
    ] = "powerloom",
    name: Annotated[
        str,
        typer.Option(
            "--name",
            help="Display name for the tracker project (only used on first import).",
        ),
    ] = "Powerloom",
    slug_override: Annotated[
        Optional[str],
        typer.Option(
            "--slug-override",
            help=(
                "Apply the parsed plan against a different project slug than "
                "--slug (useful for sandbox/preview imports without touching "
                "the canonical project)."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Parse + would-apply but rollback the engine transaction. "
                "Counters in the output reflect what WOULD have changed."
            ),
        ),
    ] = False,
) -> None:
    """Import a Powerloom-shaped checkout into the tracker.

    Walks the checkout for known source files, uploads them as a JSON
    dict to the engine, the engine parses + applies. Idempotent —
    re-running against the same checkout creates 0 new threads (every
    op carries a dedupe_key the engine matches against existing
    import_source replies).
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)

    # --- Collect source files ---
    source_files = _collect_source_files(repo_path)
    if not source_files:
        _console.print(
            f"[red]No recognized source files found under {repo_path}.[/red]"
        )
        _console.print(
            "[dim]Expected at least one of: Project.md, KNOWN_ISSUES.md, "
            "ReadMe.md, docs/out-of-scope.md, docs/phases/phase-*.md, "
            "docs/handoffs/*.md[/dim]"
        )
        raise typer.Exit(1)

    total_bytes = sum(len(c.encode("utf-8")) for c in source_files.values())
    if total_bytes > _MAX_TOTAL_BYTES:
        _console.print(
            f"[red]Total source size {total_bytes} bytes exceeds 5 MB cap.[/red] "
            "The engine will reject this with HTTP 413; failing fast locally."
        )
        raise typer.Exit(1)

    commit_sha = _git_short_sha(repo_path)

    _console.print(
        f"[dim]Collected {len(source_files)} files "
        f"({total_bytes:,} bytes) from {repo_path}"
        + (f" @ {commit_sha}" if commit_sha else " (not a git checkout)")
        + (" [bold yellow]DRY RUN[/bold yellow]" if dry_run else "")
        + "[/dim]"
    )

    # --- Upload + apply ---
    body: dict = {
        "source_files": source_files,
        "project_slug": slug,
        "project_name": name,
        "dry_run": dry_run,
    }
    if slug_override:
        body["project_slug_override"] = slug_override
    if commit_sha:
        body["commit_sha"] = commit_sha

    with PowerloomClient(cfg) as client:
        try:
            result = client.post("/projects/import/from-source", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Import failed:[/red] {e}")
            raise typer.Exit(1) from None

    # --- Render result ---
    if not isinstance(result, dict):
        _console.print(f"[red]Unexpected response shape:[/red] {result}")
        raise typer.Exit(1)

    summary = result.get("summary") or "(no summary)"
    if dry_run:
        _console.print(f"[bold yellow]DRY RUN[/bold yellow] — {summary}")
        _console.print(
            "[dim]Counters reflect the would-be state; nothing was persisted.[/dim]"
        )
    else:
        _console.print(f"[green]{summary}[/green]")

    # Detail table for non-trivial results
    counters = [
        ("project_created", result.get("project_created")),
        ("milestones_created", result.get("milestones_created")),
        ("milestones_updated", result.get("milestones_updated")),
        ("threads_created", result.get("threads_created")),
        ("threads_updated", result.get("threads_updated")),
        ("tags_created", result.get("tags_created")),
        ("import_source_replies_written", result.get("import_source_replies_written")),
        ("skipped_dedupe_match", result.get("skipped_dedupe_match")),
    ]
    for name_, value in counters:
        if value:
            _console.print(f"  {name_}: {value}")

    errors = result.get("errors") or []
    if errors:
        _console.print(f"[yellow]{len(errors)} per-op error(s):[/yellow]")
        for err in errors[:10]:
            _console.print(f"  [yellow]·[/yellow] {err}")
        if len(errors) > 10:
            _console.print(f"  [dim]... + {len(errors) - 10} more[/dim]")
