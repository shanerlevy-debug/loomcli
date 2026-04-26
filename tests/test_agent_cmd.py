from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.commands.agent_cmd import _absolute_ws_url, _extract_text_from_event


runner = CliRunner()


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = "fake-token"
    cfg.api_base_url = "https://api.powerloom.org"
    cfg.request_timeout_seconds = 30
    return cfg


def test_root_help_lists_agentic_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ask" in result.stdout
    assert "chat" in result.stdout


@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_ask_without_token_bails(mock_load_cfg):
    cfg = _cfg()
    cfg.access_token = None
    mock_load_cfg.return_value = cfg

    result = runner.invoke(
        app,
        [
            "ask",
            "00000000-0000-0000-0000-000000000001",
            "hello",
        ],
    )

    assert result.exit_code != 0
    assert "Not signed in" in result.stdout


@patch("loomcli.commands.agent_cmd._stream_session_events", return_value="hello\n")
@patch("loomcli.commands.agent_cmd.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_ask_uuid_invokes_and_streams(
    mock_load_cfg,
    mock_client_cls,
    mock_stream,
):
    cfg = _cfg()
    mock_load_cfg.return_value = cfg
    client = MagicMock()
    agent_id = "00000000-0000-0000-0000-000000000001"
    session_id = "00000000-0000-0000-0000-000000000002"
    client.get.return_value = {
        "id": agent_id,
        "name": "alfred",
        "runtime_type": "openai",
        "model": "gpt-5.5",
    }
    client.post.return_value = {
        "session_id": session_id,
        "agent_id": agent_id,
        "status": "pending",
        "mode": "fire_and_forget",
        "ws_url": f"/sessions/{session_id}/stream?ticket=ticket-1",
    }
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["ask", agent_id, "What changed?"])

    assert result.exit_code == 0, result.stdout
    client.get.assert_called_once_with(f"/agents/{agent_id}")
    args, _ = client.post.call_args
    assert args[0] == f"/agents/{agent_id}/invoke"
    assert args[1]["prompt"] == "What changed?"
    assert args[1]["mode"] == "fire_and_forget"
    mock_stream.assert_called_once_with(
        f"wss://api.powerloom.org/sessions/{session_id}/stream?ticket=ticket-1",
        raw_events=False,
    )


@patch("loomcli.commands.agent_cmd._stream_session_events", return_value="ok\n")
@patch("loomcli.commands.agent_cmd.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_ask_path_resolves_agent_in_ou(
    mock_load_cfg,
    mock_client_cls,
    _mock_stream,
):
    cfg = _cfg()
    mock_load_cfg.return_value = cfg
    client = MagicMock()
    agent_id = "agent-123"
    session_id = "session-123"

    def get_side_effect(path: str, **params):
        if path == "/ous/tree":
            return [
                {
                    "id": "ou-root",
                    "name": "dev-org",
                    "display_name": "Dev Org",
                    "children": [
                        {
                            "id": "ou-eng",
                            "name": "engineering",
                            "display_name": "Engineering",
                            "children": [],
                        }
                    ],
                }
            ]
        if path == "/agents" and params == {"ou_id": "ou-eng"}:
            return [{"id": agent_id, "name": "alfred"}]
        raise AssertionError(f"unexpected GET {path} {params}")

    client.get.side_effect = get_side_effect
    client.post.return_value = {
        "session_id": session_id,
        "agent_id": agent_id,
        "status": "pending",
        "mode": "fire_and_forget",
        "ws_url": f"/sessions/{session_id}/stream?ticket=ticket-1",
    }
    mock_client_cls.return_value = client

    result = runner.invoke(
        app,
        ["ask", "/dev-org/engineering/alfred", "hello"],
    )

    assert result.exit_code == 0, result.stdout
    args, _ = client.post.call_args
    assert args[0] == "/agents/agent-123/invoke"


def test_absolute_ws_url_from_relative_path():
    assert (
        _absolute_ws_url(
            "https://api.powerloom.org",
            "/sessions/s1/stream?ticket=t1",
        )
        == "wss://api.powerloom.org/sessions/s1/stream?ticket=t1"
    )


def test_extract_text_from_common_event_shapes():
    assert _extract_text_from_event({"payload": {"text": "hi"}}) == "hi"
    assert _extract_text_from_event({"payload": {"delta": "hi"}}) == "hi"
    assert (
        _extract_text_from_event(
            {"payload": {"content": [{"type": "text", "text": "hi"}]}}
        )
        == "hi"
    )
