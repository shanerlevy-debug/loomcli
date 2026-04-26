"""M2 — weave setup-claude-code tests.

Verifies idempotent write behaviour for .mcp.json and
.claude/settings.local.json without touching the real filesystem or
the live control plane.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app

runner = CliRunner()

FAKE_ME = {
    "id": "user-123",
    "email": "dev@example.com",
    "organization_id": "org-abc",
    "mcp_proxy_id": "cf2b72ba-af17-4e71-b10a-86a8cae45e49",
}
EXPECTED_URL = "https://cf2b72ba-af17-4e71-b10a-86a8cae45e49.mcp.powerloom.org/mcp"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "myproject"


def _invoke(project_dir: Path, extra_args: list[str] | None = None) -> object:
    args = ["setup-claude-code", "--project-dir", str(project_dir)] + (extra_args or [])
    with patch("loomcli.auth.PowerloomClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = FAKE_ME
        mock_cls.return_value = mock_client
        return runner.invoke(app, args)


# ---------------------------------------------------------------------------
# Fresh project (no pre-existing files)
# ---------------------------------------------------------------------------


def test_creates_mcp_json(project_dir):
    project_dir.mkdir()
    result = _invoke(project_dir)
    assert result.exit_code == 0, result.output
    mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert mcp["powerloom"]["type"] == "http"
    assert mcp["powerloom"]["url"] == EXPECTED_URL
    assert "${POWERLOOM_MCP_TOKEN}" in mcp["powerloom"]["headers"]["Authorization"]


def test_creates_settings_local_json(project_dir):
    project_dir.mkdir()
    result = _invoke(project_dir)
    assert result.exit_code == 0, result.output
    settings = json.loads((project_dir / ".claude" / "settings.local.json").read_text())
    assert settings["env"]["POWERLOOM_MCP_TOKEN"] == "test-token"
    assert "powerloom" in settings["enabledMcpjsonServers"]


def test_creates_claude_dir_if_absent(project_dir):
    project_dir.mkdir()
    assert not (project_dir / ".claude").exists()
    _invoke(project_dir)
    assert (project_dir / ".claude").is_dir()


# ---------------------------------------------------------------------------
# Idempotency — re-running updates only powerloom keys, preserves others
# ---------------------------------------------------------------------------


def test_mcp_json_idempotent_preserves_other_servers(project_dir):
    project_dir.mkdir()
    existing = {"github": {"type": "http", "url": "https://github.mcp/api"}}
    (project_dir / ".mcp.json").write_text(json.dumps(existing))

    _invoke(project_dir)

    mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert "github" in mcp, "pre-existing server should be preserved"
    assert mcp["powerloom"]["url"] == EXPECTED_URL


def test_settings_local_idempotent_preserves_existing_keys(project_dir):
    project_dir.mkdir()
    (project_dir / ".claude").mkdir()
    existing = {
        "permissions": {"allow": ["Bash(git *)"]},
        "enabledMcpjsonServers": ["some-other-server"],
    }
    (project_dir / ".claude" / "settings.local.json").write_text(json.dumps(existing))

    _invoke(project_dir)

    settings = json.loads((project_dir / ".claude" / "settings.local.json").read_text())
    assert settings["permissions"]["allow"] == ["Bash(git *)"], "permissions should be preserved"
    assert "some-other-server" in settings["enabledMcpjsonServers"], "other servers should be preserved"
    assert "powerloom" in settings["enabledMcpjsonServers"]


def test_settings_local_does_not_duplicate_server(project_dir):
    """Running setup twice must not append powerloom twice."""
    project_dir.mkdir()
    _invoke(project_dir)
    _invoke(project_dir)
    settings = json.loads((project_dir / ".claude" / "settings.local.json").read_text())
    assert settings["enabledMcpjsonServers"].count("powerloom") == 1


# ---------------------------------------------------------------------------
# --quiet flag
# ---------------------------------------------------------------------------


def test_quiet_flag_produces_no_output(project_dir):
    project_dir.mkdir()
    result = _invoke(project_dir, ["--quiet"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_quiet_still_writes_files(project_dir):
    project_dir.mkdir()
    _invoke(project_dir, ["--quiet"])
    assert (project_dir / ".mcp.json").exists()
    assert (project_dir / ".claude" / "settings.local.json").exists()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_not_signed_in_exits_nonzero(project_dir, monkeypatch):
    project_dir.mkdir()
    # Remove the fake credentials the conftest wrote
    monkeypatch.setenv("POWERLOOM_HOME", str(project_dir / "empty-home"))
    result = runner.invoke(
        app, ["setup-claude-code", "--project-dir", str(project_dir)]
    )
    assert result.exit_code != 0
    assert "login" in result.output.lower() or "signed in" in result.output.lower()


def test_missing_proxy_id_exits_nonzero(project_dir):
    project_dir.mkdir()
    me_without_proxy = {k: v for k, v in FAKE_ME.items() if k != "mcp_proxy_id"}
    with patch("loomcli.auth.PowerloomClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = me_without_proxy
        mock_cls.return_value = mock_client
        result = runner.invoke(
            app, ["setup-claude-code", "--project-dir", str(project_dir)]
        )
    assert result.exit_code != 0
    assert "proxy" in result.output.lower() or "support" in result.output.lower()
