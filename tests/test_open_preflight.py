"""Tests for ``loomcli._open.preflight``.

Sprint clone-auth-policy-20260430, threads 9233e176 + 79e876b1.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from loomcli._open import preflight


# ---------------------------------------------------------------------------
# check_git_on_path
# ---------------------------------------------------------------------------


def test_check_git_on_path_ok_when_present() -> None:
    with patch(
        "loomcli._open.preflight.shutil.which", return_value="/usr/bin/git"
    ):
        result = preflight.check_git_on_path()
    assert result.status == "ok"


def test_check_git_on_path_fail_when_missing() -> None:
    with patch("loomcli._open.preflight.shutil.which", return_value=None):
        result = preflight.check_git_on_path()
    assert result.status == "fail"
    assert "git" in result.message.lower()
    assert result.fix_hint is not None


# ---------------------------------------------------------------------------
# check_runtime_on_path
# ---------------------------------------------------------------------------


def test_check_runtime_present() -> None:
    with patch(
        "loomcli._open.preflight.shutil.which", return_value="/usr/bin/claude"
    ):
        result = preflight.check_runtime_on_path("claude_code")
    assert result.status == "ok"


def test_check_runtime_missing() -> None:
    with patch("loomcli._open.preflight.shutil.which", return_value=None):
        result = preflight.check_runtime_on_path("claude_code")
    assert result.status == "fail"
    assert "claude" in result.message
    assert "claude.com" in (result.fix_hint or "")


def test_check_runtime_antigravity_skip() -> None:
    """No binary required — daemon flow."""
    with patch("loomcli._open.preflight.shutil.which") as mock_which:
        result = preflight.check_runtime_on_path("antigravity")
        mock_which.assert_not_called()
    assert result.status == "ok"


def test_check_runtime_unknown_kind() -> None:
    result = preflight.check_runtime_on_path("nope_cli")
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# check_powerloom_writable
# ---------------------------------------------------------------------------


def test_check_powerloom_writable_creates_dirs(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    worktrees = tmp_path / "worktrees"
    result = preflight.check_powerloom_writable(repos, worktrees)
    assert result.status == "ok"
    assert repos.is_dir()
    assert worktrees.is_dir()


def test_check_powerloom_writable_existing_dirs(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    repos.mkdir()
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    result = preflight.check_powerloom_writable(repos, worktrees)
    assert result.status == "ok"


def test_check_powerloom_writable_unwritable_parent_fails(tmp_path: Path) -> None:
    """Parent dir is read-only → fail."""
    if os.name == "nt":
        pytest.skip("POSIX-only chmod semantics")
    parent = tmp_path / "ro_parent"
    parent.mkdir()
    repos = parent / "repos"
    # Make parent read-only so mkdir of `repos` fails.
    os.chmod(parent, 0o500)
    try:
        result = preflight.check_powerloom_writable(repos, parent / "worktrees")
        assert result.status == "fail"
        assert "writable" in result.message.lower()
    finally:
        os.chmod(parent, 0o700)


# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------


def test_check_disk_space_ok(tmp_path: Path) -> None:
    """Most tmp_path mounts have plenty of free space."""
    result = preflight.check_disk_space(tmp_path)
    # In CI / dev, tmp_path is usually on a drive with > 500MB free.
    assert result.status in ("ok", "warn")  # tolerant — small CI runners might warn


def test_check_disk_space_warns_when_low(tmp_path: Path) -> None:
    """Mock disk_usage to return < 500MB free."""
    from collections import namedtuple

    DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
    fake = DiskUsage(total=10**9, used=10**9 - 100 * 1024 * 1024, free=100 * 1024 * 1024)
    with patch(
        "loomcli._open.preflight.shutil.disk_usage", return_value=fake
    ):
        result = preflight.check_disk_space(tmp_path)
    assert result.status == "warn"
    assert "100 MB" in result.message


# ---------------------------------------------------------------------------
# check_local_clone_credentials  — sprint thread 79e876b1
# ---------------------------------------------------------------------------


class TestLocalCloneCredentials:
    def test_gh_auth_ok_short_circuits(self) -> None:
        with patch("loomcli._open.preflight._gh_auth_ok", return_value=True):
            with patch(
                "loomcli._open.preflight._git_credential_helper_ok"
            ) as cred:
                with patch(
                    "loomcli._open.preflight._ssh_agent_has_github_key"
                ) as ssh:
                    result = preflight.check_local_clone_credentials(
                        "https://github.com/x/y.git"
                    )
        assert result.status == "ok"
        assert "gh" in result.message.lower()
        # Subsequent probes shouldn't run when gh wins.
        cred.assert_not_called()
        ssh.assert_not_called()

    def test_git_cred_ok_when_no_gh(self) -> None:
        with patch("loomcli._open.preflight._gh_auth_ok", return_value=False):
            with patch(
                "loomcli._open.preflight._git_credential_helper_ok",
                return_value=True,
            ):
                with patch(
                    "loomcli._open.preflight._ssh_agent_has_github_key"
                ) as ssh:
                    result = preflight.check_local_clone_credentials(
                        "https://github.com/x/y.git"
                    )
        assert result.status == "ok"
        assert "credential helper" in result.message
        ssh.assert_not_called()

    def test_ssh_only(self) -> None:
        with patch("loomcli._open.preflight._gh_auth_ok", return_value=False):
            with patch(
                "loomcli._open.preflight._git_credential_helper_ok",
                return_value=False,
            ):
                with patch(
                    "loomcli._open.preflight._ssh_agent_has_github_key",
                    return_value=True,
                ):
                    result = preflight.check_local_clone_credentials(
                        "https://github.com/x/y.git"
                    )
        assert result.status == "ok"
        assert "ssh-agent" in result.message

    def test_none_of_the_above_fails_with_actionable_message(self) -> None:
        with patch("loomcli._open.preflight._gh_auth_ok", return_value=False):
            with patch(
                "loomcli._open.preflight._git_credential_helper_ok",
                return_value=False,
            ):
                with patch(
                    "loomcli._open.preflight._ssh_agent_has_github_key",
                    return_value=False,
                ):
                    result = preflight.check_local_clone_credentials(
                        "https://github.com/x/y.git"
                    )
        assert result.status == "fail"
        assert "github.com" in result.message
        assert "clone_auth_mode = local_credentials" in result.message
        assert "gh auth login" in (result.fix_hint or "")

    def test_admin_email_in_fix_hint(self) -> None:
        with patch("loomcli._open.preflight._gh_auth_ok", return_value=False):
            with patch(
                "loomcli._open.preflight._git_credential_helper_ok",
                return_value=False,
            ):
                with patch(
                    "loomcli._open.preflight._ssh_agent_has_github_key",
                    return_value=False,
                ):
                    result = preflight.check_local_clone_credentials(
                        "https://github.com/x/y.git",
                        admin_email="admin@example.com",
                    )
        assert "admin@example.com" in (result.fix_hint or "")


# ---------------------------------------------------------------------------
# Helper subprocess wrappers — _gh_auth_ok / _git_credential_helper_ok / _ssh_agent_has_github_key
# ---------------------------------------------------------------------------


class TestSubprocessHelpers:
    def test_gh_auth_ok_returns_false_when_gh_missing(self) -> None:
        with patch("loomcli._open.preflight.shutil.which", return_value=None):
            assert preflight._gh_auth_ok() is False

    def test_gh_auth_ok_returns_true_on_zero_exit(self) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/gh",
        ):
            with patch(
                "loomcli._open.preflight.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ):
                assert preflight._gh_auth_ok() is True

    def test_gh_auth_ok_returns_false_on_nonzero_exit(self) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/gh",
        ):
            with patch(
                "loomcli._open.preflight.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr=""
                ),
            ):
                assert preflight._gh_auth_ok() is False

    def test_git_credential_returns_true_with_username_password(self) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/git",
        ):
            with patch(
                "loomcli._open.preflight.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "protocol=https\nhost=github.com\nusername=shane\npassword=ghp_x\n"
                    ),
                    stderr="",
                ),
            ):
                assert preflight._git_credential_helper_ok() is True

    def test_git_credential_returns_false_when_username_missing(self) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/git",
        ):
            with patch(
                "loomcli._open.preflight.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="protocol=https\nhost=github.com\n",
                    stderr="",
                ),
            ):
                assert preflight._git_credential_helper_ok() is False

    def test_ssh_agent_returns_true_with_loaded_identity(self) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/ssh-add",
        ):
            with patch(
                "loomcli._open.preflight.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="ssh-rsa AAAAB3...",
                    stderr="",
                ),
            ):
                assert preflight._ssh_agent_has_github_key() is True

    def test_ssh_agent_returns_false_when_no_identities(self) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/ssh-add",
        ):
            with patch(
                "loomcli._open.preflight.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr=""
                ),
            ):
                assert preflight._ssh_agent_has_github_key() is False


# ---------------------------------------------------------------------------
# run_preflights — aggregation
# ---------------------------------------------------------------------------


class TestRunPreflights:
    def test_aggregates_all_checks(self, tmp_path: Path) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/exe",
        ):
            result = preflight.run_preflights(
                runtime="claude_code",
                repos_root=tmp_path / "repos",
                worktrees_root=tmp_path / "worktrees",
                repo_url="https://github.com/x/y.git",
                clone_auth_mode="server_minted",
            )
        names = [c.name for c in result.checks]
        # server_minted skips local-creds check.
        assert names == ["git", "runtime", "filesystem", "disk_space"]

    def test_local_credentials_mode_adds_local_creds_check(self, tmp_path: Path) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/exe",
        ):
            with patch(
                "loomcli._open.preflight._gh_auth_ok", return_value=True
            ):
                result = preflight.run_preflights(
                    runtime="claude_code",
                    repos_root=tmp_path / "repos",
                    worktrees_root=tmp_path / "worktrees",
                    repo_url="https://github.com/x/y.git",
                    clone_auth_mode="local_credentials",
                )
        names = [c.name for c in result.checks]
        assert "local_clone_credentials" in names

    def test_any_failed_rolls_up(self, tmp_path: Path) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value=None,  # → both git + runtime fail
        ):
            result = preflight.run_preflights(
                runtime="claude_code",
                repos_root=tmp_path / "repos",
                worktrees_root=tmp_path / "worktrees",
                repo_url="https://github.com/x/y.git",
                clone_auth_mode="server_minted",
            )
        assert result.any_failed
        assert len(result.failures) >= 2

    def test_to_dict_serialises(self, tmp_path: Path) -> None:
        with patch(
            "loomcli._open.preflight.shutil.which",
            return_value="/usr/bin/exe",
        ):
            result = preflight.run_preflights(
                runtime="claude_code",
                repos_root=tmp_path / "repos",
                worktrees_root=tmp_path / "worktrees",
                repo_url="https://github.com/x/y.git",
                clone_auth_mode="server_minted",
            )
        d = result.to_dict()
        assert "checks" in d
        assert "any_failed" in d
        assert all("status" in c for c in d["checks"])
