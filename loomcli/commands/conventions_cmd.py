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
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

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


def _autodetect_scope_from_user() -> Optional[str]:
    """v0.7.3 — fallback when no --scope and no cache: ask /me for the
    active user's home OU and walk the OU tree to build the dotted
    path. Returns None on any auth/network/data failure (caller falls
    through to the next detection path)."""
    try:
        cfg = load_runtime_config()
        if cfg.access_token is None:
            return None
        client = PowerloomClient(cfg)
        with client:
            me = client.get("/me")
            home_ou_id = me.get("home_ou_id")
            if not home_ou_id:
                return None
            # Walk the tree by following parent_id from home OU upward.
            tree = client.get("/ous/tree")
            chain = _path_to_ou(tree, home_ou_id)
            if chain:
                return ".".join(chain)
    except Exception:
        # Any failure — auth, network, missing endpoint — falls through.
        return None
    return None


def _path_to_ou(tree: Any, target_id: str, *, prefix: Optional[list[str]] = None) -> Optional[list[str]]:
    """Recursive walk to find the dotted-path slug to `target_id`."""
    if isinstance(tree, list):
        for node in tree:
            found = _path_to_ou(node, target_id, prefix=prefix)
            if found:
                return found
        return None
    if not isinstance(tree, dict):
        return None
    name = tree.get("name") or tree.get("slug")
    if not name:
        return None
    here = (prefix or []) + [name]
    if str(tree.get("id")) == str(target_id):
        return here
    for child in tree.get("children") or []:
        found = _path_to_ou(child, target_id, prefix=here)
        if found:
            return found
    return None


def _autodetect_scope_from_git_remote() -> Optional[str]:
    """v0.7.3 — last-resort fallback: read `git remote get-url origin`
    and look for a Powerloom project whose github_repo_url matches.
    The project's OU (via tracker_projects.ou_id and the OU tree)
    becomes the scope. Returns None on any failure."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        remote = result.stdout.strip()
        if not remote:
            return None
        cfg = load_runtime_config()
        if cfg.access_token is None:
            return None
        client = PowerloomClient(cfg)
        with client:
            projects = client.get("/projects", status="active")
            # Engine returns a list directly (not a dict); guard either way.
            project_list = projects if isinstance(projects, list) else (projects.get("projects") or [])
            match = None
            normalized_remote = remote.rstrip("/").removesuffix(".git").lower()
            for p in project_list:
                url = p.get("github_repo_url")
                if not url:
                    continue
                if url.rstrip("/").removesuffix(".git").lower() == normalized_remote:
                    match = p
                    break
            if not match or not match.get("ou_id"):
                return None
            tree = client.get("/ous/tree")
            chain = _path_to_ou(tree, match["ou_id"])
            if chain:
                return ".".join(chain)
    except Exception:
        return None
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
    """Hit /memory/semantic/conventions/effective and return the cascaded
    list. Falls back to /match for older engines (pre-cascading).

    v0.7.0: cascading-aware. Treats `scope` as a dotted OU path (e.g.
    `bespoke-technology.powerloom`), resolves it server-side via the
    OU tree, and returns the folded effective set with org-level +
    OU-ancestors + leaf-OU all merged. Each row carries an
    `inheritance_chain` so the rendered block can label which OU each
    rule came from.

    Pre-v0.7.0 engines (no /effective endpoint, returns 404) trigger a
    fallback to the legacy /match call so an upgraded CLI doesn't break
    an older deployment.
    """
    # `scope` historically was an applies_to_scope_ref (dotted action
    # scope). With cascading, the more useful interpretation is "OU
    # path of the agent's home OU" — same string shape, different axis.
    # Try /effective first (cascading); if the endpoint isn't there,
    # fall back to /match for back-compat.
    ou_path = scope if scope.startswith("/") else "/" + scope.replace(".", "/")
    try:
        rows = client.get(
            "/memory/semantic/conventions/effective",
            ou_path=ou_path,
        )
    except PowerloomApiError as e:
        if e.status_code == 404 or e.status_code == 405:
            # Older engine — fall back to /match.
            try:
                rows = client.get(
                    "/memory/semantic/conventions/match", scope_ref=scope
                )
            except PowerloomApiError as e2:
                _console.print(f"[red]Convention fetch failed:[/red] {e2}")
                raise typer.Exit(1) from None
        else:
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
        # v0.7.0 cascading: /effective returns source_scope + inheritance_chain.
        # Surface the inheritance trail so agents reading CLAUDE.md know
        # which OU level a given rule came from.
        source_scope = c.get("source_scope")
        chain = c.get("inheritance_chain") or []
        inherited = bool(c.get("inherited"))

        scope_tag = f"`{source_scope}`" if source_scope else ""
        suffix = f" · inherited" if inherited else ""
        lines.append(
            f"### {display}  *(`{mode}`, name=`{name}`{(' · ' + scope_tag) if scope_tag else ''}{suffix})*"
        )
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
        if inherited and chain:
            chain_labels = []
            for lvl in chain:
                lvl_scope = lvl.get("scope")
                if lvl_scope == "org":
                    chain_labels.append("org")
                elif lvl_scope == "ou":
                    chain_labels.append(f"ou:{lvl.get('ou_name') or lvl.get('ou_id')}")
                elif lvl_scope == "project":
                    chain_labels.append(f"project:{lvl.get('project_id')}")
            if chain_labels:
                lines.append(
                    f"_Inheritance: {' → '.join(chain_labels)}_"
                )
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
    autosync block, leaving hand-edits outside the block intact.

    Scope detection (priority order, v0.7.3+):
      1. Explicit --scope flag
      2. Cached scope from the last successful sync in this config dir
      3. Active sub-principal's home OU (via /me)
      4. git remote get-url origin → match against project github_repo_url

    Drops the SessionStart-hook requirement of hardcoding --scope: the
    .claude/settings.json hook can now be just `weave conventions sync
    --runtime claude_code --quiet` and the CLI will figure out the OU.
    """
    if scope is None:
        scope = _read_cached_scope()
    if scope is None:
        scope = _autodetect_scope_from_user()
    if scope is None:
        scope = _autodetect_scope_from_git_remote()
    if scope is None:
        _console.print(
            "[red]Could not determine OU scope.[/red]\n"
            "[dim]Tried (in order): --scope flag, cached scope from last sync, "
            "active sub-principal home OU, git remote → project lookup.\n"
            "Pass --scope <dotted-path> explicitly, e.g. "
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
