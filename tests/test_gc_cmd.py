"""Tests for ``weave gc``.

Sprint polish-doctor-resume-20260430, thread 7a81d721.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli._open.git_ops import WeaveOpenPaths
from loomcli._open.session_reg import SESSION_ENV_FILENAME


runner = CliRunner()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_paths(tmp_path):
    """Redirect WeaveOpenPaths.default to tmp dirs."""
    paths = WeaveOpenPaths(
        repos_root=tmp_path / "repos",
        worktrees_root=tmp_path / "worktrees",
    )
    with patch.object(WeaveOpenPaths, "default", return_value=paths):
        yield paths


def _seed_worktree(
    root: Path,
    name: str,
    *,
    age_days: int,
    session_status: str | None,
    scope: str = "cc-x-20260101",
) -> Path:
    wt = root / name
    wt.mkdir(parents=True, exist_ok=True)
    if session_status is not None:
        (wt / SESSION_ENV_FILENAME).write_text(
            f"POWERLOOM_SESSION_ID=s-{name}\n"
            f"POWERLOOM_SESSION_STATUS={session_status}\n"
            f"POWERLOOM_SCOPE={scope}\n"
            "POWERLOOM_RUNTIME=claude_code\n",
            encoding="utf-8",
        )
    target_t = time.time() - age_days * 86400
    os.utime(wt, (target_t, target_t))
    return wt


# ---------------------------------------------------------------------------
# gc — dry-run report
# ---------------------------------------------------------------------------


def test_gc_no_worktrees_root_says_so(tmp_path: Path) -> None:
    """No `~/.powerloom/worktrees` yet → "nothing to gc" message."""
    paths = WeaveOpenPaths(
        repos_root=tmp_path / "nope-repos",
        worktrees_root=tmp_path / "nope-worktrees",
    )
    with patch.object(WeaveOpenPaths, "default", return_value=paths):
        result = runner.invoke(app, ["gc"])
    assert result.exit_code == 0
    assert "Nothing to gc" in result.output or "nothing to gc" in result.output


def test_gc_with_no_candidates_succeeds(fake_paths: WeaveOpenPaths) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "active",
        age_days=1, session_status="in_progress",
    )
    result = runner.invoke(app, ["gc"])
    assert result.exit_code == 0
    assert "No worktrees older" in result.output


def test_gc_lists_abandoned_worktrees(fake_paths: WeaveOpenPaths) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "old-merged",
        age_days=45, session_status="merged",
    )
    _seed_worktree(
        fake_paths.worktrees_root, "old-abandoned",
        age_days=60, session_status="abandoned",
    )
    _seed_worktree(
        fake_paths.worktrees_root, "young",
        age_days=1, session_status="merged",
    )
    result = runner.invoke(app, ["gc"])
    assert result.exit_code == 0
    # Rich truncates long Windows paths in the Worktree column with `…`,
    # so we can't substring-match by worktree name. Instead check the
    # status column (which fits) for "merged" + "abandoned", and rely
    # on the line count below.
    assert "merged" in result.output
    assert "abandoned" in result.output
    # The "young" row has session_status=merged but is below the 30d
    # cutoff so wouldn't be listed. The PRESENCE of merged in the
    # output above is from old-merged, not young — verify by counting
    # data rows in the table (two pipes per row, two rows).
    table_rows = [
        ln for ln in result.output.splitlines()
        if ln.startswith("│ ") and "Mtime" not in ln
    ]
    assert len(table_rows) == 2
    # Dry-run by default.
    assert "Dry run" in result.output


def test_gc_skips_active_by_default(fake_paths: WeaveOpenPaths) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "old-active",
        age_days=45, session_status="in_progress",
    )
    result = runner.invoke(app, ["gc"])
    assert result.exit_code == 0
    assert "No worktrees older" in result.output


def test_gc_include_active_lists_active_too(
    fake_paths: WeaveOpenPaths,
) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "old-active",
        age_days=45, session_status="in_progress",
    )
    result = runner.invoke(app, ["gc", "--include-active"])
    assert result.exit_code == 0
    # Status column shows "in_progress" when --include-active picks it up.
    assert "in_progress" in result.output


def test_gc_treats_no_env_file_as_abandoned(
    fake_paths: WeaveOpenPaths,
) -> None:
    """Partial-bootstrap leftover (no env file) → list as abandoned."""
    fake_paths.worktrees_root.mkdir(parents=True)
    wt = fake_paths.worktrees_root / "leftover"
    wt.mkdir()
    target_t = time.time() - 60 * 86400
    os.utime(wt, (target_t, target_t))
    result = runner.invoke(app, ["gc"])
    assert result.exit_code == 0
    # No env file → "(no env file)" detail in the status column.
    assert "no env file" in result.output


# ---------------------------------------------------------------------------
# gc --apply removes worktrees
# ---------------------------------------------------------------------------


def test_gc_apply_removes_dirs(fake_paths: WeaveOpenPaths) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "old-merged",
        age_days=45, session_status="merged",
    )
    # Suppress git invocation — even though `git worktree remove` would
    # only succeed against a real bare clone, the fallback shutil.rmtree
    # is what actually deletes for us in tests.
    with patch("loomcli.commands.gc_cmd.shutil.which", return_value=None):
        result = runner.invoke(app, ["gc", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Removed 1 worktree" in result.output
    assert not (fake_paths.worktrees_root / "old-merged").exists()


def test_gc_apply_includes_active_requires_confirmation(
    fake_paths: WeaveOpenPaths,
) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "old-active",
        age_days=45, session_status="in_progress",
    )
    with patch("loomcli.commands.gc_cmd.shutil.which", return_value=None):
        # Don't type the magic word → abort.
        result = runner.invoke(
            app,
            ["gc", "--apply", "--include-active"],
            input="no\n",
        )
    assert result.exit_code == 1
    assert (fake_paths.worktrees_root / "old-active").exists()


def test_gc_apply_includes_active_with_confirmation(
    fake_paths: WeaveOpenPaths,
) -> None:
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "old-active",
        age_days=45, session_status="in_progress",
    )
    with patch("loomcli.commands.gc_cmd.shutil.which", return_value=None):
        result = runner.invoke(
            app,
            ["gc", "--apply", "--include-active"],
            input="remove-active\n",
        )
    assert result.exit_code == 0, result.output
    assert not (fake_paths.worktrees_root / "old-active").exists()


def test_gc_older_than_days_override(fake_paths: WeaveOpenPaths) -> None:
    """Lower threshold catches younger worktrees."""
    fake_paths.worktrees_root.mkdir(parents=True)
    _seed_worktree(
        fake_paths.worktrees_root, "five-day-old-merged",
        age_days=5, session_status="merged",
    )
    # Default 30d cutoff — should NOT list (no merged-status row in output).
    result_default = runner.invoke(app, ["gc"])
    assert "No worktrees older" in result_default.output
    # Lowered 1d cutoff — SHOULD list.
    result_low = runner.invoke(app, ["gc", "--older-than-days", "1"])
    assert "merged" in result_low.output
    table_rows = [
        ln for ln in result_low.output.splitlines()
        if ln.startswith("│ ") and "Mtime" not in ln
    ]
    assert len(table_rows) == 1
