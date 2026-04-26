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


# ---------------------------------------------------------------------------
# M3 — register --from-branch + --if-not-active
# ---------------------------------------------------------------------------

FAKE_REGISTER_RESP = {
    "session": {
        "id": "aaa-bbb",
        "session_slug": "phase23-service-accounts-20260425",
        "status": "active",
        "version_claimed": None,
        "capabilities": ["api", "python"],
    },
    "work_chain_event_hash": "deadbeef",
    "overlap_warnings": [],
}


def _mock_client_for_register(mock_cls, register_resp=None, ls_resp=None):
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = register_resp or FAKE_REGISTER_RESP
    mock_client.get.return_value = ls_resp or {"sessions": []}
    mock_cls.return_value = mock_client
    return mock_client


def test_register_from_branch_infers_scope(monkeypatch):
    """--from-branch reads git branch and derives scope + branch."""
    monkeypatch.setattr(
        "loomcli.commands.agent_session_cmd._current_git_branch",
        lambda: "session/phase23-service-accounts-20260425",
    )
    with patch("loomcli.commands.agent_session_cmd.PowerloomClient") as mock_cls:
        mock_client = _mock_client_for_register(mock_cls)
        result = runner.invoke(app, ["agent-session", "register", "--from-branch"])

    assert result.exit_code == 0, result.output
    called_body = mock_client.post.call_args[0][1]
    assert called_body["session_slug"] == "phase23-service-accounts-20260425"
    assert called_body["branch_name"] == "session/phase23-service-accounts-20260425"


def test_register_from_branch_scope_can_be_overridden(monkeypatch):
    """--scope overrides the inferred scope when --from-branch is also set."""
    monkeypatch.setattr(
        "loomcli.commands.agent_session_cmd._current_git_branch",
        lambda: "session/phase23-service-accounts-20260425",
    )
    resp = {**FAKE_REGISTER_RESP, "session": {**FAKE_REGISTER_RESP["session"], "session_slug": "custom-scope"}}
    with patch("loomcli.commands.agent_session_cmd.PowerloomClient") as mock_cls:
        mock_client = _mock_client_for_register(mock_cls, register_resp=resp)
        result = runner.invoke(
            app,
            ["agent-session", "register", "--from-branch", "--scope", "custom-scope"],
        )

    assert result.exit_code == 0, result.output
    called_body = mock_client.post.call_args[0][1]
    assert called_body["session_slug"] == "custom-scope"


def test_register_from_branch_non_session_branch_errors(monkeypatch):
    """--from-branch on a non-session branch exits non-zero."""
    monkeypatch.setattr(
        "loomcli.commands.agent_session_cmd._current_git_branch",
        lambda: "main",
    )
    result = runner.invoke(app, ["agent-session", "register", "--from-branch"])
    assert result.exit_code != 0
    assert "convention" in result.output.lower() or "session/" in result.output


def test_register_from_branch_no_git_errors(monkeypatch):
    """--from-branch with no git repo exits non-zero."""
    monkeypatch.setattr(
        "loomcli.commands.agent_session_cmd._current_git_branch",
        lambda: None,
    )
    result = runner.invoke(app, ["agent-session", "register", "--from-branch"])
    assert result.exit_code != 0
    assert "branch" in result.output.lower()


def test_register_if_not_active_skips_when_already_registered():
    """--if-not-active is a no-op when the scope is already in the active list."""
    active_sessions = {
        "sessions": [
            {"session_slug": "phase23-service-accounts-20260425", "status": "active"}
        ]
    }
    with patch("loomcli.commands.agent_session_cmd.PowerloomClient") as mock_cls:
        mock_client = _mock_client_for_register(mock_cls, ls_resp=active_sessions)
        result = runner.invoke(
            app,
            [
                "agent-session", "register",
                "--scope", "phase23-service-accounts-20260425",
                "--summary", "test session",
                "--if-not-active",
            ],
        )

    assert result.exit_code == 0, result.output
    # POST should NOT have been called
    mock_client.post.assert_not_called()
    assert "already active" in result.output.lower() or "skipping" in result.output.lower()


def test_register_if_not_active_proceeds_when_not_registered():
    """--if-not-active registers normally when the scope is absent."""
    with patch("loomcli.commands.agent_session_cmd.PowerloomClient") as mock_cls:
        mock_client = _mock_client_for_register(mock_cls, ls_resp={"sessions": []})
        result = runner.invoke(
            app,
            [
                "agent-session", "register",
                "--scope", "new-session-20260426",
                "--summary", "brand new session",
                "--if-not-active",
            ],
        )

    assert result.exit_code == 0, result.output
    mock_client.post.assert_called_once()


def test_register_missing_scope_without_from_branch_errors():
    """Omitting --scope without --from-branch exits non-zero."""
    result = runner.invoke(
        app, ["agent-session", "register", "--summary", "oops"]
    )
    assert result.exit_code != 0


def test_register_missing_summary_without_from_branch_errors():
    """Omitting --summary without --from-branch exits non-zero."""
    result = runner.invoke(
        app, ["agent-session", "register", "--scope", "my-scope-20260426"]
    )
    assert result.exit_code != 0
