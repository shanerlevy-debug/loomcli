"""`weave thread create / pluck / reply / done / close / wont-do / list / show / update`.

Implements the tracker thread workflow that CLAUDE.md / GEMINI.md / AGENTS.md §4.10
established as the canonical "what's being worked on" surface for every agent
session. The weave-tracker skill (`plugin/skills/weave-tracker/SKILL.md`) describes
the full workflow; this module is the CLI surface that backs it.

Usage examples:

    # File a thread for the work this session is doing
    weave thread create \\
      --project powerloom \\
      --title "Fix Alfred WS connection" \\
      --priority high \\
      --description "..."

    # Claim it (sets status=in_progress)
    weave thread pluck <thread_id>

    # As decisions/blockers land, post replies
    weave thread reply <thread_id> "decided to go with option B because ..."
    weave thread reply <thread_id> --from-stdin < notes.md

    # At PR merge, close out
    weave thread done <thread_id>      # the work shipped
    weave thread close <thread_id>     # thread closed but scope might still matter
    weave thread wont-do <thread_id>   # decided not to ship

    # Find work
    weave thread list --mine                                # what you're on
    weave thread list --project powerloom --status open     # available work
    weave thread show <thread_id>                            # full detail + replies

    # Generic update (status / priority / assignee / metadata)
    weave thread update <thread_id> --status review
    weave thread update <thread_id> --priority critical
    weave thread update <thread_id> --assigned-to <user-uuid>

Project addressing accepts either the slug (`powerloom`) or a UUID. Slug is
resolved via GET /projects + linear lookup — fine for the small project counts
expected. If the lookup misses, raises typer.Exit(1) with a list of available
slugs.

Output rendering matches the `import_project_cmd.py` pattern: terse, rich-
styled lines for command output; tables for list views; full pretty-print for
show. JSON output via --json flag for scripting.
"""
from __future__ import annotations

import json as _json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


app = typer.Typer(help="Manage Powerloom tracker threads (create / pluck / reply / done / list / show / update). See CLAUDE.md / GEMINI.md / AGENTS.md §4.10.")

# Friendly-names L4 — auto-stamp session_attribution into metadata_json on
# create + reply when the caller has registered a sub-principal for this
# session. Env-driven opt-in: the SessionStart hook (Powerloom #142 M3) +
# CC plugin set POWERLOOM_ACTIVE_SUBPRINCIPAL_ID; the CLI lazy-fetches the
# sub-principal details once per command invocation and stamps the metadata.
# When the env var is absent (most current callers), no stamp is added —
# zero behavior change for direct human users.
SUBPRINCIPAL_ENV_VAR = "POWERLOOM_ACTIVE_SUBPRINCIPAL_ID"


def _resolve_active_subprincipal_id() -> Optional[str]:
    """Pick the active sub-principal UUID for this session, with two-tier
    resolution:

      1. `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` env var — explicit override,
         wins when set (and non-empty). Same as v0.6.4-rc1 behavior.
      2. Per-scope cache file at `<config_dir>/active-subprincipal-<scope>.txt` —
         written by `weave agent-session register`. Fallback because
         shell SessionStart hooks can't propagate env to the parent
         shell, so the env-var-only path is a no-op for every real
         agent session today (verified in the v067 onboarding sprint).

    `<scope>` is derived from the current git branch via the
    `session/<scope>-<yyyymmdd>` convention. Branches that don't match
    return None for the file path → fall through to "no attribution"
    (graceful degradation; old behavior).

    Returns None when neither source resolves.
    """
    # Tier 1 — env var
    env_id = os.environ.get(SUBPRINCIPAL_ENV_VAR, "").strip()
    if env_id:
        return env_id

    # Tier 2 — per-scope cache file
    import subprocess as _subprocess
    try:
        result = _subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        branch = (result.stdout or "").strip()
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        return None
    if not branch.startswith("session/"):
        return None
    scope = branch[len("session/"):]
    if not scope:
        return None

    from loomcli.config import active_subprincipal_file
    path = active_subprincipal_file(scope)
    if not path.exists():
        return None
    try:
        cached = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    # Validate UUID-ish shape (skip the malformed leftovers that an
    # earlier broken hook could have written). uuid.UUID raises
    # ValueError on bad input.
    try:
        uuid.UUID(cached)
    except (ValueError, TypeError):
        return None
    return cached


def _build_session_attribution(client: PowerloomClient) -> Optional[dict]:
    """Fetch the active sub-principal's details and assemble the
    session_attribution payload to merge into metadata_json.

    Resolution order (see `_resolve_active_subprincipal_id`):
      1. `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` env var
      2. Per-scope cache file written by `weave agent-session register`

    Returns None when:
    - Neither source resolves a sub-principal id (most direct-human callers)
    - The sub-principal lookup against /me/agents fails (logged + continues)

    The shape matches what the dogfood scripts have been writing manually so
    queries (e.g. "find threads attributed to Claude Code sessions") work
    against both old (manually-stamped) and new (auto-stamped) threads.
    """
    sp_id = _resolve_active_subprincipal_id()
    if not sp_id:
        return None
    try:
        sp = client.get(f"/me/agents/{sp_id}")
    except PowerloomApiError as e:
        # Best-effort. Log + continue without stamping rather than failing
        # the create/reply call. The user can debug separately if attribution
        # is missing.
        _console.print(
            f"[yellow]Warning:[/yellow] could not fetch sub-principal "
            f"{sp_id} for attribution stamp: {e.status_code}. Thread will "
            f"be created without session_attribution metadata.",
            highlight=False,
        )
        return None
    return {
        "subprincipal_id": sp.get("id"),
        "subprincipal_principal_id": sp.get("principal_id"),
        "subprincipal_name": sp.get("name"),
        "client_kind": sp.get("client_kind"),
        "parent_user_id": sp.get("user_id"),
        "stamped_at": datetime.now(timezone.utc).isoformat(),
    }


def _maybe_stamp_attribution(
    client: PowerloomClient,
    thread: dict,
    no_attribution: bool,
) -> dict:
    """If the caller has an active sub-principal, PATCH the thread's
    metadata_json to add session_attribution. Returns the (possibly updated)
    thread dict so callers can use it without a re-fetch.

    No-op (returns the input thread unchanged) when disabled, env-unset, or
    the sub-principal lookup fails.
    """
    if no_attribution:
        return thread
    attribution = _build_session_attribution(client)
    if not attribution:
        return thread
    try:
        # Use the in-memory `thread` rather than re-fetching — the engine
        # just returned it from create/pluck so it's authoritative + fresh.
        meta = dict(thread.get("metadata_json") or {})
        meta["session_attribution"] = attribution
        return client.patch(f"/threads/{thread['id']}", {"metadata_json": meta})
    except PowerloomApiError as e:
        _console.print(
            f"[yellow]Warning:[/yellow] thread created/updated but "
            f"attribution stamp failed: {e}. Run `weave thread show {thread['id']}` "
            f"to verify state.",
            highlight=False,
        )
        return thread
_console = Console()

# Status enum — must match engine's tracker_thread.status. Kept locally so the
# CLI can validate before round-tripping to the engine. If the enum drifts the
# engine returns 422 and the user sees the engine's message verbatim.
_STATUS_CHOICES = ("open", "in_progress", "blocked", "review", "needs_client_review", "done", "closed", "wont_do")
_PRIORITY_CHOICES = ("critical", "high", "medium", "low", "none")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_or_exit() -> PowerloomClient:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    return PowerloomClient(cfg)


def _resolve_project(client: PowerloomClient, project: str) -> str:
    """Accept either a project slug or UUID; return the UUID.

    UUID detection is lazy — try uuid.UUID() and fall back to slug lookup.
    Slug lookup queries GET /projects and linear-scans. Fine for small counts.
    """
    try:
        # Already a UUID — use it directly
        return str(uuid.UUID(project))
    except ValueError:
        pass

    # Slug — look up
    try:
        projects = client.get("/projects")
    except PowerloomApiError as e:
        _console.print(f"[red]Could not list projects:[/red] {e}")
        raise typer.Exit(1) from None

    items = projects if isinstance(projects, list) else (projects.get("items") or projects.get("projects") or [])
    for p in items:
        if isinstance(p, dict) and p.get("slug") == project:
            return p["id"]

    available = sorted({p.get("slug", "?") for p in items if isinstance(p, dict)})
    _console.print(f"[red]No project with slug {project!r} found.[/red]")
    if available:
        _console.print(f"[dim]Available slugs: {', '.join(available)}[/dim]")
    raise typer.Exit(1)


def _resolve_thread(
    client: PowerloomClient,
    ref: str,
    *,
    default_project: str = "powerloom",
) -> str:
    """W1.5.1 — Accept either a thread UUID, a `project:slug` pair, or a bare
    slug; return the thread UUID.

    Examples:
        "8d2c7502-d79a-..."   -> direct UUID, no lookup
        "powerloom:ki-004"    -> resolve project 'powerloom', GET by-slug
        "ki-004"              -> use default project (powerloom), GET by-slug

    Slug lookup hits GET /projects/{project_id}/threads/by-slug/{slug}
    (added in API migration 0065). On 404, raises typer.Exit(1) with a
    targeted message.
    """
    # 1. UUID — fast path, no network
    try:
        return str(uuid.UUID(ref))
    except ValueError:
        pass

    # 2. Slug form — split out project context if present
    if ":" in ref:
        project_part, _, slug_part = ref.partition(":")
        project_part = project_part.strip()
        slug_part = slug_part.strip()
        if not project_part or not slug_part:
            _console.print(
                f"[red]Invalid thread reference {ref!r}.[/red] "
                "Expected `project-slug:thread-slug`, `thread-slug`, or a UUID."
            )
            raise typer.Exit(2)
    else:
        project_part = default_project
        slug_part = ref.strip()

    # Validate slug shape early — saves a useless HTTP roundtrip.
    import re as _re_mod
    if not _re_mod.match(r"^[a-z0-9][a-z0-9-]{0,62}$", slug_part):
        _console.print(
            f"[red]Invalid slug shape {slug_part!r}.[/red] "
            "Slugs are lowercase alphanumeric + hyphens, 1-63 chars."
        )
        raise typer.Exit(2)

    project_id = _resolve_project(client, project_part)
    try:
        thread = client.get(f"/projects/{project_id}/threads/by-slug/{slug_part}")
    except PowerloomApiError as e:
        if e.status_code == 404:
            _console.print(
                f"[red]No thread with slug {slug_part!r} in project "
                f"{project_part!r}.[/red]"
            )
        else:
            _console.print(f"[red]Slug lookup failed:[/red] {e}")
        raise typer.Exit(1) from None
    return str(thread["id"])


def _print_thread_summary(t: dict) -> None:
    """Render a single-thread summary line. Used by create / pluck / done / etc.
    after a mutation lands so the user sees the new state."""
    status = t.get("status", "?")
    priority = t.get("priority", "?")
    title = t.get("title", "(no title)")
    sp_name = ((t.get("metadata_json") or {}).get("session_attribution") or {}).get("subprincipal_name")
    suffix = f" [dim]· {sp_name}[/dim]" if sp_name else ""
    _console.print(f"  [bold]{title}[/bold]{suffix}")
    _console.print(f"  [dim]id={t.get('id')} status={status} priority={priority}[/dim]")


def _output_json(data) -> None:
    """Print data as JSON for --json flag. Handles UUIDs + datetimes."""
    print(_json.dumps(data, indent=2, default=str))


def _rows_from_response(items) -> list[dict]:
    rows = items if isinstance(items, list) else (items.get("items") or items.get("threads") or [])
    return [row for row in rows if isinstance(row, dict)]


def _print_thread_table(rows: list[dict]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", style="cyan", width=14)
    table.add_column("Pri", width=8)
    table.add_column("Title", overflow="fold")
    table.add_column("Owner", overflow="fold", width=30)
    table.add_column("ID", overflow="fold", width=10)
    for t in rows:
        sp = ((t.get("metadata_json") or {}).get("session_attribution") or {}).get("subprincipal_name") or ""
        owner = sp[:28] if sp else (t.get("assigned_to") or "")[:8]
        table.add_row(
            t.get("status", "?"),
            t.get("priority", "?"),
            t.get("title", "")[:60],
            owner,
            (t.get("id") or "")[:8],
        )
    _console.print(table)
    _console.print(f"[dim]{len(rows)} thread(s)[/dim]")


def _my_work_watch_line(rows: list[dict]) -> str:
    counts = Counter(str(row.get("status", "unknown")) for row in rows)
    counts_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    if not counts_text:
        counts_text = "none"
    top = rows[0] if rows else {}
    top_label = ""
    if top:
        top_label = (
            f" | top={top.get('status', '')} "
            f"{str(top.get('title', ''))[:80]}"
        )
    return f"my-work total={len(rows)} statuses={counts_text}{top_label}"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@app.command("create")
def create(
    title: Annotated[str, typer.Option("--title", "-t", help="Imperative phrase. e.g. 'Fix Alfred WS connection'.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug (e.g. 'powerloom') or UUID.")] = "powerloom",
    priority: Annotated[str, typer.Option("--priority", help="One of: critical, high, medium, low, none.")] = "medium",
    description: Annotated[Optional[str], typer.Option("--description", "-d", help="Body text. Markdown supported.")] = None,
    description_from_stdin: Annotated[bool, typer.Option("--description-from-stdin", help="Read description from stdin (useful for long content).")] = False,
    milestone_id: Annotated[Optional[str], typer.Option("--milestone-id", help="UUID of an existing milestone in the project.")] = None,
    assigned_to: Annotated[Optional[str], typer.Option("--assigned-to", help="UUID of the user to assign. Default: unassigned.")] = None,
    tag_ids: Annotated[Optional[list[str]], typer.Option("--tag-id", help="Tag UUIDs (repeatable).")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print the created thread as JSON.")] = False,
    no_attribution: Annotated[
        bool,
        typer.Option(
            "--no-attribution",
            help=(
                "Skip session_attribution metadata stamping even when "
                "POWERLOOM_ACTIVE_SUBPRINCIPAL_ID is set. By default, when the "
                "env var is present, the CLI auto-fetches the sub-principal's "
                "details and writes session_attribution into metadata_json."
            ),
        ),
    ] = False,
) -> None:
    """Create a new tracker thread.

    Per CLAUDE.md §4.10, file a thread at session start (or as soon as work
    scope is known). Title should be an imperative phrase; description should
    follow the canonical four-section shape (Reported / Repro / Definition of
    done / Out of scope). See the weave-tracker skill for the description
    template.

    **Friendly-names L4 — auto-attribution.** When the
    `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` env var is set (e.g. by a CC SessionStart
    hook or CI runner), the new thread's `metadata_json.session_attribution` is
    populated automatically with the sub-principal's id / name / client_kind /
    parent_user_id + a stamped_at timestamp. Pass `--no-attribution` to suppress
    even when the env is set.
    """
    if priority not in _PRIORITY_CHOICES:
        _console.print(f"[red]Invalid priority {priority!r}.[/red] One of: {', '.join(_PRIORITY_CHOICES)}")
        raise typer.Exit(2)

    if description_from_stdin:
        if description:
            _console.print("[red]Pick one of --description / --description-from-stdin (not both).[/red]")
            raise typer.Exit(2)
        description = sys.stdin.read()

    body: dict = {
        "title": title,
        "priority": priority,
        "description": description or "",
    }
    if milestone_id:
        body["milestone_id"] = milestone_id
    if assigned_to:
        body["assigned_to"] = assigned_to
    if tag_ids:
        body["tag_ids"] = tag_ids

    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        try:
            thread = client.post(f"/projects/{project_id}/threads", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Create failed:[/red] {e}")
            raise typer.Exit(1) from None

        # L4 auto-stamp — returns the patched thread (or the original if no-op)
        # so the JSON / summary output reflects the stamped metadata.
        thread = _maybe_stamp_attribution(client, thread, no_attribution)

    if json_output:
        _output_json(thread)
        return

    _console.print(f"[green]Thread created.[/green]")
    _print_thread_summary(thread)
    _console.print(f"  [dim]Next: weave thread pluck {thread.get('id')}[/dim]")


# ---------------------------------------------------------------------------
# pluck
# ---------------------------------------------------------------------------


@app.command("pluck")
def pluck(
    thread_id: Annotated[str, typer.Argument(help="Thread reference: UUID, slug (e.g. 'ki-004'), or 'project:slug' (e.g. 'powerloom:ki-004').")],
    agent_id: Annotated[Optional[str], typer.Option("--agent-id", help="Optional Agent UUID to attribute the pluck to (for hosted-agent claiming).")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print the updated thread as JSON.")] = False,
) -> None:
    """Claim a thread for the current session (sets status=in_progress).

    Pluck is one-shot — re-plucking returns 409. To take over a thread someone
    else plucked, use `weave thread update <id> --assigned-to <principal>`.
    """
    pluck_body: dict = {}
    if agent_id:
        pluck_body["agent_id"] = agent_id

    with _client_or_exit() as client:
        thread_uuid = _resolve_thread(client, thread_id)
        try:
            thread = client.post(f"/threads/{thread_uuid}/pluck", pluck_body)
        except PowerloomApiError as e:
            if e.status_code == 409:
                _console.print(f"[yellow]Thread already plucked.[/yellow] Run `weave thread show {thread_uuid}` to see who has it.")
            else:
                _console.print(f"[red]Pluck failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(thread)
        return
    _console.print(f"[green]Plucked.[/green]")
    _print_thread_summary(thread)


# ---------------------------------------------------------------------------
# reply
# ---------------------------------------------------------------------------


@app.command("reply")
def reply(
    thread_id: Annotated[str, typer.Argument(help="Thread reference: UUID, slug, or 'project:slug'.")],
    content: Annotated[Optional[str], typer.Argument(help="Reply text. Use --from-stdin for long content.")] = None,
    from_stdin: Annotated[bool, typer.Option("--from-stdin", help="Read reply content from stdin.")] = False,
    reply_type: Annotated[str, typer.Option("--type", help="Reply kind: comment, system, import_source.")] = "comment",
    json_output: Annotated[bool, typer.Option("--json", help="Print the created reply as JSON.")] = False,
    no_attribution: Annotated[
        bool,
        typer.Option(
            "--no-attribution",
            help="Skip session_attribution metadata stamping (see `weave thread create --help`).",
        ),
    ] = False,
) -> None:
    """Post a reply to a thread (decisions, blockers, scope changes).

    Use replies for moments future-you would want to see. Don't use them for
    progress narration — that's session noise, not durable signal.

    **Friendly-names L4 — auto-attribution.** When `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID`
    is set, the reply's metadata_json carries `session_attribution` so the
    audit trail shows "who wrote this reply" at sub-principal granularity.
    Pass `--no-attribution` to suppress.
    """
    if from_stdin:
        if content:
            _console.print("[red]Pick one of <content> / --from-stdin (not both).[/red]")
            raise typer.Exit(2)
        content = sys.stdin.read()
    if not content:
        _console.print("[red]Reply content is required (positional arg or --from-stdin).[/red]")
        raise typer.Exit(2)

    body: dict = {"content": content, "reply_type": reply_type}
    with _client_or_exit() as client:
        thread_uuid = _resolve_thread(client, thread_id)
        # L4 auto-stamp — for replies, attribution lives on the reply's own
        # metadata_json (set at create time, not via PATCH afterward; the
        # ReplyCreate schema accepts the field directly).
        if not no_attribution:
            attribution = _build_session_attribution(client)
            if attribution:
                body["metadata_json"] = {"session_attribution": attribution}
        try:
            reply_obj = client.post(f"/threads/{thread_uuid}/replies", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Reply failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(reply_obj)
        return
    _console.print(f"[green]Reply posted.[/green] [dim]id={reply_obj.get('id')}[/dim]")


# ---------------------------------------------------------------------------
# status verbs (done / close / wont-do)
# ---------------------------------------------------------------------------


def _set_status(thread_id: str, status: str, json_output: bool) -> None:
    with _client_or_exit() as client:
        thread_uuid = _resolve_thread(client, thread_id)
        try:
            thread = client.patch(f"/threads/{thread_uuid}", {"status": status})
        except PowerloomApiError as e:
            _console.print(f"[red]Status update failed:[/red] {e}")
            raise typer.Exit(1) from None
    if json_output:
        _output_json(thread)
        return
    _console.print(f"[green]Status set to {status}.[/green]")
    _print_thread_summary(thread)


@app.command("done")
def done(
    thread_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a thread as done — the work shipped."""
    _set_status(thread_id, "done", json_output)


@app.command("close")
def close(
    thread_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Close a thread — scope abandoned but might still be relevant."""
    _set_status(thread_id, "closed", json_output)


@app.command("wont-do")
def wont_do(
    thread_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a thread as wont_do — decided not to ship."""
    _set_status(thread_id, "wont_do", json_output)


# ---------------------------------------------------------------------------
# update (generic mutation)
# ---------------------------------------------------------------------------


@app.command("update")
def update(
    thread_id: Annotated[str, typer.Argument()],
    title: Annotated[Optional[str], typer.Option("--title")] = None,
    description: Annotated[Optional[str], typer.Option("--description")] = None,
    status: Annotated[Optional[str], typer.Option("--status", help=f"One of: {', '.join(_STATUS_CHOICES)}")] = None,
    priority: Annotated[Optional[str], typer.Option("--priority", help=f"One of: {', '.join(_PRIORITY_CHOICES)}")] = None,
    assigned_to: Annotated[Optional[str], typer.Option("--assigned-to", help="User UUID, or empty string '' to unassign.")] = None,
    milestone_id: Annotated[Optional[str], typer.Option("--milestone-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generic thread update — set any combination of fields via PATCH."""
    if status and status not in _STATUS_CHOICES:
        _console.print(f"[red]Invalid status {status!r}.[/red] One of: {', '.join(_STATUS_CHOICES)}")
        raise typer.Exit(2)
    if priority and priority not in _PRIORITY_CHOICES:
        _console.print(f"[red]Invalid priority {priority!r}.[/red] One of: {', '.join(_PRIORITY_CHOICES)}")
        raise typer.Exit(2)

    body: dict = {}
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    if priority is not None:
        body["priority"] = priority
    if assigned_to is not None:
        body["assigned_to"] = assigned_to or None  # empty string → null (unassign)
    if milestone_id is not None:
        body["milestone_id"] = milestone_id or None

    if not body:
        _console.print("[yellow]No fields to update — pass at least one --field.[/yellow]")
        raise typer.Exit(2)

    with _client_or_exit() as client:
        thread_uuid = _resolve_thread(client, thread_id)
        try:
            thread = client.patch(f"/threads/{thread_uuid}", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Update failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(thread)
        return
    _console.print(f"[green]Updated.[/green]")
    _print_thread_summary(thread)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command("list")
def list_threads(
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Project slug or UUID. Required unless --mine is used.")] = None,
    mine: Annotated[bool, typer.Option("--mine", help="List threads currently assigned to the signed-in user.")] = False,
    status: Annotated[Optional[str], typer.Option("--status", help="Comma-separated status filter (e.g. 'open,in_progress').")] = None,
    priority: Annotated[Optional[str], typer.Option("--priority", help="Comma-separated priority filter.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List tracker threads — by project, by status, or your own queue.

    Examples:
        weave thread list --mine
        weave thread list --project powerloom --status open --priority high,critical
        weave thread list --project powerloom --status in_progress
    """
    with _client_or_exit() as client:
        if mine:
            params: dict = {"limit": limit}
            if status:
                params["status"] = status
            try:
                items = client.get("/threads/my-work", **params)
            except PowerloomApiError as e:
                _console.print(f"[red]Query failed:[/red] {e}")
                raise typer.Exit(1) from None
        else:
            if not project:
                _console.print("[red]--project or --mine is required.[/red]")
                raise typer.Exit(2)
            project_id = _resolve_project(client, project)
            params = {"limit": limit}
            if status:
                params["status"] = status
            if priority:
                params["priority"] = priority
            try:
                items = client.get(f"/projects/{project_id}/threads", **params)
            except PowerloomApiError as e:
                _console.print(f"[red]Query failed:[/red] {e}")
                raise typer.Exit(1) from None

    rows = _rows_from_response(items)

    if json_output:
        _output_json(rows)
        return

    if not rows:
        _console.print("[dim]No threads matched.[/dim]")
        return

    _print_thread_table(rows)


@app.command("my-work")
def my_work(
    status: Annotated[Optional[str], typer.Option("--status", help="Filter by status.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    watch: Annotated[bool, typer.Option("--watch", help="Poll until interrupted.")] = False,
    interval: Annotated[float, typer.Option("--interval", min=1.0, help="Polling interval in seconds.")] = 5.0,
    once: Annotated[bool, typer.Option("--once", help="Poll once and exit. Useful with --watch.")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show threads assigned to, created by, or plucked by the signed-in user."""
    with _client_or_exit() as client:
        try:
            while True:
                params: dict = {"limit": limit}
                if status:
                    params["status"] = status
                rows = _rows_from_response(client.get("/threads/my-work", **params))
                if json_output:
                    _output_json(rows)
                elif watch:
                    _console.print(_my_work_watch_line(rows))
                elif rows:
                    _print_thread_table(rows)
                else:
                    _console.print("[dim]No threads matched.[/dim]")
                if not watch or once:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            typer.echo()
        except PowerloomApiError as e:
            _console.print(f"[red]Query failed:[/red] {e}")
            if e.status_code == 422:
                _console.print(
                    "[yellow]The server may still have /threads/my-work shadowed by /threads/{thread_id}. "
                    "Upgrade/deploy the route-order fix, then retry.[/yellow]"
                )
            raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command("show")
def show(
    thread_id: Annotated[str, typer.Argument(help="Thread reference: UUID, slug (e.g. 'ki-004'), or 'project:slug'.")],
    with_replies: Annotated[bool, typer.Option("--with-replies/--no-replies", help="Include reply timeline.")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show full detail for one thread — fields, metadata, reply timeline."""
    with _client_or_exit() as client:
        thread_uuid = _resolve_thread(client, thread_id)
        try:
            thread = client.get(f"/threads/{thread_uuid}")
        except PowerloomApiError as e:
            _console.print(f"[red]Fetch failed:[/red] {e}")
            raise typer.Exit(1) from None
        replies = []
        if with_replies and not json_output:
            try:
                replies = client.get(f"/threads/{thread_uuid}/replies") or []
            except PowerloomApiError:
                replies = []

    if json_output:
        if with_replies:
            thread = dict(thread)
            thread["replies"] = thread.get("replies") or []
        _output_json(thread)
        return

    _console.print(f"\n[bold]{thread.get('title','(no title)')}[/bold]")
    _console.print(
        f"  [dim]id={thread.get('id')} status={thread.get('status','?')} "
        f"priority={thread.get('priority','?')}[/dim]"
    )
    sp = ((thread.get("metadata_json") or {}).get("session_attribution") or {}).get("subprincipal_name")
    if sp:
        _console.print(f"  [dim]session: {sp}[/dim]")
    _console.print(f"  [dim]assigned_to={thread.get('assigned_to') or '(unassigned)'}[/dim]")
    _console.print(f"  [dim]created_at={thread.get('created_at')}[/dim]")

    desc = thread.get("description") or ""
    if desc:
        _console.print("\n[bold]Description[/bold]")
        _console.print(desc)

    if replies:
        _console.print(f"\n[bold]Replies[/bold] ([dim]{len(replies)}[/dim])")
        for r in replies:
            if not isinstance(r, dict):
                continue
            ts = r.get("created_at", "")[:19]
            kind = r.get("reply_type", "comment")
            _console.print(f"  [cyan]{ts}[/cyan] [dim]({kind})[/dim] {r.get('content','')[:200]}")


# ---------------------------------------------------------------------------
# my-work (richer dedicated subcommand with --watch mode)
# ---------------------------------------------------------------------------
#
# Distinct from `weave thread list --mine` — this one adds watch-mode polling
# + a compact one-line summary view + JSON output. Useful for "I want a
# heads-up display" workflows + CI integrations. Added in PR #25 on top of
# T1's broader `list` subcommand.

import json as _json_mw
import time as _time_mw
from collections import Counter as _Counter_mw


def _fetch_my_work(client, *, status: Optional[str], limit: int) -> list[dict]:
    params: dict = {"limit": limit}
    if status:
        params["status"] = status
    rows = client.get("/threads/my-work", **params)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _print_my_work_table(rows: list[dict]) -> None:
    if not rows:
        _console.print("[dim]No threads in my work.[/dim]")
        return
    table = Table(title=f"My work — {len(rows)} thread(s)", show_header=True)
    for col in ("#", "status", "priority", "title", "updated"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("sequence_number", "")),
            str(row.get("status", "")),
            str(row.get("priority", "")),
            str(row.get("title", ""))[:100],
            str(row.get("updated_at", ""))[:19],
        )
    _console.print(table)


def _watch_line(rows: list[dict]) -> str:
    counts = _Counter_mw(str(row.get("status", "unknown")) for row in rows)
    counts_text = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
    top = rows[0] if rows else {}
    top_label = ""
    if top:
        top_label = (
            f" | top=#{top.get('sequence_number', '')} "
            f"{top.get('status', '')} {str(top.get('title', ''))[:80]}"
        )
    return f"my-work total={len(rows)} statuses={counts_text}{top_label}"


@app.command("my-work")
def my_work(
    status: Annotated[Optional[str], typer.Option("--status", help="Filter by thread status.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    output: Annotated[str, typer.Option("-o", "--output", help="table or json")] = "table",
    watch: Annotated[bool, typer.Option("--watch", help="Poll until interrupted.")] = False,
    interval: Annotated[float, typer.Option("--interval", min=1.0)] = 5.0,
    once: Annotated[bool, typer.Option("--once", help="Poll once and exit (debug; useful with --watch).")] = False,
) -> None:
    """Show tracker threads assigned to, plucked by, or created by me.

    Richer than `weave thread list --mine` — adds watch-mode polling with a
    compact one-line status summary + JSON output for scripting / heads-up
    displays.
    """
    with _client_or_exit() as client:
        try:
            while True:
                rows = _fetch_my_work(client, status=status, limit=limit)
                if output == "json":
                    print(_json_mw.dumps(rows, indent=2, default=str))
                elif watch:
                    _console.print(_watch_line(rows))
                else:
                    _print_my_work_table(rows)
                if not watch or once:
                    break
                _time_mw.sleep(interval)
        except KeyboardInterrupt:
            typer.echo()
        except PowerloomApiError as e:
            _console.print(f"[red]Error:[/red] {e}")
            if e.status_code == 422:
                _console.print(
                    "[yellow]The server may still have /threads/my-work shadowed by "
                    "/threads/{thread_id}. Deploy the route-order fix (Powerloom #143/#145), then retry.[/yellow]"
                )
            raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# W1.5.3 — tree view (`weave thread tree <ref>`, `weave project orphans`)
# ---------------------------------------------------------------------------


def _render_tree_node(node: dict, prefix: str = "", is_last: bool = True) -> None:
    """ASCII-render a single tree node + recurse into children. Style:

      └─ ki-004  Thread title here
          ├─ child-1  Another thread
          └─ child-2  ...
    """
    thread = node.get("thread") or {}
    title = thread.get("title", "(no title)")
    slug = thread.get("slug") or (thread.get("id") or "?")[:8]
    status = thread.get("status", "?")
    pri = thread.get("priority", "?")
    truncated = node.get("truncated_at_depth", False)
    depth = node.get("depth", 0)

    branch = "└─ " if is_last else "├─ "
    if depth == 0:
        # Root has no prefix
        line_prefix = ""
        branch = ""
    else:
        line_prefix = prefix + branch

    trunc_marker = " [yellow](more...)[/yellow]" if truncated else ""
    _console.print(
        f"{line_prefix}[bold cyan]{slug}[/bold cyan] [dim]({status}/{pri})[/dim] "
        f"{title[:80]}{trunc_marker}"
    )
    children = node.get("children") or []
    new_prefix = prefix + ("   " if is_last else "│  ") if depth > 0 else ""
    for i, child in enumerate(children):
        _render_tree_node(
            child, prefix=new_prefix, is_last=(i == len(children) - 1),
        )


@app.command("tree")
def tree(
    thread_id: Annotated[str, typer.Argument(help="Thread reference: UUID, slug, or 'project:slug'.")],
    max_depth: Annotated[int, typer.Option("--max-depth", min=1, max=50, help="Max levels to descend.")] = 10,
    json_output: Annotated[bool, typer.Option("--json", help="Print the raw tree as JSON.")] = False,
) -> None:
    """Print the parent/child tree rooted at <thread_id>.

    Children are merged from `parent_thread_id` (W1.2 column form) and
    `parent_of` dependency edges (W1.5.2 graph form). Nodes whose
    children weren't expanded show "(more...)"; re-run with a higher
    `--max-depth` or rooted at that node to see the missing subtree.
    """
    with _client_or_exit() as client:
        thread_uuid = _resolve_thread(client, thread_id)
        try:
            tree_data = client.get(
                f"/threads/{thread_uuid}/tree", max_depth=max_depth,
            )
        except PowerloomApiError as e:
            _console.print(f"[red]Tree fetch failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(tree_data)
        return
    _render_tree_node(tree_data)


# ---------------------------------------------------------------------------
# Sprint subcommands — sit on `weave thread tree` for now (singular surface)
# but a sprint-rooted tree is its own command for clarity.
# ---------------------------------------------------------------------------


@app.command("sprint-tree")
def sprint_tree_cmd(
    sprint_id: Annotated[str, typer.Argument(help="Sprint UUID. (Slug-form CLI for sprints lands in a follow-up.)")],
    max_depth: Annotated[int, typer.Option("--max-depth", min=1, max=50)] = 10,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Render every top-level thread in the sprint as a tree.

    A "top-level" thread in a sprint is one whose parent isn't also in
    the sprint — so a flat sprint of 5 unrelated threads renders as 5
    single-node trees.
    """
    # Validate UUID shape (sprint slug-resolution lands later)
    try:
        uuid.UUID(sprint_id)
    except ValueError:
        _console.print(
            "[red]Sprint argument must be a UUID[/red] "
            "(slug-form for sprints is on the W1.5 follow-up list)."
        )
        raise typer.Exit(2)

    with _client_or_exit() as client:
        try:
            data = client.get(f"/sprints/{sprint_id}/tree", max_depth=max_depth)
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint tree fetch failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(data)
        return

    sprint = data.get("sprint") or {}
    _console.print(
        f"\n[bold]Sprint:[/bold] {sprint.get('name','(no name)')} "
        f"[dim]({sprint.get('slug','?')}, status={sprint.get('status','?')})[/dim]"
    )
    trees = data.get("trees") or []
    if not trees:
        _console.print("[dim]No threads in this sprint yet.[/dim]")
        return
    for t in trees:
        _console.print()
        _render_tree_node(t)


@app.command("orphans")
def orphans_cmd(
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    include_done: Annotated[bool, typer.Option("--include-done", help="Include done/closed/wont_do threads in the orphan list.")] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 200,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List threads in the project with no parent and no sprint membership.

    The "what's loose right now?" triage view — work that might fall
    through the cracks if nobody picks it up.
    """
    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        try:
            params: dict = {"limit": limit}
            if include_done:
                params["include_done"] = True
            rows = client.get(f"/projects/{project_id}/orphans", **params)
        except PowerloomApiError as e:
            _console.print(f"[red]Orphan fetch failed:[/red] {e}")
            raise typer.Exit(1) from None

    rows = _rows_from_response(rows)
    if json_output:
        _output_json(rows)
        return
    if not rows:
        _console.print(f"[green]No orphan threads in {project!r}.[/green]")
        return

    _console.print(f"\n[bold]Orphan threads in {project!r}[/bold] ([dim]{len(rows)}[/dim])")
    for t in rows:
        slug = t.get("slug") or (t.get("id") or "?")[:8]
        _console.print(
            f"  [cyan]{slug}[/cyan] [dim]({t.get('status','?')}/{t.get('priority','?')})[/dim] "
            f"{t.get('title','')[:80]}"
        )
