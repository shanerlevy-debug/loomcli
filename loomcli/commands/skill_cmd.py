"""`weave skill upload / activate / upload-and-activate`.

Closes the gap that `weave apply` leaves open — apply creates/updates
the Skill *shell* (manifest metadata), but archive content (the zip
with SKILL.md + code + prompts) has to be uploaded separately via
REST. Before 0.5.2, that meant curl. These commands wrap it properly.

Usage:
    weave skill upload /ou-path/skill-name ./archive.zip
      → POSTs archive to /skills/{id}/versions; prints the version UUID.
      DOES NOT activate — the skill still has the previous
      current_version_id until you call `weave skill activate`.

    weave skill activate /ou-path/skill-name <version-uuid>
      → PATCHes the skill's current_version_id.

    weave skill upload-and-activate /ou-path/skill-name ./archive.zip
      → Upload + activate in one step. Most common case.

    weave skill versions /ou-path/skill-name
      → List versions for a skill (GET /skills/{id}/versions).

Resource addressing works the same as `weave apply` — the skill is
identified by `{ou_path}/{name}` and resolved via the same
AddressResolver used elsewhere in the CLI.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config
from loomcli.manifest.addressing import AddressResolutionError, AddressResolver

app = typer.Typer(help="Manage Skill archives (upload + activate versions).")
_console = Console()


SKILL_LIST_PATH = "/skills"


def _split_address(address: str) -> tuple[str, str]:
    """Split '/ou/path/skill-name' into (ou_path, skill_name).

    The skill name is the trailing segment; the ou_path is everything
    before it. Validates the shape is usable.
    """
    if not address.startswith("/"):
        raise typer.BadParameter(
            f"Expected absolute path like /ou-path/skill-name, got {address!r}"
        )
    parts = address.rstrip("/").split("/")
    if len(parts) < 3:
        raise typer.BadParameter(
            f"Address {address!r} is missing OU path or skill name. "
            "Expected /ou-path/skill-name (minimum 2 segments after the leading slash)."
        )
    skill_name = parts[-1]
    ou_path = "/".join(parts[:-1])
    return ou_path, skill_name


def _resolve_skill_id(
    client: PowerloomClient, resolver: AddressResolver, ou_path: str, skill_name: str
) -> str:
    """Resolve {ou_path}/{skill_name} to a skill UUID. Raises
    typer.Exit(1) with a helpful error if not found."""
    try:
        ou_id = resolver.ou_path_to_id(ou_path)
    except AddressResolutionError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    skill = resolver.find_in_ou(
        list_path=SKILL_LIST_PATH, ou_id=ou_id, name=skill_name
    )
    if skill is None:
        _console.print(
            f"[red]Skill {skill_name!r} not found in OU {ou_path!r}.[/red]"
        )
        _console.print(
            "[dim]Create it first with `weave apply -f <manifest.yaml>`.[/dim]"
        )
        raise typer.Exit(1)
    return skill["id"]


def _guess_content_type(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "application/gzip"
    if name.endswith(".zip"):
        return "application/zip"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@app.command("upload")
def upload(
    address: str = typer.Argument(
        ...,
        help="Skill address: /ou-path/skill-name (e.g. /bespoke-technology/studio/bespoke-brand-style).",
    ),
    archive: Path = typer.Argument(
        ...,
        help="Path to the archive file (.zip or .tar.gz).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Upload a new version of an existing Skill's archive.

    Does NOT activate the new version. The skill's current_version_id
    stays unchanged until you call `weave skill activate` with the
    version UUID printed here.
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    ou_path, skill_name = _split_address(address)

    archive_bytes = archive.read_bytes()
    content_type = _guess_content_type(archive)
    _console.print(
        f"[dim]Uploading {archive.name} ({len(archive_bytes)} bytes, "
        f"{content_type}) to {ou_path}/{skill_name}...[/dim]"
    )

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        skill_id = _resolve_skill_id(client, resolver, ou_path, skill_name)
        try:
            version = client.post_multipart(
                f"/skills/{skill_id}/versions",
                file_name=archive.name,
                file_bytes=archive_bytes,
                content_type=content_type,
            )
        except PowerloomApiError as e:
            _console.print(f"[red]Upload failed:[/red] {e}")
            raise typer.Exit(1) from None

    version_id = version.get("id") if isinstance(version, dict) else None
    _console.print(f"[green]Uploaded version [bold]{version_id}[/bold].[/green]")
    if version.get("name"):
        _console.print(f"  [dim]skill name (frontmatter):[/dim] {version['name']}")
    if version.get("description"):
        _console.print(f"  [dim]description:[/dim] {version['description']}")
    if version.get("sha256"):
        _console.print(f"  [dim]sha256:[/dim] {version['sha256']}")
    if version.get("size_bytes") is not None:
        _console.print(f"  [dim]size:[/dim] {version['size_bytes']} bytes")
    _console.print()
    _console.print(
        f"[dim]This version is [yellow]not yet active[/yellow]. "
        f"Run `weave skill activate {address} {version_id}` to promote it.[/dim]"
    )


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


@app.command("activate")
def activate(
    address: str = typer.Argument(...),
    version_id: str = typer.Argument(
        ..., help="UUID of the version to promote (from `weave skill upload`)."
    ),
) -> None:
    """Set a skill's current_version_id to the given version UUID."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    ou_path, skill_name = _split_address(address)

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        skill_id = _resolve_skill_id(client, resolver, ou_path, skill_name)
        try:
            client.patch(
                f"/skills/{skill_id}",
                {"current_version_id": version_id},
            )
        except PowerloomApiError as e:
            _console.print(f"[red]Activation failed:[/red] {e}")
            raise typer.Exit(1) from None
    _console.print(
        f"[green]Activated version {version_id} on {address}.[/green]"
    )


# ---------------------------------------------------------------------------
# upload-and-activate
# ---------------------------------------------------------------------------


@app.command("upload-and-activate")
def upload_and_activate(
    address: str = typer.Argument(...),
    archive: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Upload a new version AND set it as the active version.

    The common case. Equivalent to `weave skill upload X Y` followed
    by `weave skill activate X <printed-version-id>`, but in a single
    operation with a single set of diagnostic output.
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    ou_path, skill_name = _split_address(address)

    archive_bytes = archive.read_bytes()
    content_type = _guess_content_type(archive)
    _console.print(
        f"[dim]Uploading + activating {archive.name} "
        f"({len(archive_bytes)} bytes) on {ou_path}/{skill_name}...[/dim]"
    )

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        skill_id = _resolve_skill_id(client, resolver, ou_path, skill_name)
        try:
            version = client.post_multipart(
                f"/skills/{skill_id}/versions",
                file_name=archive.name,
                file_bytes=archive_bytes,
                content_type=content_type,
            )
        except PowerloomApiError as e:
            _console.print(f"[red]Upload failed:[/red] {e}")
            raise typer.Exit(1) from None
        version_id = version.get("id") if isinstance(version, dict) else None
        if not version_id:
            _console.print(
                f"[red]Upload succeeded but response had no version id:[/red] {version}"
            )
            raise typer.Exit(1)
        try:
            client.patch(
                f"/skills/{skill_id}",
                {"current_version_id": version_id},
            )
        except PowerloomApiError as e:
            _console.print(
                f"[red]Uploaded version {version_id} but activation failed:[/red] {e}"
            )
            _console.print(
                f"[yellow]Retry with: weave skill activate {address} {version_id}[/yellow]"
            )
            raise typer.Exit(1) from None

    _console.print(
        f"[green]Uploaded + activated version [bold]{version_id}[/bold] "
        f"on {address}.[/green]"
    )


# ---------------------------------------------------------------------------
# versions (list)
# ---------------------------------------------------------------------------


@app.command("versions")
def versions(
    address: str = typer.Argument(...),
) -> None:
    """List all uploaded versions for a skill."""
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)
    ou_path, skill_name = _split_address(address)

    with PowerloomClient(cfg) as client:
        resolver = AddressResolver(client)
        skill_id = _resolve_skill_id(client, resolver, ou_path, skill_name)
        try:
            items = client.get(f"/skills/{skill_id}/versions")
        except PowerloomApiError as e:
            _console.print(f"[red]List failed:[/red] {e}")
            raise typer.Exit(1) from None

    if not items:
        _console.print("[dim]No versions uploaded yet.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Version ID", overflow="fold")
    table.add_column("Name (frontmatter)")
    table.add_column("SHA256", overflow="fold")
    table.add_column("Size (bytes)")
    table.add_column("Uploaded")
    for v in items:
        if not isinstance(v, dict):
            continue
        table.add_row(
            str(v.get("id", "")),
            v.get("name", "") or "",
            str(v.get("sha256", "") or ""),
            str(v.get("size_bytes", "") or ""),
            str(v.get("created_at", "") or ""),
        )
    _console.print(table)
