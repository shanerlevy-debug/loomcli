"""Tests for ``loomcli._open.runtime_exec``.

Sprint cli-weave-open-20260430, thread 53573d73.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from loomcli._open.runtime_exec import (
    ANTIGRAVITY_RUNTIME,
    RUNTIME_BINARIES,
    RuntimeBinaryError,
    _load_session_env,
    assert_runtime_available,
    binary_for_runtime,
    exec_runtime,
)
from loomcli._open.session_reg import SESSION_ENV_FILENAME


# ---------------------------------------------------------------------------
# RUNTIME_BINARIES mapping
# ---------------------------------------------------------------------------


def test_runtime_binaries_covers_all_exec_runtimes() -> None:
    """LaunchRuntime literal lists 4 runtimes; 3 exec'd directly, antigravity special-cased."""
    assert set(RUNTIME_BINARIES.keys()) == {"claude_code", "codex_cli", "gemini_cli"}
    assert RUNTIME_BINARIES["claude_code"] == "claude"
    assert RUNTIME_BINARIES["codex_cli"] == "codex"
    assert RUNTIME_BINARIES["gemini_cli"] == "gemini"


def test_binary_for_runtime_returns_none_for_antigravity() -> None:
    assert binary_for_runtime(ANTIGRAVITY_RUNTIME) is None


def test_binary_for_runtime_unknown_raises() -> None:
    with pytest.raises(RuntimeBinaryError):
        binary_for_runtime("nope_cli")


# ---------------------------------------------------------------------------
# assert_runtime_available
# ---------------------------------------------------------------------------


def test_assert_runtime_available_passes_when_binary_present() -> None:
    with patch(
        "loomcli._open.runtime_exec.shutil.which", return_value="/usr/bin/claude"
    ):
        assert_runtime_available("claude_code")  # no exception


def test_assert_runtime_available_raises_when_binary_missing() -> None:
    with patch("loomcli._open.runtime_exec.shutil.which", return_value=None):
        with pytest.raises(RuntimeBinaryError) as excinfo:
            assert_runtime_available("claude_code")
    assert excinfo.value.binary == "claude"
    assert "claude" in str(excinfo.value)


def test_assert_runtime_available_noop_for_antigravity() -> None:
    # No shutil.which call needed — antigravity skips exec entirely.
    with patch("loomcli._open.runtime_exec.shutil.which") as mock_which:
        assert_runtime_available(ANTIGRAVITY_RUNTIME)
        mock_which.assert_not_called()


# ---------------------------------------------------------------------------
# _load_session_env
# ---------------------------------------------------------------------------


def test_load_session_env_parses_kv_lines(tmp_path: Path) -> None:
    (tmp_path / SESSION_ENV_FILENAME).write_text(
        "POWERLOOM_SESSION_ID=abc123\n"
        "POWERLOOM_SCOPE=cc-test-20260501\n"
        "# comment line\n"
        "\n"
        "POWERLOOM_RUNTIME=claude_code\n",
        encoding="utf-8",
    )
    env = _load_session_env(tmp_path)
    assert env == {
        "POWERLOOM_SESSION_ID": "abc123",
        "POWERLOOM_SCOPE": "cc-test-20260501",
        "POWERLOOM_RUNTIME": "claude_code",
    }


def test_load_session_env_returns_empty_when_missing(tmp_path: Path) -> None:
    assert _load_session_env(tmp_path) == {}


# ---------------------------------------------------------------------------
# exec_runtime
# ---------------------------------------------------------------------------


def test_exec_runtime_calls_execvpe_with_binary(tmp_path: Path) -> None:
    """Asserts cd-then-execvpe pattern with merged env (POSIX)."""
    (tmp_path / SESSION_ENV_FILENAME).write_text(
        "POWERLOOM_SESSION_ID=fromenvfile\n", encoding="utf-8"
    )

    captured = {}

    def fake_chdir(path):
        captured["chdir"] = str(path)

    def fake_execvpe(file, args, env):
        captured["binary"] = file
        captured["args"] = list(args)
        captured["env"] = dict(env)
        # In real life this replaces the process; in tests we just
        # raise to abort the call so we don't loop / continue.
        raise SystemExit(0)

    # Force the POSIX branch — Windows takes a separate subprocess path
    # (covered by test_exec_runtime_windows_uses_subprocess_for_tty_passthrough).
    with patch("loomcli._open.runtime_exec._is_windows", return_value=False):
        with patch("loomcli._open.runtime_exec.shutil.which", return_value="/usr/bin/claude"):
            with patch("loomcli._open.runtime_exec.os.chdir", side_effect=fake_chdir):
                with patch(
                    "loomcli._open.runtime_exec.os.execvpe", side_effect=fake_execvpe
                ):
                    with pytest.raises(SystemExit):
                        exec_runtime(tmp_path, "claude_code")

    assert captured["chdir"] == str(tmp_path)
    assert captured["binary"] == "claude"
    assert captured["args"] == ["claude"]
    # Env-file contents merged into the env vector.
    assert captured["env"]["POWERLOOM_SESSION_ID"] == "fromenvfile"


def test_exec_runtime_antigravity_returns_without_exec(tmp_path: Path) -> None:
    """Antigravity branch doesn't replace the process — it just returns."""
    with patch("loomcli._open.runtime_exec.os.execvpe") as mock_exec:
        result = exec_runtime(tmp_path, ANTIGRAVITY_RUNTIME)
        assert result is None
        mock_exec.assert_not_called()


def test_exec_runtime_windows_uses_subprocess_for_tty_passthrough(
    tmp_path: Path,
) -> None:
    """Windows: subprocess.run with inherited stdio so interactive
    prompts (Claude Code's "trust this folder?" arrow-key picker)
    can read keyboard input. `os.execvpe` on Windows leaves the TTY
    in a half-detached state that swallows input."""
    captured = {}

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["cwd"] = kw.get("cwd")
        captured["env"] = dict(kw.get("env") or {})
        return FakeCompleted()

    with patch("loomcli._open.runtime_exec._is_windows", return_value=True):
        with patch(
            "loomcli._open.runtime_exec.shutil.which",
            return_value=r"C:\Users\u\bin\claude.exe",
        ):
            with patch(
                "loomcli._open.runtime_exec.subprocess.run", side_effect=fake_run
            ):
                with patch("loomcli._open.runtime_exec.sys.exit") as mock_exit:
                    exec_runtime(tmp_path, "claude_code")
                    mock_exit.assert_called_once_with(0)

    assert captured["cmd"] == [r"C:\Users\u\bin\claude.exe"]
    assert captured["cwd"] == str(tmp_path)


def test_exec_runtime_binary_disappeared_raises(tmp_path: Path) -> None:
    """Race between pre-flight and exec — binary uninstalled in between."""
    with patch("loomcli._open.runtime_exec.shutil.which", return_value=None):
        with pytest.raises(RuntimeBinaryError):
            exec_runtime(tmp_path, "claude_code")
