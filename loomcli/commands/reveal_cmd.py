"""``weave reveal`` — open the worktree of the current/specified session in the OS file manager.

Sprint polish-doctor-resume-20260430, thread f6d8f7b0.

Addresses the "wait, my code is in `~/.powerloom/`?" confusion that
``weave open`` introduces by parking worktrees under the user's
config dir. With one ``weave reveal`` the user sees their actual
working tree in Finder / Explorer / their default file manager.

Resolution order:
  1. ``weave reveal`` (no args) — read ``.powerloom-session.env`` from
     cwd or any ancestor and use ``POWERLOOM_SESSION_ID`` to find the
     worktree. Most common path; ``cd`` into a worktree, run reveal.
  2. ``weave reveal <session-id>`` — explicit session UUID. Scans
     ``~/.powerloom/worktrees/`` for a matching env file.

Cross-platform:
  * Windows → ``explorer.exe``
  * macOS → ``open``
  * Linux + others → ``xdg-open``
  * No GUI handler → print the path so the user can paste it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from loomcli._open.git_ops import WeaveOpenPaths
from loomcli._open.session_reg import SESSION_ENV_FILENAME
from loomcli._open.resume import find_by_session_id


_console = Console()
_err = Console(stderr=True)


def reveal_command(
    session_id: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Session UUID to reveal. When omitted, reads the "
                ".powerloom-session.env in cwd (or any ancestor)."
            ),
        ),
    ] = None,
    print_only: Annotated[
        bool,
        typer.Option(
            "--print-only",
            help="Print the path; don't try to launch a file manager.",
        ),
    ] = False,
) -> None:
    """Open the worktree in the OS file manager (or print the path)."""
    target = _resolve_target(session_id)
    if target is None:
        _err.print(
            "[red]error:[/red] couldn't find a session to reveal.\n"
            "  [dim]hint:[/dim] cd into a Powerloom worktree, or pass "
            "the session UUID explicitly."
        )
        raise typer.Exit(1)

    _console.print(f"[green]✓[/green] Worktree: {target}")
    if print_only:
        return

    handler = _file_manager_command()
    if handler is None:
        # No GUI handler — printing was the best we could do.
        _console.print(
            "[dim](no OS file-manager handler detected; "
            "path printed above for manual navigation)[/dim]"
        )
        return

    try:
        subprocess.Popen(  # noqa: S603 — fixed binary, sanitized arg
            [*handler, str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        _err.print(f"[yellow]warn:[/yellow] couldn't launch handler: {exc}")
        # Path was already printed above; user can navigate manually.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_target(session_id: Optional[str]) -> Optional[Path]:
    """Return the worktree path for either an explicit session_id or cwd ancestor."""
    if session_id:
        try:
            target = find_by_session_id(
                WeaveOpenPaths.default().worktrees_root, session_id
            )
        except Exception:  # noqa: BLE001
            return None
        return target.worktree

    # Walk up from cwd looking for ``.powerloom-session.env``.
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        env_file = candidate / SESSION_ENV_FILENAME
        if env_file.is_file():
            return candidate
    return None


def _file_manager_command() -> Optional[list[str]]:
    """Return the ``[binary, *args]`` to use, or None when nothing's available."""
    if sys.platform == "win32":
        # explorer.exe is in System32 — always present on Windows.
        return ["explorer"]
    if sys.platform == "darwin":
        return ["open"]
    # Linux + other Unix — try xdg-open, fall back to gio if present,
    # then nothing (path-print only).
    if shutil.which("xdg-open"):
        return ["xdg-open"]
    if shutil.which("gio"):
        return ["gio", "open"]
    return None
