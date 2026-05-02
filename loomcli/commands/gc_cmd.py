"""``weave gc`` — clean up abandoned worktrees + launch-spec cache entries.

Sprint polish-doctor-resume-20260430, thread 7a81d721.

Without periodic GC, ``~/.powerloom/worktrees/`` grows unbounded:
every ``weave open`` mints a fresh worktree, sessions end (merged
or abandoned) but the directory lingers. ``weave gc`` lists and
optionally removes worktrees whose underlying agent-session is
``abandoned`` / ``merged`` AND whose mtime is older than 30 days.

Two modes:
  * ``weave gc`` (default) — dry-run report. Shows what would be
    removed; doesn't touch disk.
  * ``weave gc --apply`` — actually removes the worktrees + clears
    matching launch-spec cache entries. Bare clones are also pruned
    when no worktrees remain for a project.

Active sessions are skipped by default. ``--include-active`` lists
them with a warning; combined with ``--apply`` it requires a
typed-confirmation to proceed (we don't want to nuke a worktree the
user is actively coding in).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli._open.git_ops import WeaveOpenPaths
from loomcli._open.launch_cache import _cache_root, prune_expired
from loomcli._open.session_reg import SESSION_ENV_FILENAME


_console = Console()
_err = Console(stderr=True)


# Worktree mtime cutoff — anything older AND backed by a non-active
# session is fair game. Aligns with the milestone plan doc's stance
# that 30 days of idle is "abandoned in practice."
ABANDONED_MTIME_DAYS = 30


def gc_command(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Actually remove the listed worktrees (and matching "
                "cache entries). Without this flag, gc is a dry-run."
            ),
        ),
    ] = False,
    include_active: Annotated[
        bool,
        typer.Option(
            "--include-active",
            help=(
                "Also list worktrees with status=in_progress sessions. "
                "Combined with --apply, requires typed confirmation "
                "before removal — we don't want to nuke a worktree the "
                "user is actively coding in."
            ),
        ),
    ] = False,
    older_than_days: Annotated[
        int,
        typer.Option(
            "--older-than-days",
            help=(
                "Override the default 30-day mtime cutoff for "
                "abandonment. Lower for faster turnover; higher to "
                "keep stale-but-not-truly-abandoned worktrees around."
            ),
        ),
    ] = ABANDONED_MTIME_DAYS,
) -> None:
    """List (and optionally remove) abandoned `weave open` worktrees."""
    paths = WeaveOpenPaths.default()
    if not paths.worktrees_root.is_dir():
        _console.print("[dim]No worktrees root yet — nothing to gc.[/dim]")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    candidates = _scan_worktrees(paths.worktrees_root, cutoff, include_active)
    if not candidates:
        _console.print(
            f"[green]✓[/green] No worktrees older than {older_than_days} "
            "days with abandoned sessions."
        )
        # Still prune expired cache entries — they accumulate from
        # tokens that never made it to a worktree.
        pruned = prune_expired()
        if pruned:
            _console.print(
                f"  [dim]Pruned {pruned} expired launch-spec cache entries.[/dim]"
            )
        return

    table = Table(title="Worktrees up for collection", show_header=True)
    table.add_column("Worktree")
    table.add_column("Mtime (days ago)")
    table.add_column("Session status")
    table.add_column("Scope")
    for cand in candidates:
        table.add_row(
            str(cand.path),
            str(cand.age_days),
            cand.session_status or "(no env file)",
            cand.scope or "?",
        )
    _console.print(table)

    if not apply:
        _console.print(
            "[dim]Dry run — pass `--apply` to remove these worktrees.[/dim]"
        )
        return

    if any(c.session_status == "in_progress" for c in candidates):
        _err.print(
            "[yellow]warn:[/yellow] one or more candidates have "
            "in_progress sessions. Removing them will orphan live agent "
            "work."
        )
        confirm = typer.prompt(
            "Type 'remove-active' to proceed",
            default="",
            show_default=False,
        )
        if confirm != "remove-active":
            _console.print("[dim]Aborted; nothing removed.[/dim]")
            raise typer.Exit(1)

    removed = 0
    for cand in candidates:
        if _remove_worktree(paths, cand):
            removed += 1

    cache_removed = prune_expired()
    bare_removed = _maybe_prune_bare_clones(paths)

    _console.print(
        f"[green]✓[/green] Removed {removed} worktree(s); "
        f"pruned {cache_removed} expired launch-cache entr"
        f"{'y' if cache_removed == 1 else 'ies'}; "
        f"removed {bare_removed} unused bare clone(s)."
    )


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class _Candidate:
    __slots__ = ("path", "age_days", "session_status", "scope")

    def __init__(
        self,
        path: Path,
        age_days: int,
        session_status: Optional[str],
        scope: Optional[str],
    ):
        self.path = path
        self.age_days = age_days
        self.session_status = session_status
        self.scope = scope


def _scan_worktrees(
    worktrees_root: Path,
    cutoff: datetime,
    include_active: bool,
) -> list[_Candidate]:
    """Find worktree dirs older than ``cutoff`` whose session is not active.

    Sessions whose env file is missing entirely are treated as "abandoned"
    — partial-bootstrap leftovers usually fall here.
    """
    out: list[_Candidate] = []
    for child in sorted(worktrees_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime > cutoff:
            continue
        env = _read_session_env(child)
        status = env.get("POWERLOOM_SESSION_STATUS")
        scope = env.get("POWERLOOM_SCOPE")
        # Without a status field, treat as abandoned (legacy / partial).
        if status == "in_progress" and not include_active:
            continue
        age_days = (datetime.now(timezone.utc) - mtime).days
        out.append(_Candidate(child, age_days, status, scope))
    return out


def _read_session_env(worktree: Path) -> dict[str, str]:
    target = worktree / SESSION_ENV_FILENAME
    if not target.is_file():
        return {}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def _remove_worktree(paths: WeaveOpenPaths, cand: _Candidate) -> bool:
    """``git worktree remove`` + fall back to ``rmtree`` on failure."""
    # Best-effort: ask git to deregister the worktree first so the
    # bare clone's metadata stays clean.
    git = shutil.which("git")
    if git:
        for repo in _bare_clones(paths):
            try:
                subprocess.run(  # noqa: S603 — fixed binaries
                    [git, "worktree", "remove", "--force", str(cand.path)],
                    cwd=str(repo),
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
    # Even after a successful `git worktree remove` the dir is gone,
    # but if git couldn't find it (cross-clone confusion, missing
    # admin metadata) we still want the dir removed.
    try:
        if cand.path.exists():
            shutil.rmtree(cand.path, ignore_errors=False)
    except OSError as exc:
        _err.print(
            f"[yellow]warn:[/yellow] couldn't remove {cand.path}: {exc}"
        )
        return False
    return True


def _maybe_prune_bare_clones(paths: WeaveOpenPaths) -> int:
    """Drop bare clones with no surviving worktrees. Returns the count removed."""
    if not paths.repos_root.is_dir():
        return 0
    surviving_clones = {p.name for p in _bare_clones(paths)}
    if not surviving_clones:
        return 0
    # Heuristic: a bare clone is "in use" iff any worktree dir still
    # carries its project_slug embedded in the env file's
    # POWERLOOM_PROJECT_ID OR POWERLOOM_SCOPE prefix. We don't have a
    # direct slug → clone-name map, so we check `git worktree list`
    # output for each clone and remove ones with no worktrees.
    git = shutil.which("git")
    if not git:
        return 0
    removed = 0
    for clone in _bare_clones(paths):
        try:
            res = subprocess.run(  # noqa: S603 — fixed binaries
                [git, "worktree", "list", "--porcelain"],
                cwd=str(clone),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        # `git worktree list` always reports the bare clone itself
        # plus any registered worktrees. >1 entry = real worktrees;
        # exactly 1 = just the bare → safe to drop.
        worktree_lines = [
            ln for ln in (res.stdout or "").splitlines()
            if ln.startswith("worktree ")
        ]
        if len(worktree_lines) > 1:
            continue
        try:
            shutil.rmtree(clone, ignore_errors=False)
            removed += 1
        except OSError as exc:
            _err.print(
                f"[yellow]warn:[/yellow] couldn't remove bare clone "
                f"{clone}: {exc}"
            )
    return removed


def _bare_clones(paths: WeaveOpenPaths) -> list[Path]:
    if not paths.repos_root.is_dir():
        return []
    return [
        p for p in paths.repos_root.iterdir()
        if p.is_dir() and p.suffix == ".git"
    ]
