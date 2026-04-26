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
    assert mcp["mcpServers"]["powerloom"]["type"] == "http"
    assert mcp["mcpServers"]["powerloom"]["url"] == EXPECTED_URL
    # 2026-04-27 hotfix: default behavior writes the literal Bearer token rather
    # than `${POWERLOOM_MCP_TOKEN}` substitution. Claude Code's HTTP-transport
    # MCP config doesn't reliably env-var-substitute inside header values.
    assert mcp["mcpServers"]["powerloom"]["headers"]["Authorization"] == "Bearer test-token"


def test_use_env_substitution_writes_var_form(project_dir):
    """Opt-in: --use-env-substitution writes `Bearer ${POWERLOOM_MCP_TOKEN}`
    instead of the literal token. For shared/committed workspaces (e.g.
    powerloom repo itself) where each user must supply their own token via
    shell env."""
    project_dir.mkdir()
    result = _invoke(project_dir, ["--use-env-substitution"])
    assert result.exit_code == 0, result.output
    mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert mcp["mcpServers"]["powerloom"]["headers"]["Authorization"] == \
        "Bearer ${POWERLOOM_MCP_TOKEN}"


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
    assert "github" in mcp["mcpServers"], "pre-existing server should be preserved"
    assert mcp["mcpServers"]["powerloom"]["url"] == EXPECTED_URL


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


# ---------------------------------------------------------------------------
# Schema correctness + auto-migration of pre-hotfix broken shape
# ---------------------------------------------------------------------------


def test_mcp_json_uses_mcpServers_wrapper(project_dir):
    """Canonical Claude Code .mcp.json schema requires {mcpServers: {...}}.
    Pre-hotfix versions wrote {<name>: {...}} at top level, which CC rejects
    with `mcpServers: Does not adhere to MCP server configuration schema`."""
    project_dir.mkdir()
    _invoke(project_dir)

    mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert "mcpServers" in mcp, "must use mcpServers wrapper per CC schema"
    assert "powerloom" in mcp["mcpServers"]
    # Top-level should NOT have the server entry directly anymore
    assert "powerloom" not in {k for k in mcp.keys() if k != "mcpServers"}


def test_mcp_json_migrates_pre_hotfix_broken_shape(project_dir):
    """A .mcp.json written by pre-hotfix loomcli (server entries at top level,
    no mcpServers wrapper) should be auto-migrated to the canonical shape on
    next setup-claude-code run.

    This regression covers the 2026-04-27 hotfix where pre-hotfix users had
    .mcp.json files that CC was rejecting."""
    project_dir.mkdir()
    # Broken shape — what pre-hotfix setup_cmd.py wrote
    broken = {
        "powerloom": {
            "type": "http",
            "url": "https://stale.example.com/mcp",
            "headers": {"Authorization": "Bearer ${POWERLOOM_MCP_TOKEN}"},
        },
        "github": {
            "type": "http",
            "url": "https://github.mcp/api",
        },
    }
    (project_dir / ".mcp.json").write_text(json.dumps(broken))

    _invoke(project_dir)

    mcp = json.loads((project_dir / ".mcp.json").read_text())
    # Canonical wrapper present
    assert "mcpServers" in mcp
    # Both pre-existing servers migrated under the wrapper
    assert "powerloom" in mcp["mcpServers"]
    assert "github" in mcp["mcpServers"]
    # github's pre-existing config preserved
    assert mcp["mcpServers"]["github"]["url"] == "https://github.mcp/api"
    # powerloom's stale URL got refreshed to the current proxy URL
    assert mcp["mcpServers"]["powerloom"]["url"] == EXPECTED_URL
    # Top level is clean — no leftover keys from the broken shape
    leftover = {k for k in mcp.keys() if k != "mcpServers"}
    assert leftover == set(), f"unexpected leftover top-level keys: {leftover}"


def test_mcp_json_preserves_unknown_top_level_keys(project_dir):
    """If the existing .mcp.json has top-level keys that aren't server-shaped
    (don't have type/url/command/args), they're preserved untouched. Future-
    proofs against config formats that add metadata at the root."""
    project_dir.mkdir()
    pre = {
        "$schema": "https://example.com/mcp.schema.json",
        "metadata": {"author": "shane", "notes": "preserve me"},
    }
    (project_dir / ".mcp.json").write_text(json.dumps(pre))

    _invoke(project_dir)

    mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert mcp["$schema"] == "https://example.com/mcp.schema.json"
    assert mcp["metadata"]["author"] == "shane"
    assert "powerloom" in mcp["mcpServers"]
