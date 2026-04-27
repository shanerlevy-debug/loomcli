"""`weave project ls / show` — discover and inspect tracker projects.

Until this command shipped, the only way to find a project was to already
know its slug (every other tracker command takes `--project <slug>` and
errors if you pick a wrong one). The CLI's slug→UUID resolver did
internally hit `GET /projects` for that purpose, but the result wasn't
exposed.

`weave get projects` also wires in via `get.py`'s _LISTABLE map for
parity with `weave get agents` / `weave get skills` / etc.

Usage:

    weave project ls                # all projects you can see
    weave project ls --json         # same, JSON shape
    weave project show powerloom    # full detail by slug
    weave project show <uuid>       # full detail by UUID

Output respects the auto-JSON detection in cli._apply_global_options
(non-TTY / agent sessions get JSON automatically).
"""
from __future__ import annotations

import json as _json
import uuid
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config


app = typer.Typer(help="Inspect tracker projects (ls / show).")
_console = Console()


def _client_or_exit() -> PowerloomClient:
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    return PowerloomClient(cfg)


def _output_format() -> str:
    """Resolve the active output format. Mirrors thread_cmd's choice — env wins
    over the load_runtime_config default since cli._apply_global_options sets
    POWERLOOM_FORMAT after the fact."""
    cfg = load_runtime_config()
    import os
    return os.environ.get("POWERLOOM_FORMAT") or (cfg.default_output or "table")


def _items_from_response(payload: Any) -> list[dict]:
    """The /projects endpoint may return a bare list or {'items': [...]}.
    Normalize either shape into a list of dicts."""
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in ("items", "projects", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [p for p in value if isinstance(p, dict)]
    return []


@app.command("ls")
def ls(
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """List tracker projects visible to the current user."""
    with _client_or_exit() as client:
        try:
            payload = client.get("/projects")
        except PowerloomApiError as e:
            _console.print(f"[red]Could not list projects:[/red] {e}")
            raise typer.Exit(1) from None

    items = _items_from_response(payload)
    fmt = "json" if json_output else _output_format()

    if fmt == "json":
        _console.print_json(_json.dumps(items))
        return

    if not items:
        _console.print("[yellow]No projects visible.[/yellow]")
        return

    table = Table(title=f"projects — {len(items)} row(s)", show_header=True)
    for col in ("slug", "name", "id", "status", "ou_id"):
        table.add_column(col, overflow="fold")
    for p in items:
        table.add_row(
            str(p.get("slug", "")),
            str(p.get("name", "")),
            str(p.get("id", "")),
            str(p.get("status", "")),
            str(p.get("ou_id", "") or "-"),
        )
    _console.print(table)


@app.command("show")
def show(
    project: Annotated[
        str,
        typer.Argument(help="Project slug (e.g. 'powerloom') or UUID."),
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Print as JSON.")] = False,
) -> None:
    """Show full detail for one project."""
    with _client_or_exit() as client:
        # UUID → fetch directly; slug → list and lookup (same fallback every
        # other tracker command uses; engine doesn't expose /projects/by-slug
        # as a first-class GET as of this writing).
        try:
            project_uuid = str(uuid.UUID(project))
            payload = client.get(f"/projects/{project_uuid}")
        except ValueError:
            try:
                items = _items_from_response(client.get("/projects"))
            except PowerloomApiError as e:
                _console.print(f"[red]Could not list projects:[/red] {e}")
                raise typer.Exit(1) from None
            match = next((p for p in items if p.get("slug") == project), None)
            if not match:
                slugs = sorted({p.get("slug", "?") for p in items})
                _console.print(f"[red]No project with slug {project!r}.[/red]")
                if slugs:
                    _console.print(f"[dim]Available slugs: {', '.join(slugs)}[/dim]")
                raise typer.Exit(1)
            payload = match
        except PowerloomApiError as e:
            _console.print(f"[red]Could not fetch project:[/red] {e}")
            raise typer.Exit(1) from None

    fmt = "json" if json_output else _output_format()
    if fmt == "json":
        _console.print_json(_json.dumps(payload))
        return

    _console.print(f"[bold]{payload.get('name', '?')}[/bold]  [dim]({payload.get('slug', '?')})[/dim]")
    _console.print(f"  id     : {payload.get('id', '')}")
    _console.print(f"  status : {payload.get('status', '')}")
    _console.print(f"  ou_id  : {payload.get('ou_id', '') or '-'}")
    if payload.get("description"):
        _console.print(f"  desc   : {payload['description']}")
    if payload.get("created_at"):
        _console.print(f"  created: {payload['created_at']}")
