"""Tests for ``weave agent run`` deployment-credential mode (P3, v0.7.12).

When /etc/powerloom/deployment.json (or its per-user fallback) is
present, the daemon reads it and:

  * Authenticates with deployment_token (Bearer dep-...).
  * Skips the ``--agent`` positional arg + agent-resolution dance.
  * Per tick: GETs runtime-config (with If-None-Match), POSTs heartbeat.
  * Exits cleanly when heartbeat returns 401 (deployment archived).

These tests cover the new behavior. The legacy PAT path lives in
test_agent_daemon.py and isn't re-tested here.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli import config as cfg_mod
from loomcli.cli import app
from loomcli.commands import agent_daemon


runner = CliRunner()

_DEPLOYMENT_ID = "11111111-2222-3333-4444-555555555555"
_AGENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _seed_deployment_credential(
    monkeypatch: pytest.MonkeyPatch,
    *,
    interval_seconds: int = 10,
    confidence_threshold: float = 0.8,
    dry_run: bool = False,
    model: str | None = None,
    api_base_url: str = "http://api.test",
):
    """Drop a deployment.json into POWERLOOM_HOME so the daemon picks
    it up on the next read."""
    cfg_mod.write_deployment_credential(
        {
            "deployment_id": _DEPLOYMENT_ID,
            "agent_id": _AGENT_ID,
            "agent_slug": "reconciler",
            "deployment_token": "dep-test-token-LongEnough",
            "api_base_url": api_base_url,
            "runtime_config": {
                "interval_seconds": interval_seconds,
                "confidence_threshold": confidence_threshold,
                "dry_run": dry_run,
                "model": model,
            },
        }
    )


def _make_mock_client_responding_with_empty_queue(
    *,
    config_etag: str | None = None,
    config_response_status: int = 304,
    config_response_body: dict | None = None,
    heartbeat_status: int = 200,
):
    """Build a MagicMock PowerloomClient whose `_http.request` returns
    the right shapes for runtime-config polls + heartbeats, and whose
    `.get` returns an empty work queue."""
    client = MagicMock()
    # /agents/{id}/work-queue path on the high-level wrapper.
    client.get.return_value = {"items": [], "next_cursor": None}

    def _fake_request(method, path, **kwargs):
        response = MagicMock()
        if path.endswith("/runtime-config") and method == "GET":
            response.status_code = config_response_status
            response.headers = {"ETag": config_etag} if config_etag else {}
            response.json.return_value = config_response_body or {}
            return response
        if path.endswith("/heartbeat") and method == "POST":
            response.status_code = heartbeat_status
            response.headers = {}
            response.json.return_value = {}
            return response
        raise AssertionError(f"unexpected {method} {path}")

    client._http.request.side_effect = _fake_request
    return client


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------
def test_run_uses_deployment_credential_when_present(
    monkeypatch: pytest.MonkeyPatch,
):
    """With a deployment.json on disk, the daemon prefers it over the
    PAT path: no positional argument needed, agent_id comes from the
    credential."""
    _seed_deployment_credential(monkeypatch)

    with patch("loomcli.commands.agent_daemon.PowerloomClient") as cls:
        client = _make_mock_client_responding_with_empty_queue()
        cls.return_value = client

        result = runner.invoke(app, ["agent", "run", "--once"])

    assert result.exit_code == 0, result.stdout
    # Banner mentions the deployment.
    assert "deployment" in result.stdout.lower()
    assert _DEPLOYMENT_ID in result.stdout
    # Work queue was hit at the agent_id from the credential.
    queue_calls = [
        c.args[0] for c in client.get.call_args_list
        if c.args[0].endswith("/work-queue")
    ]
    assert queue_calls == [f"/agents/{_AGENT_ID}/work-queue"]


def test_heartbeat_called_each_tick(monkeypatch: pytest.MonkeyPatch):
    """Heartbeat POSTed exactly once during a --once tick."""
    _seed_deployment_credential(monkeypatch)

    with patch("loomcli.commands.agent_daemon.PowerloomClient") as cls:
        client = _make_mock_client_responding_with_empty_queue()
        cls.return_value = client

        result = runner.invoke(app, ["agent", "run", "--once"])
    assert result.exit_code == 0, result.stdout

    heartbeats = [
        c for c in client._http.request.call_args_list
        if c.args == ("POST",) + (f"/deployments/{_DEPLOYMENT_ID}/heartbeat",)
        or (
            c.args[0] == "POST"
            and c.args[1] == f"/deployments/{_DEPLOYMENT_ID}/heartbeat"
        )
    ]
    assert len(heartbeats) == 1


def test_runtime_config_304_keeps_local_config(
    monkeypatch: pytest.MonkeyPatch,
):
    """ETag-matched config response (304) → local options unchanged.
    Specifically: dry_run stays False as set in the credential."""
    _seed_deployment_credential(monkeypatch, dry_run=False)

    with patch("loomcli.commands.agent_daemon.PowerloomClient") as cls:
        client = _make_mock_client_responding_with_empty_queue(
            config_response_status=304,
        )
        cls.return_value = client

        result = runner.invoke(app, ["agent", "run", "--once"])
    assert result.exit_code == 0, result.stdout
    # No 'dry-run' badge in the queue line because dry_run stayed False.
    assert "(dry-run)" not in result.stdout


def test_runtime_config_200_replaces_local_config(
    monkeypatch: pytest.MonkeyPatch,
):
    """200 → daemon picks up the new runtime_config + new etag.
    Specifically: dry_run flips to True between credential's value and
    server's response."""
    _seed_deployment_credential(monkeypatch, dry_run=False)

    with patch("loomcli.commands.agent_daemon.PowerloomClient") as cls:
        client = _make_mock_client_responding_with_empty_queue(
            config_response_status=200,
            config_response_body={
                "runtime_config": {
                    "interval_seconds": 30,
                    "confidence_threshold": 0.9,
                    "dry_run": True,
                    "model": "claude-something",
                },
                "config_etag": "abc123",
            },
        )
        cls.return_value = client

        result = runner.invoke(app, ["agent", "run", "--once"])
    assert result.exit_code == 0, result.stdout
    # Dry-run badge present because the server-pushed config flipped
    # dry_run on, and the loop applies it before the work queue line.
    assert "(dry-run)" in result.stdout


def test_heartbeat_401_exits_clean_with_code_3(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the deployment is archived server-side, the next heartbeat
    returns 401 → daemon exits with code 3 + an actionable message."""
    _seed_deployment_credential(monkeypatch)

    with patch("loomcli.commands.agent_daemon.PowerloomClient") as cls:
        client = _make_mock_client_responding_with_empty_queue(
            heartbeat_status=401,
        )
        cls.return_value = client

        result = runner.invoke(app, ["agent", "run", "--once"])
    # The Typer runner surfaces the typer.Exit code in result.exit_code.
    assert result.exit_code == 3, (result.exit_code, result.stdout)
    assert "revoked" in result.stdout.lower() or "archived" in result.stdout.lower()
    assert "weave register" in result.stdout


def test_explicit_agent_arg_skips_deployment_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the operator passes an explicit agent positional, the daemon
    uses the legacy PAT path even if a deployment.json exists. Lets ops
    debug a different agent without removing the credential."""
    _seed_deployment_credential(monkeypatch)
    # The daemon will fall through to RuntimeConfig; the autouse fixture
    # provided a 'test-token' PAT so legacy path doesn't bail on auth.

    with patch(
        "loomcli.commands.agent_cmd.load_runtime_config"
    ) as mock_cfg, patch(
        "loomcli.commands.agent_daemon.PowerloomClient"
    ) as cls:
        from unittest.mock import MagicMock as _MM
        cfg = _MM()
        cfg.access_token = "test-pat"
        cfg.api_base_url = "http://api.test"
        cfg.request_timeout_seconds = 30
        cfg.approval_justification = None
        mock_cfg.return_value = cfg

        client = MagicMock()
        # Legacy path: GET /agents/<id> for runtime_type sanity, then
        # GET /agents/<id>/work-queue.
        explicit_id = "00000000-0000-0000-0000-000000000099"
        client.get.side_effect = lambda path, **params: (
            {
                "id": explicit_id,
                "name": "other-agent",
                "runtime_type": "self_hosted",
                "task_kinds": ["pr_reconciliation"],
            }
            if path == f"/agents/{explicit_id}"
            else {"items": [], "next_cursor": None}
        )
        cls.return_value = client

        result = runner.invoke(app, ["agent", "run", explicit_id, "--once"])
    assert result.exit_code == 0, result.stdout
    # Banner says 'task_kinds' (legacy mode), not 'deployment'.
    assert "task_kinds" in result.stdout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_binding_from_credential_validates_required_fields():
    """Sanity check: the credential parser bails on missing fields."""
    with pytest.raises(ValueError):
        agent_daemon._binding_from_credential({"deployment_id": "x"})


def test_binding_from_credential_strips_trailing_slash():
    binding = agent_daemon._binding_from_credential(
        {
            "deployment_id": "d",
            "deployment_token": "dep-x",
            "agent_id": "a",
            "api_base_url": "http://api.test/",
            "agent_slug": "reconciler",
            "runtime_config": {},
        }
    )
    assert binding.api_base_url == "http://api.test"
