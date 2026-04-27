"""`weave sprint create / list / show / update / activate / complete / archive /
add-thread / remove-thread / threads`.

CLI surface for the W1.5.2 sprint subsystem (engine-side: Powerloom #156).
Sprints group threads into time-bounded scopes — "what is this team
shipping in the next 2 weeks?" — and pair with the W1.5.3 hierarchical
tree view for rendering.

Sprint addressing follows the same pattern as W1.5.1 thread slugs: a
sprint can be referenced by UUID, by `project-slug:sprint-slug`, or by
bare slug (using a default project). Sprint slugs auto-generate from the
sprint name on create when not explicitly provided.

Usage examples:

    # Create a sprint
    weave sprint create --project powerloom \\
      --name "v064 cleanup" --start-date 2026-04-27 --end-date 2026-05-10 \\
      --goal "Land Wave 1.5 + ship Alfred reconnect"

    # Find work
    weave sprint list --project powerloom --status active
    weave sprint show v064                              # bare slug
    weave sprint show powerloom:v064                    # explicit project
    weave sprint threads v064                           # list threads in sprint

    # Lifecycle shortcuts (each PATCHes the status)
    weave sprint activate v064
    weave sprint complete v064
    weave sprint archive v064

    # Membership
    weave sprint add-thread v064 ki-004
    weave sprint remove-thread v064 ki-004

JSON output via --json flag for scripting.
"""
from __future__ import annotations

import json as _json
import re as _re_mod
import sys
import uuid
from datetime import date as _date
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


app = typer.Typer(
    help=(
        "Manage Powerloom sprints (create / list / show / update / activate / "
        "complete / archive / add-thread / remove-thread / threads). Engine: "
        "W1.5.2 (Powerloom PR #156)."
    ),
)
_console = Console()

# Status enum matches engine's tracker_sprint.status CHECK constraint
_SPRINT_STATUSES = ("planned", "active", "completed", "archived")


# ---------------------------------------------------------------------------
# Helpers (mirror thread_cmd's resolver pattern)
# ---------------------------------------------------------------------------


def _client_or_exit() -> PowerloomClient:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    return PowerloomClient(cfg)


def _resolve_project(client: PowerloomClient, project: str) -> str:
    """UUID passthrough or slug → UUID. Same shape as thread_cmd."""
    try:
        return str(uuid.UUID(project))
    except ValueError:
        pass
    try:
        projects = client.get("/projects")
    except PowerloomApiError as e:
        _console.print(f"[red]Could not list projects:[/red] {e}")
        raise typer.Exit(1) from None
    items = (
        projects if isinstance(projects, list)
        else (projects.get("items") or projects.get("projects") or [])
    )
    for p in items:
        if isinstance(p, dict) and p.get("slug") == project:
            return p["id"]
    available = sorted({p.get("slug", "?") for p in items if isinstance(p, dict)})
    _console.print(f"[red]No project with slug {project!r}.[/red]")
    if available:
        _console.print(f"[dim]Available slugs: {', '.join(available)}[/dim]")
    raise typer.Exit(1)


def _resolve_sprint(
    client: PowerloomClient,
    ref: str,
    *,
    default_project: str = "powerloom",
) -> str:
    """Accept UUID, `project:slug`, or bare slug; return sprint UUID.

    Mirrors `thread_cmd._resolve_thread`. Slug lookup hits
    GET /projects/{id}/sprints/by-slug/{slug} (added in Powerloom #156).
    """
    # 1. UUID fast path
    try:
        return str(uuid.UUID(ref))
    except ValueError:
        pass

    # 2. project:slug or bare slug
    if ":" in ref:
        project_part, _, slug_part = ref.partition(":")
        project_part = project_part.strip()
        slug_part = slug_part.strip()
        if not project_part or not slug_part:
            _console.print(
                f"[red]Invalid sprint reference {ref!r}.[/red] "
                "Expected `project-slug:sprint-slug`, `sprint-slug`, or a UUID."
            )
            raise typer.Exit(2)
    else:
        project_part = default_project
        slug_part = ref.strip()

    if not _re_mod.match(r"^[a-z0-9][a-z0-9-]{0,62}$", slug_part):
        _console.print(
            f"[red]Invalid sprint slug shape {slug_part!r}.[/red] "
            "Slugs are lowercase alphanumeric + hyphens, 1-63 chars."
        )
        raise typer.Exit(2)

    project_id = _resolve_project(client, project_part)
    try:
        sprint = client.get(f"/projects/{project_id}/sprints/by-slug/{slug_part}")
    except PowerloomApiError as e:
        if e.status_code == 404:
            _console.print(
                f"[red]No sprint with slug {slug_part!r} in project "
                f"{project_part!r}.[/red]"
            )
        else:
            _console.print(f"[red]Sprint slug lookup failed:[/red] {e}")
        raise typer.Exit(1) from None
    return str(sprint["id"])


def _resolve_thread_ref(
    client: PowerloomClient,
    ref: str,
    *,
    default_project: str = "powerloom",
) -> str:
    """Same shape as `_resolve_sprint` but for threads. Used by add-thread /
    remove-thread so users can pass `ki-004` or `powerloom:ki-004` instead
    of UUID. Lifted-and-adapted rather than imported from thread_cmd to
    avoid the circular `weave thread` <-> `weave sprint` import dance."""
    try:
        return str(uuid.UUID(ref))
    except ValueError:
        pass
    if ":" in ref:
        project_part, _, slug_part = ref.partition(":")
        project_part = project_part.strip()
        slug_part = slug_part.strip()
        if not project_part or not slug_part:
            _console.print(f"[red]Invalid thread reference {ref!r}.[/red]")
            raise typer.Exit(2)
    else:
        project_part = default_project
        slug_part = ref.strip()
    if not _re_mod.match(r"^[a-z0-9][a-z0-9-]{0,62}$", slug_part):
        _console.print(f"[red]Invalid thread slug shape {slug_part!r}.[/red]")
        raise typer.Exit(2)
    project_id = _resolve_project(client, project_part)
    try:
        thread = client.get(f"/projects/{project_id}/threads/by-slug/{slug_part}")
    except PowerloomApiError as e:
        if e.status_code == 404:
            _console.print(
                f"[red]No thread with slug {slug_part!r} in project {project_part!r}.[/red]"
            )
        else:
            _console.print(f"[red]Thread slug lookup failed:[/red] {e}")
        raise typer.Exit(1) from None
    return str(thread["id"])


def _output_json(data) -> None:
    """Print data as JSON for --json flag. Handles UUIDs + datetimes."""
    print(_json.dumps(data, indent=2, default=str))


def _print_sprint_summary(s: dict) -> None:
    name = s.get("name", "(no name)")
    slug = s.get("slug") or (s.get("id") or "?")[:8]
    status = s.get("status", "?")
    dates = ""
    if s.get("start_date") or s.get("end_date"):
        dates = f" [dim]({s.get('start_date','?')} → {s.get('end_date','?')})[/dim]"
    _console.print(f"  [bold]{name}[/bold] [cyan]{slug}[/cyan] [dim]({status})[/dim]{dates}")
    if s.get("goal"):
        _console.print(f"  [dim]goal: {s['goal']}[/dim]")
    _console.print(f"  [dim]id={s.get('id')}[/dim]")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@app.command("create")
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Sprint name (required).")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    slug: Annotated[Optional[str], typer.Option("--slug", help="Explicit slug. Auto-generated from name if omitted.")] = None,
    description: Annotated[Optional[str], typer.Option("--description", help="Long-form description.")] = None,
    status: Annotated[str, typer.Option("--status", help=f"One of: {', '.join(_SPRINT_STATUSES)}")] = "planned",
    start_date: Annotated[Optional[str], typer.Option("--start-date", help="ISO date YYYY-MM-DD.")] = None,
    end_date: Annotated[Optional[str], typer.Option("--end-date", help="ISO date YYYY-MM-DD.")] = None,
    goal: Annotated[Optional[str], typer.Option("--goal", help="Short goal statement (surfaces in headers).")] = None,
    milestone: Annotated[Optional[str], typer.Option("--milestone", "-m", help="Nest the sprint under this milestone (UUID, currently UUID-only). Engine validates the milestone belongs to the same project.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print created sprint as JSON.")] = False,
) -> None:
    """Create a sprint in <project>. Slug auto-generates from name when
    omitted (re-uses the same KI-/T1-/v065-/PR-NN pattern detection as
    thread slugs).

    Pass --milestone to nest the sprint under a milestone (Project >
    Milestone > Sprint hierarchy from migration 0068). Today only UUID
    is accepted; slug-based addressing for milestones lands in a
    follow-up.
    """
    if status not in _SPRINT_STATUSES:
        _console.print(f"[red]Invalid status {status!r}.[/red] One of: {', '.join(_SPRINT_STATUSES)}")
        raise typer.Exit(2)

    # Validate --milestone shape (UUID only for now)
    if milestone is not None:
        try:
            uuid.UUID(milestone)
        except ValueError:
            _console.print(
                "[red]--milestone must be a UUID today[/red] "
                "(milestone slug-resolution is on the follow-up list)."
            )
            raise typer.Exit(2)

    body: dict = {"name": name, "status": status}
    if slug is not None:
        body["slug"] = slug
    if description is not None:
        body["description"] = description
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    if goal is not None:
        body["goal"] = goal
    if milestone is not None:
        body["milestone_id"] = milestone

    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        try:
            sprint = client.post(f"/projects/{project_id}/sprints", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint create failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(sprint)
        return
    _console.print("[green]Sprint created.[/green]")
    _print_sprint_summary(sprint)
    _console.print(
        f"  [dim]Next: weave sprint add-thread {sprint.get('slug') or sprint.get('id')} <thread-ref>[/dim]"
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command("list")
def list_sprints(
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    status: Annotated[Optional[str], typer.Option("--status", help="Filter by status.")] = None,
    milestone: Annotated[Optional[str], typer.Option("--milestone", "-m", help="Filter to sprints nested under this milestone (UUID).")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List sprints in a project. Optional --milestone filters to sprints
    nested under that milestone (Project > Milestone > Sprint hierarchy)."""
    if milestone is not None:
        try:
            uuid.UUID(milestone)
        except ValueError:
            _console.print(
                "[red]--milestone must be a UUID today[/red] "
                "(milestone slug-resolution is on the follow-up list)."
            )
            raise typer.Exit(2)
    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        params: dict = {"limit": limit}
        if status is not None:
            params["status"] = status
        if milestone is not None:
            params["milestone_id"] = milestone
        try:
            rows = client.get(f"/projects/{project_id}/sprints", **params)
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint list failed:[/red] {e}")
            raise typer.Exit(1) from None

    sprints = rows if isinstance(rows, list) else (rows.get("items") or [])
    if json_output:
        _output_json(sprints)
        return
    if not sprints:
        _console.print(f"[dim]No sprints in {project!r}{f' with status={status}' if status else ''}.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug", style="cyan", width=18)
    table.add_column("Status", width=10)
    table.add_column("Name", overflow="fold")
    table.add_column("Dates", width=24)
    table.add_column("Goal", overflow="fold")
    for s in sprints:
        if not isinstance(s, dict):
            continue
        dates = ""
        if s.get("start_date") or s.get("end_date"):
            dates = f"{s.get('start_date','?')} → {s.get('end_date','?')}"
        table.add_row(
            (s.get("slug") or "")[:18],
            s.get("status", "?"),
            (s.get("name") or "")[:50],
            dates,
            (s.get("goal") or "")[:50],
        )
    _console.print(table)
    _console.print(f"[dim]{len(sprints)} sprint(s)[/dim]")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command("show")
def show(
    sprint_ref: Annotated[str, typer.Argument(help="Sprint reference: UUID, slug (e.g. 'v064'), or 'project:slug'.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show full detail for one sprint."""
    with _client_or_exit() as client:
        sprint_uuid = _resolve_sprint(client, sprint_ref)
        try:
            sprint = client.get(f"/sprints/{sprint_uuid}")
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint fetch failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output:
        _output_json(sprint)
        return
    _console.print(f"\n[bold]{sprint.get('name','(no name)')}[/bold] [cyan]{sprint.get('slug','?')}[/cyan]")
    _console.print(
        f"  [dim]id={sprint.get('id')} status={sprint.get('status','?')}[/dim]"
    )
    if sprint.get("start_date") or sprint.get("end_date"):
        _console.print(f"  [dim]dates: {sprint.get('start_date','?')} → {sprint.get('end_date','?')}[/dim]")
    if sprint.get("goal"):
        _console.print(f"\n[bold]Goal[/bold]\n{sprint['goal']}")
    if sprint.get("description"):
        _console.print(f"\n[bold]Description[/bold]\n{sprint['description']}")
    _console.print(f"\n[dim]created_at={sprint.get('created_at')}[/dim]")
    if sprint.get("closed_at"):
        _console.print(f"[dim]closed_at={sprint['closed_at']}[/dim]")


# ---------------------------------------------------------------------------
# update + lifecycle shortcuts (activate / complete / archive)
# ---------------------------------------------------------------------------


def _patch_sprint(sprint_ref: str, body: dict, json_output: bool) -> None:
    with _client_or_exit() as client:
        sprint_uuid = _resolve_sprint(client, sprint_ref)
        try:
            sprint = client.patch(f"/sprints/{sprint_uuid}", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint update failed:[/red] {e}")
            raise typer.Exit(1) from None
    if json_output:
        _output_json(sprint)
        return
    _console.print("[green]Updated.[/green]")
    _print_sprint_summary(sprint)


@app.command("update")
def update(
    sprint_ref: Annotated[str, typer.Argument()],
    name: Annotated[Optional[str], typer.Option("--name")] = None,
    slug: Annotated[Optional[str], typer.Option("--slug")] = None,
    description: Annotated[Optional[str], typer.Option("--description")] = None,
    status: Annotated[Optional[str], typer.Option("--status", help=f"One of: {', '.join(_SPRINT_STATUSES)}")] = None,
    start_date: Annotated[Optional[str], typer.Option("--start-date")] = None,
    end_date: Annotated[Optional[str], typer.Option("--end-date")] = None,
    goal: Annotated[Optional[str], typer.Option("--goal")] = None,
    milestone: Annotated[Optional[str], typer.Option("--milestone", "-m", help="Attach the sprint to a milestone (UUID). Engine validates same-project.")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generic sprint update — set any combination of fields via PATCH.

    Pass --milestone <uuid> to attach (or change) the sprint's parent
    milestone (Project > Milestone > Sprint hierarchy from migration 0068).
    Engine rejects cross-project milestone refs.
    """
    if status is not None and status not in _SPRINT_STATUSES:
        _console.print(f"[red]Invalid status {status!r}.[/red] One of: {', '.join(_SPRINT_STATUSES)}")
        raise typer.Exit(2)
    if milestone is not None:
        try:
            uuid.UUID(milestone)
        except ValueError:
            _console.print(
                "[red]--milestone must be a UUID today[/red] "
                "(milestone slug-resolution is on the follow-up list)."
            )
            raise typer.Exit(2)

    body: dict = {}
    if name is not None:
        body["name"] = name
    if slug is not None:
        body["slug"] = slug
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    if goal is not None:
        body["goal"] = goal
    if milestone is not None:
        body["milestone_id"] = milestone

    if not body:
        _console.print("[yellow]No fields to update — pass at least one --field.[/yellow]")
        raise typer.Exit(2)
    _patch_sprint(sprint_ref, body, json_output)


@app.command("activate")
def activate(
    sprint_ref: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a sprint as active (work is in progress)."""
    _patch_sprint(sprint_ref, {"status": "active"}, json_output)


@app.command("complete")
def complete(
    sprint_ref: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a sprint as completed (engine auto-stamps closed_at)."""
    _patch_sprint(sprint_ref, {"status": "completed"}, json_output)


@app.command("archive")
def archive(
    sprint_ref: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a sprint as archived (engine auto-stamps closed_at)."""
    _patch_sprint(sprint_ref, {"status": "archived"}, json_output)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@app.command("delete")
def delete(
    sprint_ref: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a sprint. Cascades to the M2M sprint_threads rows but threads
    themselves are unaffected."""
    if not yes:
        _console.print(
            f"[yellow]This will delete sprint {sprint_ref!r} and its membership rows.[/yellow]"
        )
        _console.print("Re-run with --yes to confirm.")
        raise typer.Exit(2)

    with _client_or_exit() as client:
        sprint_uuid = _resolve_sprint(client, sprint_ref)
        try:
            client.delete(f"/sprints/{sprint_uuid}")
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint delete failed:[/red] {e}")
            raise typer.Exit(1) from None
    _console.print("[green]Sprint deleted.[/green]")


# ---------------------------------------------------------------------------
# membership: add-thread / remove-thread / threads
# ---------------------------------------------------------------------------


@app.command("add-thread")
def add_thread(
    sprint_ref: Annotated[str, typer.Argument(help="Sprint UUID / slug / 'project:slug'.")],
    thread_ref: Annotated[str, typer.Argument(help="Thread UUID / slug / 'project:slug'.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Link a thread to a sprint. Idempotent — already-present pairs return
    success, not 409."""
    with _client_or_exit() as client:
        sprint_uuid = _resolve_sprint(client, sprint_ref)
        thread_uuid = _resolve_thread_ref(client, thread_ref)
        try:
            thread = client.post(
                f"/sprints/{sprint_uuid}/threads",
                {"thread_id": thread_uuid},
            )
        except PowerloomApiError as e:
            _console.print(f"[red]Add-thread failed:[/red] {e}")
            raise typer.Exit(1) from None
    if json_output:
        _output_json(thread)
        return
    _console.print(
        f"[green]Added thread {thread.get('slug') or thread_uuid[:8]} to sprint.[/green]"
    )


@app.command("remove-thread")
def remove_thread(
    sprint_ref: Annotated[str, typer.Argument()],
    thread_ref: Annotated[str, typer.Argument()],
) -> None:
    """Unlink a thread from a sprint. Thread itself is unaffected."""
    with _client_or_exit() as client:
        sprint_uuid = _resolve_sprint(client, sprint_ref)
        thread_uuid = _resolve_thread_ref(client, thread_ref)
        try:
            client.delete(f"/sprints/{sprint_uuid}/threads/{thread_uuid}")
        except PowerloomApiError as e:
            _console.print(f"[red]Remove-thread failed:[/red] {e}")
            raise typer.Exit(1) from None
    _console.print("[green]Thread removed from sprint.[/green]")


@app.command("threads")
def threads(
    sprint_ref: Annotated[str, typer.Argument()],
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 200,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List threads in a sprint, sorted by priority then created_at."""
    with _client_or_exit() as client:
        sprint_uuid = _resolve_sprint(client, sprint_ref)
        try:
            rows = client.get(f"/sprints/{sprint_uuid}/threads", limit=limit)
        except PowerloomApiError as e:
            _console.print(f"[red]Sprint thread list failed:[/red] {e}")
            raise typer.Exit(1) from None

    items = rows if isinstance(rows, list) else (rows.get("items") or [])
    if json_output:
        _output_json(items)
        return
    if not items:
        _console.print("[dim]No threads in this sprint.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug", style="cyan", width=18)
    table.add_column("Status", width=14)
    table.add_column("Pri", width=8)
    table.add_column("Title", overflow="fold")
    for t in items:
        if not isinstance(t, dict):
            continue
        table.add_row(
            (t.get("slug") or (t.get("id") or "?")[:8])[:18],
            t.get("status", "?"),
            t.get("priority", "?"),
            (t.get("title") or "")[:80],
        )
    _console.print(table)
    _console.print(f"[dim]{len(items)} thread(s) in sprint[/dim]")
