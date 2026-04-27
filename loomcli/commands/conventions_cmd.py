"""`weave conventions sync / show / list` — surface OU-scoped Powerloom
conventions into the agent runtime's project-rules file.

Conventions are an org/OU-level memory type (engine: v064, lives at
`/memory/semantic/conventions/*`). They encode rules like "engine PRs
require pytest run before merge" or "threads must include a
Definition-of-Done section." The engine stores them as structured rows;
this command renders them into the project-rules file (CLAUDE.md /
AGENTS.md / GEMINI.md) the runtime actually reads at session start.

Design choices (per Shane 2026-04-27 evening):
- (A) Append into CLAUDE.md inside a marker block. Re-syncs replace
  the block instead of duplicating. Hand-edits outside the block are
  preserved.
- (B) Auto-fires from the SessionStart hook (after `weave agent-session
  register`) AND available manually via `weave conventions sync`.
- OU-scope only for v1. Project-scope is filed as a follow-up thread.
- Source: `/memory/semantic/conventions/match` — same memory API
  surface a future enforcement loop will use, no parallel "render"
  endpoint needed.

USAGE

    # Sync conventions for an OU into CLAUDE.md (and/or AGENTS.md /
    # GEMINI.md depending on --runtime)
    weave conventions sync --scope bespoke-technology.powerloom.engineering

    # Refresh after Shane updates a convention via UI
    weave conventions sync                  # uses last --scope from cache

    # See what's currently scoped without writing anything
    weave conventions show --scope bespoke-technology.powerloom.engineering

    # List every convention visible to your org (cross-OU view)
    weave conventions list

The auto-sync block format (in CLAUDE.md / AGENTS.md / GEMINI.md):

    <!-- POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN -->
    *Last sync: 2026-04-27T01:23:45Z · Scope: <scope> · N conventions*

    ### <name>  (<enforcement_mode>)
    <body summary>
    - <body item>
    - ...
    <!-- POWERLOOM_CONVENTIONS_AUTOSYNC_END -->
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import config_dir, load_runtime_config


app = typer.Typer(
    help=(
        "Sync OU-scoped Powerloom conventions into CLAUDE.md / AGENTS.md "
        "/ GEMINI.md. Engine: v064 (`/memory/semantic/conventions/*`)."
    ),
    no_args_is_help=True,
)
_console = Console()

# Marker block. HTML comments so they're invisible in rendered markdown
# but unique enough that no human would ever type them by accident.
_MARKER_BEGIN = "<!-- POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN -->"
_MARKER_END = "<!-- POWERLOOM_CONVENTIONS_AUTOSYNC_END -->"

# Per-runtime project-rules file mapping. Mirror of CLAUDE.md §4.9.
_RUNTIME_FILES = {
    "claude_code": "CLAUDE.md",
    "codex_cli": "AGENTS.md",
    "gemini_cli": "GEMINI.md",
    "antigravity": "GEMINI.md",  # Antigravity reads the same Gemini rule file
}

# Last-used scope cache so re-runs of `weave conventions sync` (e.g.
# from the SessionStart hook on subsequent opens of the same workspace)
# don't need --scope explicitly. Stored in the loomcli config dir as a
# tiny JSON file.
_SCOPE_CACHE_NAME = "conventions-last-scope.json"


def _scope_cache_path() -> Path:
    return config_dir() / _SCOPE_CACHE_NAME


def _read_cached_scope() -> Optional[str]:
    p = _scope_cache_path()
    if not p.exists():
        return None
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        s = data.get("scope")
        return s if isinstance(s, str) and s else None
    except (OSError, _json.JSONDecodeError):
        return None


def _write_cached_scope(scope: str) -> None:
    p = _scope_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"scope": scope}), encoding="utf-8")
    except OSError:
        pass  # best-effort; cache is convenience, not correctness


def _client_or_exit() -> PowerloomClient:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    return PowerloomClient(cfg)


def _fetch_conventions(client: PowerloomClient, scope: str) -> list[dict]:
    """Hit /memory/semantic/conventions/match and return the raw list.

    Engine match semantics (per services/conventions.py:match_active_conventions):
    exact match on applies_to_scope_ref, OR ancestor (scope is a dotted
    descendant of the convention's primary scope), OR same on
    additional_scope_refs[]. Archived rows excluded. Sorted deterministically.
    """
    try:
        rows = client.get(f"/memory/semantic/conventions/match", scope_ref=scope)
    except PowerloomApiError as e:
        _console.print(f"[red]Convention fetch failed:[/red] {e}")
        raise typer.Exit(1) from None
    return rows if isinstance(rows, list) else (rows.get("items") or [])


def _render_block(scope: str, conventions: list[dict]) -> str:
    """Build the markdown block (without the surrounding markers).

    Format chosen for legibility when an agent reads CLAUDE.md:
      *Last sync timestamp + scope + count*
      ### <name>  (<enforcement_mode>)
      <body summary>
      - body item 1
      - body item 2
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "## §5 Org conventions (auto-synced from Powerloom)",
        "",
        f"*Last sync: {now} · Scope: `{scope}` · {len(conventions)} convention(s)*",
        "",
    ]
    if not conventions:
        lines.extend([
            "_No conventions defined for this scope yet. "
            "Set them via the Powerloom UI or `weave apply convention.yaml`._",
            "",
        ])
        return "\n".join(lines)

    for c in conventions:
        name = c.get("name", "(unnamed)")
        display = c.get("display_name") or name
        mode = c.get("enforcement_mode", "advisory")
        body = c.get("body") or {}
        summary = body.get("summary") or c.get("body_summary") or ""
        items = body.get("items") or c.get("body_items") or []

        lines.append(f"### {display}  *(`{mode}`, name=`{name}`)*")
        lines.append("")
        if summary:
            lines.append(summary.strip())
            lines.append("")
        for item in items:
            if not isinstance(item, str):
                continue
            lines.append(f"- {item.strip()}")
        if items:
            lines.append("")
    return "\n".join(lines)


def _apply_block_to_file(
    target: Path, block_content: str, *, dry_run: bool
) -> tuple[str, str]:
    """Read target, replace existing marker block (or append a new one).
    Returns (action, message). Action is one of: 'created', 'replaced',
    'appended', 'unchanged'.
    """
    new_block = f"{_MARKER_BEGIN}\n{block_content}\n{_MARKER_END}"

    if not target.exists():
        if dry_run:
            return ("created", f"would create {target} with autosync block")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bare-bones header so the file isn't empty above the autosync block.
        # Honors the convention from existing CLAUDE.md/AGENTS.md/GEMINI.md
        # that section §1 names the project + working agreement intent.
        header = (
            f"# {target.stem} — Powerloom-managed project rules\n\n"
            "This file is partly machine-managed. The block between the\n"
            "POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN/END markers is rewritten\n"
            "by `weave conventions sync` (also fires from the SessionStart\n"
            "hook). Edit content OUTSIDE the block; edits inside are\n"
            "lost on the next sync.\n\n"
        )
        target.write_text(header + new_block + "\n", encoding="utf-8")
        return ("created", f"created {target}")

    text = target.read_text(encoding="utf-8")

    if _MARKER_BEGIN in text and _MARKER_END in text:
        # Replace existing block
        before, _, rest = text.partition(_MARKER_BEGIN)
        _, _, after = rest.partition(_MARKER_END)
        new_text = before + new_block + after
        if new_text == text:
            return ("unchanged", f"{target} already up to date")
        if dry_run:
            return ("replaced", f"would replace autosync block in {target}")
        target.write_text(new_text, encoding="utf-8")
        return ("replaced", f"replaced autosync block in {target}")

    if _MARKER_BEGIN in text or _MARKER_END in text:
        # Half-marker — corrupted file. Refuse rather than guess.
        return (
            "skipped",
            f"{target} has only one of the marker pair — refusing to overwrite. "
            "Manually fix or delete the file.",
        )

    # No markers — append at end with a leading separator
    if dry_run:
        return ("appended", f"would append autosync block to {target}")
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    target.write_text(text + sep + new_block + "\n", encoding="utf-8")
    return ("appended", f"appended autosync block to {target}")


@app.command("sync")
def sync(
    scope: Annotated[Optional[str], typer.Option(
        "--scope", "-s",
        help="OU scope-ref (dotted path, e.g. 'bespoke-technology.powerloom.engineering'). "
             "If omitted, uses the cached scope from the last successful sync in this workspace.",
    )] = None,
    runtime: Annotated[str, typer.Option(
        "--runtime", "-r",
        help="Which project-rules file(s) to write. One of: claude_code (CLAUDE.md), "
             "codex_cli (AGENTS.md), gemini_cli (GEMINI.md), antigravity (GEMINI.md), "
             "or 'all' (writes all three). Default: all.",
    )] = "all",
    workdir: Annotated[Optional[Path], typer.Option(
        "--workdir",
        help="Project root to write into. Defaults to current directory.",
    )] = None,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run",
        help="Print what would change; don't write files.",
    )] = False,
    quiet: Annotated[bool, typer.Option(
        "--quiet",
        help="Suppress success output (for SessionStart hooks). Errors still print.",
    )] = False,
) -> None:
    """Fetch OU-scoped conventions from Powerloom and sync into the
    workspace's project-rules file(s). Idempotent — re-runs replace the
    autosync block, leaving hand-edits outside the block intact."""
    if scope is None:
        scope = _read_cached_scope()
        if scope is None:
            _console.print(
                "[red]No --scope provided and no cached scope from a prior sync.[/red]\n"
                "[dim]Pass --scope <dotted-path>, e.g. "
                "`bespoke-technology.powerloom.engineering`.[/dim]"
            )
            raise typer.Exit(2)

    workdir = (workdir or Path(".")).resolve()

    # Resolve target file set from runtime
    if runtime == "all":
        runtimes = list(_RUNTIME_FILES.keys())
    elif runtime in _RUNTIME_FILES:
        runtimes = [runtime]
    else:
        _console.print(
            f"[red]Unknown runtime {runtime!r}.[/red] One of: "
            f"{', '.join(sorted(_RUNTIME_FILES) | {'all'})}."
        )
        raise typer.Exit(2)
    # De-dupe target paths (gemini_cli + antigravity both -> GEMINI.md)
    targets: list[Path] = []
    seen: set[Path] = set()
    for r in runtimes:
        p = (workdir / _RUNTIME_FILES[r]).resolve()
        if p not in seen:
            seen.add(p)
            targets.append(p)

    with _client_or_exit() as client:
        conventions = _fetch_conventions(client, scope)

    block_content = _render_block(scope, conventions)
    actions: list[tuple[Path, str, str]] = []
    for target in targets:
        action, message = _apply_block_to_file(target, block_content, dry_run=dry_run)
        actions.append((target, action, message))

    # Cache the scope on success (even in dry-run, so the next real run
    # picks it up)
    _write_cached_scope(scope)

    if quiet:
        # Silent on the happy path — errors above already printed.
        return
    _console.print(
        f"[green]Synced {len(conventions)} convention(s)[/green] "
        f"for scope [cyan]{scope}[/cyan]"
        + (" [dim](dry-run)[/dim]" if dry_run else "")
    )
    for target, action, message in actions:
        marker = {"created": "+", "replaced": "↻", "appended": "+",
                  "unchanged": "·", "skipped": "!"}.get(action, "?")
        _console.print(f"  {marker} {message}")


@app.command("show")
def show(
    scope: Annotated[str, typer.Option(
        "--scope", "-s",
        help="OU scope-ref (dotted path).",
    )],
    json_output: Annotated[bool, typer.Option(
        "--json",
        help="Print the raw API response as JSON.",
    )] = False,
) -> None:
    """Print the conventions that would be synced for a given scope.
    Read-only; doesn't touch any files."""
    with _client_or_exit() as client:
        conventions = _fetch_conventions(client, scope)
    if json_output:
        print(_json.dumps(conventions, indent=2, default=str))
        return
    if not conventions:
        _console.print(f"[dim]No conventions for scope [cyan]{scope}[/cyan].[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Display name")
    table.add_column("Mode", width=10)
    table.add_column("Scope")
    table.add_column("Items", width=6, justify="right")
    for c in conventions:
        body = c.get("body") or {}
        items = body.get("items") or c.get("body_items") or []
        table.add_row(
            c.get("name", "?"),
            (c.get("display_name") or "")[:50],
            c.get("enforcement_mode", "?"),
            (c.get("applies_to_scope_ref") or "")[:40],
            str(len(items)),
        )
    _console.print(table)
    _console.print(f"[dim]{len(conventions)} convention(s) for scope {scope!r}[/dim]")


@app.command("list")
def list_conventions(
    status: Annotated[Optional[str], typer.Option(
        "--status",
        help="Filter by status (active|archived). Default: all.",
    )] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List every convention visible to your org (cross-OU view).

    For per-scope filtering use `weave conventions show --scope <ref>`.
    """
    with _client_or_exit() as client:
        params: dict = {}
        if status:
            params["status"] = status
        try:
            rows = client.get("/memory/semantic/conventions", **params)
        except PowerloomApiError as e:
            _console.print(f"[red]Convention list failed:[/red] {e}")
            raise typer.Exit(1) from None
    rows = rows if isinstance(rows, list) else (rows.get("items") or [])
    if json_output:
        print(_json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        _console.print("[dim]No conventions defined yet.[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Display")
    table.add_column("Mode", width=10)
    table.add_column("Scope")
    table.add_column("Status", width=10)
    for c in rows:
        table.add_row(
            c.get("name", "?"),
            (c.get("display_name") or "")[:40],
            c.get("enforcement_mode", "?"),
            (c.get("applies_to_scope_ref") or "")[:40],
            c.get("status", "?"),
        )
    _console.print(table)
    _console.print(f"[dim]{len(rows)} convention(s)[/dim]")
