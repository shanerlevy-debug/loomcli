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
import sys
import uuid
from datetime import datetime
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


app = typer.Typer(help="Manage Powerloom tracker threads (create / pluck / reply / done / list / show / update). See CLAUDE.md / GEMINI.md / AGENTS.md §4.10.")
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
) -> None:
    """Create a new tracker thread.

    Per CLAUDE.md §4.10, file a thread at session start (or as soon as work
    scope is known). Title should be an imperative phrase; description should
    follow the canonical four-section shape (Reported / Repro / Definition of
    done / Out of scope). See the weave-tracker skill for the description
    template.
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
    thread_id: Annotated[str, typer.Argument(help="UUID of the thread to claim.")],
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
        try:
            thread = client.post(f"/threads/{thread_id}/pluck", pluck_body)
        except PowerloomApiError as e:
            if e.status_code == 409:
                _console.print(f"[yellow]Thread already plucked.[/yellow] Run `weave thread show {thread_id}` to see who has it.")
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
    thread_id: Annotated[str, typer.Argument(help="UUID of the thread.")],
    content: Annotated[Optional[str], typer.Argument(help="Reply text. Use --from-stdin for long content.")] = None,
    from_stdin: Annotated[bool, typer.Option("--from-stdin", help="Read reply content from stdin.")] = False,
    reply_type: Annotated[str, typer.Option("--type", help="Reply kind: comment, system, import_source.")] = "comment",
    json_output: Annotated[bool, typer.Option("--json", help="Print the created reply as JSON.")] = False,
) -> None:
    """Post a reply to a thread (decisions, blockers, scope changes).

    Use replies for moments future-you would want to see. Don't use them for
    progress narration — that's session noise, not durable signal.
    """
    if from_stdin:
        if content:
            _console.print("[red]Pick one of <content> / --from-stdin (not both).[/red]")
            raise typer.Exit(2)
        content = sys.stdin.read()
    if not content:
        _console.print("[red]Reply content is required (positional arg or --from-stdin).[/red]")
        raise typer.Exit(2)

    body = {"content": content, "reply_type": reply_type}
    with _client_or_exit() as client:
        try:
            reply_obj = client.post(f"/threads/{thread_id}/replies", body)
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
        try:
            thread = client.patch(f"/threads/{thread_id}", {"status": status})
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
        try:
            thread = client.patch(f"/threads/{thread_id}", body)
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

    rows = items if isinstance(items, list) else (items.get("items") or items.get("threads") or [])

    if json_output:
        _output_json(rows)
        return

    if not rows:
        _console.print("[dim]No threads matched.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", style="cyan", width=14)
    table.add_column("Pri", width=8)
    table.add_column("Title", overflow="fold")
    table.add_column("Owner", overflow="fold", width=30)
    table.add_column("ID", overflow="fold", width=10)
    for t in rows:
        if not isinstance(t, dict):
            continue
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


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command("show")
def show(
    thread_id: Annotated[str, typer.Argument()],
    with_replies: Annotated[bool, typer.Option("--with-replies/--no-replies", help="Include reply timeline.")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show full detail for one thread — fields, metadata, reply timeline."""
    with _client_or_exit() as client:
        try:
            thread = client.get(f"/threads/{thread_id}")
        except PowerloomApiError as e:
            _console.print(f"[red]Fetch failed:[/red] {e}")
            raise typer.Exit(1) from None
        replies = []
        if with_replies and not json_output:
            try:
                replies = client.get(f"/threads/{thread_id}/replies") or []
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
