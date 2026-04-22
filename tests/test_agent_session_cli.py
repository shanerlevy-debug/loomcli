"""Tests for `weave agent-session *` subcommands.

Most of the logic is API-side (see api/tests/test_agent_sessions.py);
these tests only confirm the CLI surface wires up correctly and
produces reasonable output. No network — we mock PowerloomClient.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()


def test_agent_session_help():
    result = runner.invoke(app, ["agent-session", "--help"])
    assert result.exit_code == 0
    for sub in ("register", "end", "ls", "get"):
        assert sub in result.stdout


@patch("loomcli.commands.agent_session_cmd.load_runtime_config")
def test_register_without_token_bails(mock_load_cfg):
    """Without a credentials file, register should exit non-zero with
    a friendly 'not signed in' message."""
    cfg = MagicMock()
    cfg.access_token = None
    mock_load_cfg.return_value = cfg
    result = runner.invoke(
        app,
        [
            "agent-session",
            "register",
            "--scope",
            "no-creds-test-20260421",
            "--summary",
            "should bail",
        ],
    )
    assert result.exit_code != 0
    assert "Not signed in" in result.stdout or "not signed in" in result.stdout.lower()


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_register_emits_expected_payload(mock_client_cls, monkeypatch, tmp_path):
    home = tmp_path / "has-creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock_client = MagicMock()
    mock_client.post.return_value = {
        "session": {
            "id": "abc-123",
            "session_slug": "t-reg-20260421",
            "status": "active",
            "version_claimed": "v030",
            "capabilities": ["docs"],
            "actor_kind": "claude_code",
        },
        "work_chain_event_hash": "abcd1234" * 8,  # 64 chars
        "overlap_warnings": [],
    }
    mock_client_cls.return_value = mock_client

    result = runner.invoke(
        app,
        [
            "agent-session",
            "register",
            "--scope",
            "t-reg-20260421",
            "--summary",
            "docs work",
            "--capabilities",
            "docs,plan",
            "--version",
            "v030",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Registered" in result.stdout
    assert "t-reg-20260421" in result.stdout
    # Verify POST was called with the expected body shape.
    args, kwargs = mock_client.post.call_args
    assert args[0] == "/agent-sessions"
    sent = args[1]
    assert sent["session_slug"] == "t-reg-20260421"
    assert sent["capabilities"] == ["docs", "plan"]
    assert sent["version_claimed"] == "v030"


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_ls_renders_table(mock_client_cls, monkeypatch, tmp_path):
    home = tmp_path / "has-creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock_client = MagicMock()
    mock_client.get.return_value = {
        "sessions": [
            {
                "session_slug": "session-a",
                "status": "active",
                "actor_kind": "claude_code",
                "version_claimed": "v030",
                "cross_cutting": True,
                "touches_migration": False,
                "started_at": "2026-04-21T10:00:00+00:00",
            }
        ],
        "total": 1,
    }
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["agent-session", "ls"])
    assert result.exit_code == 0
    assert "session-a" in result.stdout
    assert "active" in result.stdout
