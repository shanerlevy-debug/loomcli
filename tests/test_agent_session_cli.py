"""Tests for `weave agent-session *` subcommands.

Most of the logic is API-side (see api/tests/test_agent_sessions.py);
these tests only confirm the CLI surface wires up correctly and
produces reasonable output. No network — we mock PowerloomClient.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomApiError


runner = CliRunner()


def test_agent_session_help():
    result = runner.invoke(app, ["agent-session", "--help"])
    assert result.exit_code == 0
    for sub in ("register", "bootstrap", "end", "ls", "get", "status", "watch"):
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
    # Verify the agent-sessions POST. v067 onboarding sprint added a
    # second POST to /me/agents (find-or-create sub-principal); look at
    # the FIRST POST call rather than .call_args (which is the LAST).
    first_post = mock_client.post.call_args_list[0]
    args, kwargs = first_post
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
    # First POST is the agent-session register; subsequent POSTs are
    # the v067-era /me/agents sub-principal find-or-create.
    called_body = mock_client.post.call_args_list[0][0][1]
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
    called_body = mock_client.post.call_args_list[0][0][1]
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
    # First POST is /agent-sessions; second POST (v067 onboarding) is
    # /me/agents for sub-principal find-or-create. Both happen on a
    # successful register; the test only cares that the
    # agent-sessions one fired (i.e. --if-not-active didn't short-circuit).
    posted_paths = [c.args[0] for c in mock_client.post.call_args_list]
    assert "/agent-sessions" in posted_paths


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


@patch("loomcli.commands.agent_session_cmd._check_client_plugin")
@patch("loomcli.commands.agent_session_cmd._ensure_repo")
@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_bootstrap_uses_project_config_and_registers(
    mock_client_cls,
    mock_ensure_repo,
    mock_check_plugin,
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "has-creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))
    mock_ensure_repo.return_value = Path("D:/powerloom/Powerloom")

    mock_client = MagicMock()
    mock_client.get.side_effect = [
        {
            "project_slug": "powerloom",
            "project_name": "Powerloom",
            "config": {
                "repo_url": "https://github.com/example/Powerloom.git",
                "default_branch": "main",
                "recommended_workdir": str(tmp_path),
                "session_scope_template": "{client}-{project}-{date}",
                "branch_template": "session/{scope}",
                "summary_template": "{client} session for {project}",
                "capabilities": ["cli", "docs"],
            },
        },
        {"sessions": []},
    ]
    mock_client.post.return_value = {
        "session": {
            "id": "sess-1",
            "session_slug": f"codex-powerloom-{date.today():%Y%m%d}",
            "status": "active",
        },
        "work_chain_event_hash": "abcd" * 16,
        "overlap_warnings": [],
    }
    mock_client_cls.return_value = mock_client

    result = runner.invoke(
        app,
        ["agent-session", "bootstrap", "--project", "powerloom", "--client", "codex_cli"],
    )

    assert result.exit_code == 0, result.output
    assert "Bootstrap complete" in result.output
    ensure_kwargs = mock_ensure_repo.call_args.kwargs
    assert ensure_kwargs["repo_url"] == "https://github.com/example/Powerloom.git"
    assert ensure_kwargs["default_branch"] == "main"
    assert ensure_kwargs["session_branch"] == f"session/codex-powerloom-{date.today():%Y%m%d}"
    mock_check_plugin.assert_called_once_with("codex_cli")

    sent = mock_client.post.call_args[0][1]
    assert sent["session_slug"] == f"codex-powerloom-{date.today():%Y%m%d}"
    assert sent["branch_name"] == f"session/codex-powerloom-{date.today():%Y%m%d}"
    assert sent["actor_kind"] == "codex_cli"
    assert sent["capabilities"] == ["cli", "docs"]


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_bootstrap_requires_repo_url_when_project_has_no_config(
    mock_client_cls,
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "has-creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock_client = MagicMock()
    mock_client.get.side_effect = PowerloomApiError(404, "not found")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(
        app,
        ["agent-session", "bootstrap", "--project", "missing", "--client", "codex_cli"],
    )

    assert result.exit_code != 0
    assert "No repository URL configured" in result.output


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_agent_session_status_shows_coordination_tasks(mock_client_cls, monkeypatch, tmp_path):
    home = tmp_path / "has-creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock_client = MagicMock()
    session_id = "session-123"

    def get_side_effect(path: str, **params):
        if path == f"/agent-sessions/{session_id}":
            return {
                "id": session_id,
                "session_slug": "codex-onboarding",
                "status": "active",
                "actor_kind": "codex_cli",
                "scope_summary": "CLI onboarding work",
            }
        if path == f"/agent-sessions/{session_id}/tasks":
            return {
                "tasks": [
                    {
                        "id": "task-1",
                        "workflow_name": "Sprint",
                        "node_id": "finish-cli",
                        "node_kind": "agent",
                        "status": "running",
                    }
                ]
            }
        raise AssertionError(f"unexpected GET {path} {params}")

    mock_client.get.side_effect = get_side_effect
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["agent-session", "status", session_id])

    assert result.exit_code == 0, result.stdout
    assert "codex-onboarding" in result.stdout
    assert "codex_cli" in result.stdout
    assert "finish-cli" in result.stdout


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_agent_session_watch_once_prints_compact_line(mock_client_cls, monkeypatch, tmp_path):
    home = tmp_path / "has-creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock_client = MagicMock()
    session_id = "session-123"
    mock_client.get.side_effect = [
        {
            "id": session_id,
            "session_slug": "codex-onboarding",
            "status": "active",
            "actor_kind": "codex_cli",
        },
        {"tasks": [{"node_id": "finish-cli", "status": "running"}]},
    ]
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["agent-session", "watch", session_id, "--once"])

    assert result.exit_code == 0, result.stdout
    assert "codex-onboarding" in result.stdout
    assert "finish-cli:running" in result.stdout


# ---------------------------------------------------------------------------
# v067 onboarding sprint — sub-principal find-or-create + cache file
# ---------------------------------------------------------------------------


def test_ensure_subprincipal_creates_when_missing(monkeypatch, tmp_path):
    """First call for a (kind, scope) creates the sub-principal + writes the file."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    from loomcli.commands.agent_session_cmd import _ensure_subprincipal
    from loomcli.config import active_subprincipal_file

    client = MagicMock()
    client.get.return_value = []  # no existing sub-principals
    client.post.return_value = {
        "id": "sp-uuid-aaaa-bbbb-cccc-dddddddddddd",
        "name": "claude_code:my-test-scope-20260427",
        "client_kind": "claude_code",
    }

    sid = _ensure_subprincipal(client, scope="my-test-scope-20260427", actor_kind="claude_code")
    assert sid == "sp-uuid-aaaa-bbbb-cccc-dddddddddddd"

    # Created with the right name + client_kind
    args, _ = client.post.call_args
    assert args[0] == "/me/agents"
    assert args[1]["name"] == "claude_code:my-test-scope-20260427"
    assert args[1]["client_kind"] == "claude_code"

    # File written
    path = active_subprincipal_file("my-test-scope-20260427")
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == "sp-uuid-aaaa-bbbb-cccc-dddddddddddd"


def test_ensure_subprincipal_reuses_existing(monkeypatch, tmp_path):
    """If a sub-principal with the desired name exists, no POST happens."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    from loomcli.commands.agent_session_cmd import _ensure_subprincipal

    client = MagicMock()
    client.get.return_value = [
        {"id": "existing-uuid", "name": "claude_code:existing-scope-20260427"},
        {"id": "other-uuid", "name": "codex_cli:other-scope-20260427"},
    ]

    sid = _ensure_subprincipal(client, scope="existing-scope-20260427", actor_kind="claude_code")
    assert sid == "existing-uuid"
    client.post.assert_not_called()


def test_ensure_subprincipal_per_scope_isolation(monkeypatch, tmp_path):
    """Two scopes -> two separate cache files."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    from loomcli.commands.agent_session_cmd import _ensure_subprincipal
    from loomcli.config import active_subprincipal_file

    client = MagicMock()
    client.get.return_value = []
    client.post.side_effect = [
        {"id": "uuid-a", "name": "claude_code:scope-a-20260427"},
        {"id": "uuid-b", "name": "claude_code:scope-b-20260427"},
    ]

    a = _ensure_subprincipal(client, scope="scope-a-20260427", actor_kind="claude_code")
    b = _ensure_subprincipal(client, scope="scope-b-20260427", actor_kind="claude_code")
    assert a == "uuid-a"
    assert b == "uuid-b"

    pa = active_subprincipal_file("scope-a-20260427")
    pb = active_subprincipal_file("scope-b-20260427")
    assert pa.read_text().strip() == "uuid-a"
    assert pb.read_text().strip() == "uuid-b"
    # Different files
    assert pa != pb


def test_ensure_subprincipal_graceful_on_network_failure(monkeypatch, tmp_path):
    """If /me/agents GET fails, return None — register still succeeds."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    from loomcli.commands.agent_session_cmd import _ensure_subprincipal
    from loomcli.client import PowerloomApiError

    client = MagicMock()
    client.get.side_effect = PowerloomApiError(503, "service unavailable")

    sid = _ensure_subprincipal(client, scope="any-20260427", actor_kind="claude_code")
    assert sid is None
    # No file written on failure
    from loomcli.config import active_subprincipal_file
    assert not active_subprincipal_file("any-20260427").exists()
