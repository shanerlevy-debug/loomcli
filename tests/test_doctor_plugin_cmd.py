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


def test_plugin_instructions_prints_claude_code_standard_install():
    result = runner.invoke(app, ["plugin", "instructions", "claude-code"])

    assert result.exit_code == 0, result.stdout
    assert "weave plugin install claude-code --execute" in result.stdout
    assert "--project-dir" in result.stdout


def test_plugin_path_exports_codex_marketplace(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path / "pl-home"))

    result = runner.invoke(app, ["plugin", "path", "codex", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["client"] == "codex"
    assert payload["path"].startswith(str(tmp_path / "pl-home"))
    assert "plugins" in payload["path"]


def test_plugin_path_honors_plugin_home_override(tmp_path, monkeypatch):
    monkeypatch.delenv("POWERLOOM_HOME", raising=False)
    monkeypatch.setenv("POWERLOOM_PLUGIN_HOME", str(tmp_path / "plugin-home"))

    result = runner.invoke(app, ["plugin", "path", "codex", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["path"].startswith(str(tmp_path / "plugin-home"))


def test_plugin_install_defaults_to_dry_run():
    result = runner.invoke(app, ["plugin", "install", "gemini"])

    assert result.exit_code == 0, result.stdout
    assert "gemini extensions install" in result.stdout
    assert "Dry run" in result.stdout


@patch("loomcli.commands.plugin_cmd.plugin_path")
def test_plugin_install_single_client_exports_only_requested(mock_plugin_path, tmp_path):
    mock_plugin_path.return_value = tmp_path / "codex"

    result = runner.invoke(app, ["plugin", "install", "codex"])

    assert result.exit_code == 0, result.stdout
    mock_plugin_path.assert_called_once_with("codex")
    assert "codex plugin marketplace add" in result.stdout


def test_plugin_install_claude_code_accepts_project_dir(tmp_path):
    project_dir = tmp_path / "project"
    result = runner.invoke(
        app,
        [
            "plugin",
            "install",
            "claude-code",
            "--project-dir",
            str(project_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "weave setup-claude-code" in result.stdout
    assert "--project-dir" in result.stdout
    assert str(project_dir) in result.stdout.replace("\n", "")
    assert "Dry run" in result.stdout


@patch("loomcli.commands.plugin_cmd.shutil.which", return_value=None)
def test_plugin_install_execute_missing_binary_actionable(_mock_which):
    """When `gemini` (or other client binary) isn't on PATH and the user
    runs --execute, surface a friendly error pointing at the install URL
    instead of the bare WinError 2 / FileNotFoundError surface.
    """
    result = runner.invoke(app, ["plugin", "install", "gemini", "--execute"])

    assert result.exit_code == 1
    assert "not on PATH" in result.stdout
    # Hint should mention the install URL.
    assert "gemini-cli" in result.stdout.lower()
    # Should suggest re-running once installed.
    assert "weave plugin install gemini --execute" in result.stdout


@patch("loomcli.commands.plugin_cmd.subprocess.run")
@patch("loomcli.commands.plugin_cmd._codex_marketplace_source", return_value=None)
@patch(
    "loomcli.commands.plugin_cmd.shutil.which",
    return_value=r"C:\Users\white\AppData\Roaming\npm\codex.CMD",
)
def test_plugin_install_execute_uses_resolved_windows_shim(
    mock_which,
    _mock_source,
    mock_run,
    tmp_path,
    monkeypatch,
):
    """On Windows, npm-installed CLIs are often .CMD shims.

    `subprocess.run(["codex", ...])` can fail with WinError 2 even when
    `shutil.which("codex")` found codex.CMD. Execute the resolved path.
    """
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path / "pl-home"))

    result = runner.invoke(app, ["plugin", "install", "codex", "--execute"])

    assert result.exit_code == 0, result.stdout
    mock_which.assert_called_with("codex")
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0][0].endswith("codex.CMD")
    assert mock_run.call_args.args[0][1:4] == [
        "plugin",
        "marketplace",
        "add",
    ]


@patch("loomcli.commands.plugin_cmd.subprocess.run")
@patch(
    "loomcli.commands.plugin_cmd.shutil.which",
    return_value=r"C:\Users\white\AppData\Roaming\npm\codex.CMD",
)
def test_plugin_install_codex_replaces_existing_marketplace(
    _mock_which,
    mock_run,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path / "pl-home"))
    with patch(
        "loomcli.commands.plugin_cmd._codex_marketplace_source",
        return_value=r"\\?\D:\PowerLoom\old\plugins\codex",
    ):
        result = runner.invoke(app, ["plugin", "install", "codex", "--execute"])

    assert result.exit_code == 0, result.stdout
    assert "Replacing existing Codex marketplace" in result.stdout
    calls = [call.args[0] for call in mock_run.call_args_list]
    assert calls[0] == [
        r"C:\Users\white\AppData\Roaming\npm\codex.CMD",
        "plugin",
        "marketplace",
        "remove",
        "powerloom",
    ]
    assert calls[1][0] == r"C:\Users\white\AppData\Roaming\npm\codex.CMD"
    assert calls[1][1:4] == ["plugin", "marketplace", "add"]


@patch("loomcli.commands.plugin_cmd.subprocess.run")
@patch(
    "loomcli.commands.plugin_cmd.shutil.which",
    return_value=r"C:\Users\white\AppData\Roaming\npm\codex.CMD",
)
def test_plugin_install_codex_noops_when_marketplace_source_matches(
    _mock_which,
    mock_run,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path / "pl-home"))
    expected_path = tmp_path / "pl-home" / "plugins" / "0.7.3" / "codex"
    with patch(
        "loomcli.commands.plugin_cmd._codex_marketplace_source",
        return_value=f"\\\\?\\{expected_path}",
    ):
        result = runner.invoke(app, ["plugin", "install", "codex", "--execute"])

    assert result.exit_code == 0, result.stdout
    assert "already points at the exported path" in result.stdout
    mock_run.assert_not_called()


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
