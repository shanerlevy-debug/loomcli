"""Resume / reuse helpers for ``weave open``.

Both flags short-circuit the launch flow — they skip redeem, clone,
and session-register, jumping straight to the runtime hand-off in an
existing worktree:

  ``--resume <session-id>`` — find a worktree whose
      ``.powerloom-session.env`` carries ``POWERLOOM_SESSION_ID=<id>``.

  ``--reuse <scope>`` — find the latest worktree dir matching
      ``<scope>-*`` under the worktrees root, sorted by mtime.

Returned ``ResumeTarget`` carries the worktree path + the runtime to
exec (parsed from the worktree's env file). Caller passes it to
``runtime_exec.exec_runtime``.

Sprint cli-weave-open-20260430, thread 5790b2d6.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loomcli._open.session_reg import SESSION_ENV_FILENAME


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ResumeError(RuntimeError):
    """No worktree matched the resume / reuse selector."""


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumeTarget:
    worktree: Path
    runtime: str
    scope: Optional[str]
    session_id: Optional[str]
    branch: Optional[str]
    is_dirty: bool


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def _read_env_file(worktree: Path) -> dict[str, str]:
    """Parse ``<worktree>/.powerloom-session.env`` into a dict, empty if missing."""
    target = worktree / SESSION_ENV_FILENAME
    if not target.exists():
        return {}
    out: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _is_dirty(worktree: Path) -> bool:
    """Return True if ``git status --porcelain`` reports any changes.

    Best-effort; on any subprocess failure (git not installed, dir
    isn't a git checkout, anything else) returns False so callers
    don't accidentally block on a false alarm.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return bool(result.stdout.strip())


def _build_target(worktree: Path) -> ResumeTarget:
    env = _read_env_file(worktree)
    return ResumeTarget(
        worktree=worktree,
        runtime=env.get("POWERLOOM_RUNTIME", ""),
        scope=env.get("POWERLOOM_SCOPE"),
        session_id=env.get("POWERLOOM_SESSION_ID"),
        branch=env.get("POWERLOOM_BRANCH"),
        is_dirty=_is_dirty(worktree),
    )


def find_by_session_id(
    worktrees_root: Path,
    session_id: str,
) -> ResumeTarget:
    """Scan worktrees for one whose env file carries ``POWERLOOM_SESSION_ID==session_id``.

    Raises ``ResumeError`` when no worktree matches.
    """
    if not worktrees_root.exists():
        raise ResumeError(
            f"Worktrees root {worktrees_root} doesn't exist — "
            "no sessions to resume."
        )
    for child in sorted(worktrees_root.iterdir()):
        if not child.is_dir():
            continue
        env = _read_env_file(child)
        if env.get("POWERLOOM_SESSION_ID") == session_id:
            return _build_target(child)
    raise ResumeError(f"No worktree found with session_id {session_id!r}.")


def find_by_scope(
    worktrees_root: Path,
    scope_slug: str,
) -> ResumeTarget:
    """Find the most-recent worktree under ``<root>/<scope>-*``.

    "Most recent" = highest ``mtime``. Ties break alphabetically by
    name (stable but rarely hit). Raises ``ResumeError`` when no
    candidate exists.
    """
    if not worktrees_root.exists():
        raise ResumeError(
            f"Worktrees root {worktrees_root} doesn't exist — "
            f"no worktrees for scope {scope_slug!r}."
        )
    candidates = [
        p for p in worktrees_root.iterdir()
        if p.is_dir() and p.name.startswith(f"{scope_slug}-")
    ]
    if not candidates:
        raise ResumeError(
            f"No worktree under {worktrees_root} matches scope {scope_slug!r}."
        )
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return _build_target(candidates[0])
