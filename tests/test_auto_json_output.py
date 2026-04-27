"""Tests for the auto-JSON output detection in cli._apply_global_options.

The default 'table' renderer collapses long titles into one-character-per-row
columns when the terminal is narrow or stdout isn't a TTY. The CLI should
flip to JSON automatically in three situations:

  1. An agent-marker env var is set (CLAUDE_CODE, GEMINI_CLI, CODEX_SANDBOX,
     AGENT_MODE).
  2. POWERLOOM_ACTIVE_SUBPRINCIPAL_ID is populated by the SessionStart hook
     or weave agent-session register.
  3. stdout is not a TTY (output is being piped / captured).

In all three cases, a single stderr notice prints the reason, suppressed
by POWERLOOM_QUIET_AUTO_JSON=1.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from loomcli import cli


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the env vars these tests care about so each case starts from a
    known state regardless of the pytest invoker's shell."""
    for var in (
        *cli._AGENT_ENV_VARS,
        "POWERLOOM_FORMAT",
        "POWERLOOM_ACTIVE_SUBPRINCIPAL_ID",
        "POWERLOOM_QUIET_AUTO_JSON",
    ):
        monkeypatch.delenv(var, raising=False)


def _force_tty(value: bool) -> "patch":
    """Helper: pretend stdout is/isn't a TTY for the duration of the with."""
    return patch("loomcli.cli.sys.stdout.isatty", return_value=value)


def test_auto_json_when_claude_code_env_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CODE", "1")
    with _force_tty(True):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output=None)
    assert os.environ["POWERLOOM_FORMAT"] == "json"
    err = capsys.readouterr().err
    assert "agent env CLAUDE_CODE" in err


def test_auto_json_when_subprincipal_env_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "abc-123")
    with _force_tty(True):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output=None)
    assert os.environ["POWERLOOM_FORMAT"] == "json"
    err = capsys.readouterr().err
    assert "registered sub-principal" in err


def test_auto_json_when_stdout_not_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _force_tty(False):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output=None)
    assert os.environ["POWERLOOM_FORMAT"] == "json"
    err = capsys.readouterr().err
    assert "non-TTY stdout" in err


def test_no_auto_json_at_interactive_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No agent env, no sub-principal, stdout IS a TTY → no flip, no notice."""
    with _force_tty(True):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output=None)
    assert "POWERLOOM_FORMAT" not in os.environ
    err = capsys.readouterr().err
    assert err == ""


def test_explicit_output_wins_over_auto_detection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """User passed -o table — honor it even when auto-detect would have flipped."""
    monkeypatch.setenv("CLAUDE_CODE", "1")
    with _force_tty(False):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output="table")
    assert os.environ["POWERLOOM_FORMAT"] == "table"
    err = capsys.readouterr().err
    assert err == ""  # no auto-detect notice when user was explicit


def test_existing_powerloom_format_env_skips_auto_detection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pre-set POWERLOOM_FORMAT survives — no flip, no stderr notice."""
    monkeypatch.setenv("POWERLOOM_FORMAT", "table")
    monkeypatch.setenv("CLAUDE_CODE", "1")
    with _force_tty(False):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output=None)
    assert os.environ["POWERLOOM_FORMAT"] == "table"
    err = capsys.readouterr().err
    assert err == ""


def test_quiet_env_var_suppresses_auto_detect_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CODE", "1")
    monkeypatch.setenv("POWERLOOM_QUIET_AUTO_JSON", "1")
    with _force_tty(True):
        cli._apply_global_options(api_url=None, config_dir=None, justification=None, output=None)
    assert os.environ["POWERLOOM_FORMAT"] == "json"
    err = capsys.readouterr().err
    assert err == ""  # quiet flag in effect


def test_detect_auto_json_reason_returns_first_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When multiple triggers fire, the agent-env path wins (matches the in-code
    short-circuit order). Important for the stderr notice — we don't want to
    surface 'non-TTY stdout' when the more meaningful 'agent env' is also true."""
    monkeypatch.setenv("CLAUDE_CODE", "1")
    monkeypatch.setenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "abc-123")
    with _force_tty(False):
        reason = cli._detect_auto_json_reason()
    assert reason == "agent env CLAUDE_CODE"


def test_detect_auto_json_reason_handles_isatty_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some pytest capture / CI runners replace stdout with a non-TTY-like
    object that raises on isatty(). We swallow the error rather than crashing
    the whole command."""
    class _Brokenstdout:
        def isatty(self) -> bool:
            raise ValueError("captured stream has no real fd")

    monkeypatch.setattr(cli.sys, "stdout", _Brokenstdout())
    # No agent env, no sub-principal, broken isatty → return None gracefully.
    reason = cli._detect_auto_json_reason()
    assert reason is None
