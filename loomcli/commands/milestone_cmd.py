"""`weave milestone ...` commands for tracker milestone management."""
from __future__ import annotations

import json as _json
import os
import uuid
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


app = typer.Typer(help="Manage tracker milestones (ls / create / show / update / close / reopen).")
_console = Console()


def _client_or_exit() -> PowerloomClient:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in - run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    return PowerloomClient(cfg)


def _output_format() -> str:
    cfg = load_runtime_config()
    return os.environ.get("POWERLOOM_FORMAT") or (cfg.default_output or "table")


def _items_from_response(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("items", "milestones", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _resolve_project(client: PowerloomClient, project: str) -> str:
    try:
        return str(uuid.UUID(project))
    except ValueError:
        pass

    try:
        projects = client.get("/projects")
    except PowerloomApiError as e:
        _console.print(f"[red]Could not list projects:[/red] {e}")
        raise typer.Exit(1) from None

    items = _items_from_response(projects)
    for p in items:
        if p.get("slug") == project:
            return str(p["id"])
    available = sorted({str(p.get("slug", "?")) for p in items})
    _console.print(f"[red]No project with slug {project!r}.[/red]")
    if available:
        _console.print(f"[dim]Available slugs: {', '.join(available)}[/dim]")
    raise typer.Exit(1)


def _list_milestones(client: PowerloomClient, project_id: str) -> list[dict]:
    try:
        payload = client.get(f"/projects/{project_id}/milestones")
    except PowerloomApiError as e:
        _console.print(f"[red]Could not list milestones:[/red] {e}")
        raise typer.Exit(1) from None
    return _items_from_response(payload)


def _match_milestone(items: list[dict], ref: str) -> dict:
    """Resolve a milestone by UUID, unique id prefix, or unique title.

    Milestones do not currently have slugs in the engine. Exact title matching
    gives agents a practical, copy-pasteable reference while keeping ambiguity
    explicit.
    """
    try:
        wanted = str(uuid.UUID(ref))
        for item in items:
            if str(item.get("id")) == wanted:
                return item
    except ValueError:
        pass

    id_prefix_matches = [item for item in items if str(item.get("id", "")).startswith(ref)]
    if len(id_prefix_matches) == 1:
        return id_prefix_matches[0]
    if len(id_prefix_matches) > 1:
        _console.print(f"[red]Milestone id prefix {ref!r} is ambiguous.[/red]")
        raise typer.Exit(2)

    exact = [item for item in items if str(item.get("title", "")) == ref]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        _console.print(f"[red]Milestone title {ref!r} is ambiguous.[/red]")
        raise typer.Exit(2)

    folded = ref.casefold()
    casefolded = [item for item in items if str(item.get("title", "")).casefold() == folded]
    if len(casefolded) == 1:
        return casefolded[0]
    if len(casefolded) > 1:
        _console.print(f"[red]Milestone title {ref!r} is ambiguous.[/red]")
        raise typer.Exit(2)

    _console.print(f"[red]No milestone matching {ref!r}.[/red]")
    if items:
        titles = sorted(str(item.get("title", "")) for item in items if item.get("title"))
        _console.print(f"[dim]Available titles: {', '.join(titles)}[/dim]")
    raise typer.Exit(1)


def resolve_milestone_ref(client: PowerloomClient, project_id: str, ref: str) -> str:
    """Return a milestone UUID for UUID/id-prefix/title references.

    This is used by `weave sprint --milestone` so agents can create a milestone
    once and then attach sprints by title without copying UUIDs around.
    """
    try:
        return str(uuid.UUID(ref))
    except ValueError:
        pass
    items = _list_milestones(client, project_id)
    return str(_match_milestone(items, ref)["id"])


def _print_json(data: Any) -> None:
    _console.print_json(_json.dumps(data, default=str))


def _print_table(items: list[dict]) -> None:
    table = Table(title=f"milestones - {len(items)} row(s)", show_header=True)
    table.add_column("Title", overflow="fold")
    table.add_column("Status", width=10)
    table.add_column("Target", width=20)
    table.add_column("ID", width=36)
    for item in items:
        table.add_row(
            str(item.get("title", "")),
            str(item.get("status", "")),
            str(item.get("target_date") or ""),
            str(item.get("id", "")),
        )
    _console.print(table)


@app.command("ls")
def ls(
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    status: Annotated[Optional[str], typer.Option("--status", help="Optional client-side status filter: open or closed.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """List milestones in a project."""
    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        items = _list_milestones(client, project_id)

    if status is not None:
        items = [item for item in items if item.get("status") == status]

    fmt = "json" if json_output else _output_format()
    if fmt == "json":
        _print_json(items)
        return
    if not items:
        _console.print(f"[dim]No milestones in {project!r}.[/dim]")
        return
    _print_table(items)


@app.command("create")
def create(
    title: Annotated[str, typer.Option("--title", "-t", help="Milestone title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    description: Annotated[Optional[str], typer.Option("--description", "-d", help="Milestone description.")] = None,
    target_date: Annotated[Optional[str], typer.Option("--target-date", help="Target datetime/date accepted by the API.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print created milestone as JSON.")] = False,
) -> None:
    """Create a milestone under a project."""
    body: dict[str, Any] = {"title": title}
    if description is not None:
        body["description"] = description
    if target_date is not None:
        body["target_date"] = target_date

    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        try:
            milestone = client.post(f"/projects/{project_id}/milestones", body)
        except PowerloomApiError as e:
            _console.print(f"[red]Milestone create failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output or _output_format() == "json":
        _print_json(milestone)
        return
    _console.print("[green]Milestone created.[/green]")
    _print_table([milestone])


@app.command("show")
def show(
    milestone_ref: Annotated[str, typer.Argument(help="Milestone UUID, id prefix, or exact title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """Show one milestone."""
    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        milestone = _match_milestone(_list_milestones(client, project_id), milestone_ref)

    if json_output or _output_format() == "json":
        _print_json(milestone)
        return
    _print_table([milestone])
    if milestone.get("description"):
        _console.print(f"\n[bold]Description[/bold]\n{milestone['description']}")


def _patch_milestone(project: str, milestone_ref: str, body: dict[str, Any], json_output: bool) -> None:
    with _client_or_exit() as client:
        project_id = _resolve_project(client, project)
        milestone_id = resolve_milestone_ref(client, project_id, milestone_ref)
        try:
            milestone = client.patch(
                f"/projects/{project_id}/milestones/{milestone_id}",
                body,
            )
        except PowerloomApiError as e:
            _console.print(f"[red]Milestone update failed:[/red] {e}")
            raise typer.Exit(1) from None

    if json_output or _output_format() == "json":
        _print_json(milestone)
        return
    _console.print("[green]Milestone updated.[/green]")
    _print_table([milestone])


@app.command("update")
def update(
    milestone_ref: Annotated[str, typer.Argument(help="Milestone UUID, id prefix, or exact title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    title: Annotated[Optional[str], typer.Option("--title", "-t")] = None,
    description: Annotated[Optional[str], typer.Option("--description", "-d")] = None,
    target_date: Annotated[Optional[str], typer.Option("--target-date")] = None,
    status: Annotated[Optional[str], typer.Option("--status", help="open or closed")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """Update a milestone."""
    if status is not None and status not in ("open", "closed"):
        _console.print("[red]--status must be open or closed.[/red]")
        raise typer.Exit(2)
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if target_date is not None:
        body["target_date"] = target_date
    if status is not None:
        body["status"] = status
    if not body:
        _console.print("[yellow]No fields to update - pass at least one --field.[/yellow]")
        raise typer.Exit(2)
    _patch_milestone(project, milestone_ref, body, json_output)


@app.command("close")
def close(
    milestone_ref: Annotated[str, typer.Argument(help="Milestone UUID, id prefix, or exact title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """Mark a milestone closed."""
    _patch_milestone(project, milestone_ref, {"status": "closed"}, json_output)


@app.command("reopen")
def reopen(
    milestone_ref: Annotated[str, typer.Argument(help="Milestone UUID, id prefix, or exact title.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project slug or UUID.")] = "powerloom",
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """Mark a milestone open."""
    _patch_milestone(project, milestone_ref, {"status": "open"}, json_output)
