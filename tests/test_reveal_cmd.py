"""Tests for ``weave reveal``.

Sprint polish-doctor-resume-20260430, thread f6d8f7b0.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli._open.session_reg import SESSION_ENV_FILENAME


runner = CliRunner()


def _make_worktree_with_env(
    root: Path, name: str, *, session_id: str, runtime: str = "claude_code"
) -> Path:
    wt = root / name
    wt.mkdir(parents=True, exist_ok=True)
    (wt / SESSION_ENV_FILENAME).write_text(
        f"POWERLOOM_SESSION_ID={session_id}\n"
        f"POWERLOOM_RUNTIME={runtime}\n",
        encoding="utf-8",
    )
    return wt


# ---------------------------------------------------------------------------
# cwd-ancestor resolution
# ---------------------------------------------------------------------------


def test_reveal_uses_cwd_ancestor(tmp_path: Path) -> None:
    """No session_id → walks up from cwd looking for .powerloom-session.env."""
    wt = _make_worktree_with_env(tmp_path, "wt-aaaa", session_id="s1")
    nested = wt / "src" / "deep"
    nested.mkdir(parents=True)

    with patch("loomcli.commands.reveal_cmd.subprocess.Popen") as mock_popen:
        with patch(
            "loomcli.commands.reveal_cmd.Path.cwd", return_value=nested
        ):
            result = runner.invoke(app, ["reveal", "--print-only"])
    assert result.exit_code == 0, result.output
    assert str(wt).replace(" ", "") in "".join(result.output.split())
    mock_popen.assert_not_called()  # --print-only skips launch


def test_reveal_no_session_anywhere_fails(tmp_path: Path) -> None:
    """cwd not under any worktree → 1 with hint."""
    nowhere = tmp_path / "outside"
    nowhere.mkdir()
    with patch(
        "loomcli.commands.reveal_cmd.Path.cwd", return_value=nowhere
    ):
        result = runner.invoke(app, ["reveal"])
    assert result.exit_code == 1
    # Error / hint go to stderr; CliRunner merges by default.
    assert "couldn't find" in result.output.lower()


# ---------------------------------------------------------------------------
# explicit session-id resolution
# ---------------------------------------------------------------------------


def test_reveal_with_session_id_finds_worktree(tmp_path: Path) -> None:
    """--session_id branch — tested via direct call to _resolve_target since
    CliRunner's stdio handling masks the patch context for some Rich code
    paths on Windows. The orchestration glue is exercised in the cwd-walk
    tests above."""
    from loomcli._open.git_ops import WeaveOpenPaths
    from loomcli.commands.reveal_cmd import _resolve_target

    fake_paths = WeaveOpenPaths(
        repos_root=tmp_path / "repos",
        worktrees_root=tmp_path / "worktrees",
    )
    target = _make_worktree_with_env(
        fake_paths.worktrees_root, "wt-bbbb", session_id="my-sess-id"
    )

    with patch("loomcli.commands.reveal_cmd.WeaveOpenPaths") as mock_paths_cls:
        mock_paths_cls.default.return_value = fake_paths
        resolved = _resolve_target("my-sess-id")
    assert resolved == target


def test_reveal_with_unknown_session_id_fails(tmp_path: Path) -> None:
    from loomcli._open.git_ops import WeaveOpenPaths

    fake_paths = WeaveOpenPaths(
        repos_root=tmp_path / "repos",
        worktrees_root=tmp_path / "worktrees",
    )
    fake_paths.worktrees_root.mkdir(parents=True)

    with patch("loomcli.commands.reveal_cmd.WeaveOpenPaths") as mock_paths_cls:
        mock_paths_cls.default.return_value = fake_paths
        result = runner.invoke(app, ["reveal", "unknown-sess-id"])
    assert result.exit_code == 1
    assert "couldn't find" in result.output.lower()


# ---------------------------------------------------------------------------
# OS-handler dispatch
# ---------------------------------------------------------------------------


def test_reveal_invokes_explorer_on_windows(tmp_path: Path) -> None:
    wt = _make_worktree_with_env(tmp_path, "wt-cccc", session_id="s")
    with patch(
        "loomcli.commands.reveal_cmd.sys.platform", "win32"
    ), patch(
        "loomcli.commands.reveal_cmd.subprocess.Popen"
    ) as mock_popen, patch(
        "loomcli.commands.reveal_cmd.Path.cwd", return_value=wt
    ):
        result = runner.invoke(app, ["reveal"])
    assert result.exit_code == 0, result.output
    args = mock_popen.call_args.args[0]
    assert args[0] == "explorer"
    assert args[1] == str(wt)


def test_reveal_invokes_open_on_macos(tmp_path: Path) -> None:
    wt = _make_worktree_with_env(tmp_path, "wt-dddd", session_id="s")
    with patch(
        "loomcli.commands.reveal_cmd.sys.platform", "darwin"
    ), patch(
        "loomcli.commands.reveal_cmd.subprocess.Popen"
    ) as mock_popen, patch(
        "loomcli.commands.reveal_cmd.Path.cwd", return_value=wt
    ):
        result = runner.invoke(app, ["reveal"])
    assert result.exit_code == 0, result.output
    args = mock_popen.call_args.args[0]
    assert args[0] == "open"


def test_reveal_falls_back_to_xdg_open_on_linux(tmp_path: Path) -> None:
    wt = _make_worktree_with_env(tmp_path, "wt-eeee", session_id="s")
    with patch(
        "loomcli.commands.reveal_cmd.sys.platform", "linux"
    ), patch(
        "loomcli.commands.reveal_cmd.shutil.which",
        side_effect=lambda b: "/usr/bin/xdg-open" if b == "xdg-open" else None,
    ), patch(
        "loomcli.commands.reveal_cmd.subprocess.Popen"
    ) as mock_popen, patch(
        "loomcli.commands.reveal_cmd.Path.cwd", return_value=wt
    ):
        result = runner.invoke(app, ["reveal"])
    assert result.exit_code == 0, result.output
    args = mock_popen.call_args.args[0]
    assert args[0] == "xdg-open"


def test_reveal_no_handler_only_prints(tmp_path: Path) -> None:
    """No xdg-open / open / explorer → path-print only, no exception."""
    wt = _make_worktree_with_env(tmp_path, "wt-ffff", session_id="s")
    with patch(
        "loomcli.commands.reveal_cmd.sys.platform", "linux"
    ), patch(
        "loomcli.commands.reveal_cmd.shutil.which", return_value=None
    ), patch(
        "loomcli.commands.reveal_cmd.subprocess.Popen"
    ) as mock_popen, patch(
        "loomcli.commands.reveal_cmd.Path.cwd", return_value=wt
    ):
        result = runner.invoke(app, ["reveal"])
    assert result.exit_code == 0, result.output
    mock_popen.assert_not_called()
    assert str(wt).replace(" ", "") in "".join(result.output.split())
    assert "no OS file-manager handler" in result.output
