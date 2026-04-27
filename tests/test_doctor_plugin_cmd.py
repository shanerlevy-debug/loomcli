from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = "fake-token"
    cfg.api_base_url = "https://api.powerloom.org"
    cfg.request_timeout_seconds = 30
    cfg.approval_justification = None
    return cfg


@patch("loomcli.commands.doctor_cmd.shutil.which", return_value="C:\\bin\\tool.exe")
@patch("loomcli.commands.doctor_cmd.PowerloomClient")
@patch("loomcli.commands.doctor_cmd.load_runtime_config")
def test_doctor_uses_capabilities_and_whoami(
    mock_load_cfg,
    mock_client_cls,
    _mock_which,
):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = [
        {
            "server_version": "0.1.0",
            "api_contract_version": "powerloom.capabilities.v1",
            "actor_kinds": ["claude_code", "codex_cli", "gemini_cli", "antigravity"],
            "routes": [{"key": "threads_my_work"}],
        },
        {"email": "admin@dev.local", "organization_id": "org-1"},
    ]
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "server.capabilities" in result.stdout
    assert "actor_kind.codex_cli" in result.stdout
    assert "auth.whoami" in result.stdout


def test_plugin_instructions_prints_codex_marketplace_path():
    result = runner.invoke(app, ["plugin", "instructions", "codex"])

    assert result.exit_code == 0, result.stdout
    assert "codex plugin marketplace add" in result.stdout
    assert "powerloom-weave" in result.stdout


def test_plugin_path_exports_codex_marketplace(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path / "pl-home"))

    result = runner.invoke(app, ["plugin", "path", "codex", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["client"] == "codex"
    assert payload["path"].startswith(str(tmp_path / "pl-home"))
    assert "plugins" in payload["path"]


def test_plugin_install_defaults_to_dry_run():
    result = runner.invoke(app, ["plugin", "install", "gemini"])

    assert result.exit_code == 0, result.stdout
    assert "gemini extensions install" in result.stdout
    assert "Dry run" in result.stdout


@patch("loomcli.commands.plugin_cmd.shutil.which", return_value="C:\\bin\\tool.exe")
def test_plugin_doctor_lists_clients(_mock_which):
    result = runner.invoke(app, ["plugin", "doctor"])

    assert result.exit_code == 0, result.stdout
    assert "codex" in result.stdout
    assert "gemini" in result.stdout


@patch("loomcli.commands.plugin_cmd.shutil.which", return_value="C:\\bin\\tool.exe")
def test_plugin_doctor_json_includes_export_root(_mock_which):
    result = runner.invoke(app, ["plugin", "doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "export_root" in payload
    assert any(row["client"] == "codex" for row in payload["clients"])
