from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = "fake-token"
    cfg.api_base_url = "https://api.powerloom.org"
    cfg.request_timeout_seconds = 30
    return cfg


@patch("loomcli.commands.approval_cmd.PowerloomClient")
@patch("loomcli.commands.approval_cmd.load_runtime_config")
def test_approval_wait_exits_when_approved(mock_load_cfg, mock_client_cls):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = {"id": "approval-1", "status": "approved"}
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["approval", "wait", "approval-1"])

    assert result.exit_code == 0, result.stdout
    assert "approved" in result.stdout
    client.get.assert_called_once_with("/approvals/approval-1")


@patch("loomcli.commands.approval_cmd.PowerloomClient")
@patch("loomcli.commands.approval_cmd.load_runtime_config")
def test_approval_wait_nonapproved_terminal_status_exits_2(
    mock_load_cfg,
    mock_client_cls,
):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = {"id": "approval-1", "status": "rejected"}
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["approval", "wait", "approval-1"])

    assert result.exit_code == 2, result.stdout
    assert "rejected" in result.stdout
