"""Unit + interaction tests for ``loomcli._open.git_ops``.

The pure helpers (build_clone_url, short_id_from_launch_id,
path_length_warning) are unit-tested directly.

ensure_bare_clone / create_worktree are tested by patching ``_run_git``
so we never spawn a real git subprocess. We assert on the arg vector
shape (proves the right subcommand + flags are emitted) rather than
on side-effects (which would require a real git binary).

Sprint cli-weave-open-20260430, thread 864c55a4.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from loomcli._open.git_ops import (
    CloneAuthError,
    GitOpError,
    WINDOWS_PATH_WARN_THRESHOLD,
    WeaveOpenPaths,
    WorktreePathInvalidError,
    build_clone_url,
    create_worktree,
    ensure_bare_clone,
    path_length_warning,
    short_id_from_launch_id,
)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_short_id_from_launch_id_strips_dashes_and_takes_first_4() -> None:
    assert short_id_from_launch_id("a8f3b2c1-d4e5-6789-abcd-ef0123456789") == "a8f3"
    assert short_id_from_launch_id("a8f3b2c1d4e56789abcdef0123456789") == "a8f3"


def test_short_id_from_launch_id_too_short_raises() -> None:
    with pytest.raises(ValueError):
        short_id_from_launch_id("xy")


def test_build_clone_url_no_token_passes_through() -> None:
    assert (
        build_clone_url("https://github.com/org/repo.git", None)
        == "https://github.com/org/repo.git"
    )


def test_build_clone_url_with_token_injects_userinfo() -> None:
    assert (
        build_clone_url("https://github.com/org/repo.git", "ghs_xxx")
        == "https://x-access-token:ghs_xxx@github.com/org/repo.git"
    )


def test_build_clone_url_ssh_passes_through_even_with_token() -> None:
    """SSH/git URLs can't carry a token; the engine shouldn't send one
    with an SSH repo_url, but if it does we don't corrupt the URL."""
    assert (
        build_clone_url("git@github.com:org/repo.git", "ghs_xxx")
        == "git@github.com:org/repo.git"
    )


def test_path_length_warning_under_threshold_returns_none() -> None:
    assert path_length_warning(Path("/tmp/short")) is None


def test_path_length_warning_at_threshold_returns_warning() -> None:
    long = Path("/" + "x" * WINDOWS_PATH_WARN_THRESHOLD)
    assert path_length_warning(long) is not None


# ---------------------------------------------------------------------------
# ensure_bare_clone
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_paths(tmp_path: Path) -> WeaveOpenPaths:
    """Per-test ~/.powerloom replacement under tmp_path."""
    return WeaveOpenPaths(
        repos_root=tmp_path / "repos",
        worktrees_root=tmp_path / "worktrees",
    )


def _ok_completed(cmd: list[str], stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")


def test_ensure_bare_clone_first_time_clones(
    isolated_paths: WeaveOpenPaths,
) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _ok_completed(cmd)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        # The clone target doesn't exist on disk — but our fake_run
        # doesn't create it either, which would fool "is it cloned"
        # checks on subsequent calls. For first-time we just need the
        # clone command to be emitted.
        result = ensure_bare_clone(
            isolated_paths,
            project_slug="powerloom",
            repo_url="https://github.com/x/powerloom.git",
            clone_auth_token=None,
        )
    assert result == isolated_paths.repos_root / "powerloom.git"
    assert any(c[:3] == ["git", "clone", "--bare"] for c in captured)


def test_ensure_bare_clone_with_token_uses_authed_url(
    isolated_paths: WeaveOpenPaths,
) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _ok_completed(cmd)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        ensure_bare_clone(
            isolated_paths,
            project_slug="powerloom",
            repo_url="https://github.com/x/powerloom.git",
            clone_auth_token="ghs_test",
        )
    clone_cmd = next(c for c in captured if c[:3] == ["git", "clone", "--bare"])
    assert "x-access-token:ghs_test@github.com" in clone_cmd[3]


def test_ensure_bare_clone_existing_fetches_no_reclone(
    isolated_paths: WeaveOpenPaths,
) -> None:
    """If the bare-clone dir already exists, fetch instead of clone."""
    target = isolated_paths.repos_root / "powerloom.git"
    target.mkdir(parents=True)

    captured: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _ok_completed(cmd)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        ensure_bare_clone(
            isolated_paths,
            project_slug="powerloom",
            repo_url="https://github.com/x/powerloom.git",
            clone_auth_token=None,
        )
    # No clone-bare call.
    assert not any(c[:3] == ["git", "clone", "--bare"] for c in captured)
    # But a remote-set-url and a fetch.
    assert any(c[:3] == ["git", "remote", "set-url"] for c in captured)
    assert any(c[:2] == ["git", "fetch"] for c in captured)


def test_ensure_bare_clone_401_raises_clone_auth_error(
    isolated_paths: WeaveOpenPaths,
) -> None:
    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=cmd,
            stderr=(
                "remote: Invalid username or password.\n"
                "fatal: Authentication failed for "
                "'https://github.com/x/powerloom.git/'"
            ),
        )

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        with pytest.raises(CloneAuthError):
            ensure_bare_clone(
                isolated_paths,
                project_slug="powerloom",
                repo_url="https://github.com/x/powerloom.git",
                clone_auth_token="ghs_bad",
            )


def test_ensure_bare_clone_generic_failure_raises_git_op_error(
    isolated_paths: WeaveOpenPaths,
) -> None:
    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=cmd,
            stderr="fatal: repository 'https://nope/' not found",
        )

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        with pytest.raises(GitOpError) as exc_info:
            ensure_bare_clone(
                isolated_paths,
                project_slug="powerloom",
                repo_url="https://nope",
                clone_auth_token=None,
            )
    # Specifically NOT a CloneAuthError — caller should branch on that.
    assert not isinstance(exc_info.value, CloneAuthError)


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------


def test_create_worktree_first_time_runs_git_worktree_add(
    isolated_paths: WeaveOpenPaths,
) -> None:
    bare = isolated_paths.repos_root / "powerloom.git"
    bare.mkdir(parents=True)

    captured: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _ok_completed(cmd)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        result = create_worktree(
            isolated_paths,
            bare_clone=bare,
            scope_slug="cc-x-20260501",
            branch_base="main",
            branch_name="session/cc-x-20260501",
            short_id="a8f3",
        )
    assert result.name == "cc-x-20260501-a8f3"
    add_cmd = next(c for c in captured if c[:3] == ["git", "worktree", "add"])
    assert str(result) in add_cmd
    assert "-b" in add_cmd
    assert "session/cc-x-20260501" in add_cmd
    assert "origin/main" in add_cmd


def test_create_worktree_idempotent_on_registered_path(
    isolated_paths: WeaveOpenPaths,
) -> None:
    bare = isolated_paths.repos_root / "powerloom.git"
    bare.mkdir(parents=True)
    target = isolated_paths.worktrees_root / "cc-x-20260501-a8f3"
    target.mkdir(parents=True)

    def fake_run(cmd, **kw):
        # `git worktree list --porcelain` returns the target as registered.
        if cmd[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _ok_completed(cmd, stdout=f"worktree {target}\n")
        return _ok_completed(cmd)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        result = create_worktree(
            isolated_paths,
            bare_clone=bare,
            scope_slug="cc-x-20260501",
            branch_base="main",
            branch_name="session/cc-x-20260501",
            short_id="a8f3",
        )
    assert result == target


def test_create_worktree_path_exists_unregistered_raises(
    isolated_paths: WeaveOpenPaths,
) -> None:
    bare = isolated_paths.repos_root / "powerloom.git"
    bare.mkdir(parents=True)
    target = isolated_paths.worktrees_root / "cc-x-20260501-a8f3"
    target.mkdir(parents=True)

    def fake_run(cmd, **kw):
        # Empty worktree list — the target is NOT registered.
        if cmd[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _ok_completed(cmd, stdout="")
        return _ok_completed(cmd)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=fake_run):
        with pytest.raises(WorktreePathInvalidError):
            create_worktree(
                isolated_paths,
                bare_clone=bare,
                scope_slug="cc-x-20260501",
                branch_base="main",
                branch_name="session/cc-x-20260501",
                short_id="a8f3",
            )
