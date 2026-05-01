"""Tests for ``loomcli._open.resume``.

Sprint cli-weave-open-20260430, thread 5790b2d6.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from loomcli._open.resume import (
    ResumeError,
    ResumeTarget,
    find_by_scope,
    find_by_session_id,
)
from loomcli._open.session_reg import SESSION_ENV_FILENAME


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_worktree(
    root: Path, name: str, *, session_id: str, runtime: str = "claude_code",
    scope: str | None = None, branch: str | None = None,
) -> Path:
    wt = root / name
    wt.mkdir(parents=True, exist_ok=True)
    env_lines = [
        f"POWERLOOM_SESSION_ID={session_id}",
        f"POWERLOOM_RUNTIME={runtime}",
    ]
    if scope:
        env_lines.append(f"POWERLOOM_SCOPE={scope}")
    if branch:
        env_lines.append(f"POWERLOOM_BRANCH={branch}")
    (wt / SESSION_ENV_FILENAME).write_text(
        "\n".join(env_lines) + "\n", encoding="utf-8"
    )
    return wt


# ---------------------------------------------------------------------------
# find_by_session_id
# ---------------------------------------------------------------------------


def test_find_by_session_id_returns_matching_worktree(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    _make_worktree(root, "cc-x-20260501-aaaa", session_id="sess-1")
    target_dir = _make_worktree(
        root, "cc-y-20260501-bbbb", session_id="sess-2",
        runtime="codex_cli", scope="cc-y-20260501",
    )
    _make_worktree(root, "cc-z-20260501-cccc", session_id="sess-3")

    with patch("loomcli._open.resume._is_dirty", return_value=False):
        target = find_by_session_id(root, "sess-2")

    assert isinstance(target, ResumeTarget)
    assert target.worktree == target_dir
    assert target.session_id == "sess-2"
    assert target.runtime == "codex_cli"
    assert target.scope == "cc-y-20260501"


def test_find_by_session_id_no_match_raises(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    _make_worktree(root, "cc-x-aaaa", session_id="sess-1")

    with pytest.raises(ResumeError):
        find_by_session_id(root, "no-such-session")


def test_find_by_session_id_root_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ResumeError):
        find_by_session_id(tmp_path / "nope", "sess-1")


def test_find_by_session_id_skips_worktrees_with_no_env_file(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    (root / "stale-no-env-file").mkdir()  # no .powerloom-session.env
    target_dir = _make_worktree(root, "live-aaaa", session_id="match")

    with patch("loomcli._open.resume._is_dirty", return_value=False):
        target = find_by_session_id(root, "match")
    assert target.worktree == target_dir


# ---------------------------------------------------------------------------
# find_by_scope
# ---------------------------------------------------------------------------


def test_find_by_scope_picks_latest_mtime(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    older = _make_worktree(
        root, "cc-x-20260501-aaaa", session_id="s1", scope="cc-x-20260501"
    )
    # Touch older to be older.
    old_time = time.time() - 600
    import os
    os.utime(older, (old_time, old_time))

    newer = _make_worktree(
        root, "cc-x-20260501-bbbb", session_id="s2", scope="cc-x-20260501"
    )

    with patch("loomcli._open.resume._is_dirty", return_value=False):
        target = find_by_scope(root, "cc-x-20260501")
    assert target.worktree == newer


def test_find_by_scope_dirty_flag_propagates(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    _make_worktree(
        root, "cc-x-20260501-aaaa", session_id="s1", scope="cc-x-20260501"
    )

    with patch("loomcli._open.resume._is_dirty", return_value=True):
        target = find_by_scope(root, "cc-x-20260501")
    assert target.is_dirty is True


def test_find_by_scope_no_candidates_raises(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    _make_worktree(root, "other-scope-aaaa", session_id="s1")

    with pytest.raises(ResumeError):
        find_by_scope(root, "cc-x-20260501")


def test_find_by_scope_root_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ResumeError):
        find_by_scope(tmp_path / "nope", "cc-x-20260501")


def test_find_by_scope_only_matches_scope_prefix(tmp_path: Path) -> None:
    """Scope `cc-x` shouldn't pick up `cc-x-similar` — must be `<scope>-*`."""
    root = tmp_path / "worktrees"
    # cc-x-similar starts with "cc-x-" so technically matches the prefix.
    # But cc-xy-… does NOT — stride confirms we anchor on the dash.
    _make_worktree(root, "cc-xy-20260501-aaaa", session_id="s1")

    with pytest.raises(ResumeError):
        find_by_scope(root, "cc-x")
