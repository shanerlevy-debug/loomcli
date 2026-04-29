"""Tests for ``weave register --token=...`` (Agent Lifecycle UX P3, v0.7.12).

Coverage:
  * Successful registration writes /etc/powerloom/deployment.json
    (or the per-user fallback) with mode 0600 and the right shape.
  * Invalid token (401) → exit 1 with actionable message.
  * Already-redeemed token (404) → exit 1.
  * Network error → exit 1.
  * Existing credential refused without --force.
  * --force overwrites.
  * --output overrides path.
  * --api-url overrides the env-default base URL.

All HTTP intercepted with respx — no live control plane needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from loomcli import config as cfg_mod
from loomcli.cli import app


runner = CliRunner()

_TEST_API = "http://api.test"
_TOKEN = "pat-deploy-AbCdEfGhIjKl0123456789"


def _ok_register_response(
    deployment_id: str = "11111111-2222-3333-4444-555555555555",
    deployment_token: str = "dep-LongTokenValueHere1234567890",
    agent_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    agent_slug: str = "reconciler",
    runtime_config: dict | None = None,
):
    """Build a server response shape matching
    powerloom_api/schemas/agent_deployment.py::RegisterResponse."""
    return {
        "deployment_id": deployment_id,
        "deployment_token": deployment_token,
        "agent_id": agent_id,
        "agent_slug": agent_slug,
        "runtime_config": runtime_config
        or {
            "interval_seconds": 10,
            "confidence_threshold": 0.8,
            "dry_run": False,
            "model": None,
        },
    }


@respx.mock
def test_register_writes_credential_file(monkeypatch: pytest.MonkeyPatch):
    """Happy path: 200 → credential written, 0600 perms (POSIX), good
    exit code, all required fields present."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ok_register_response())
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 0, result.stdout

    cred_path = cfg_mod.deployment_credential_path()
    assert cred_path.exists()
    payload = json.loads(cred_path.read_text())
    assert payload["deployment_id"] == "11111111-2222-3333-4444-555555555555"
    assert payload["deployment_token"].startswith("dep-")
    assert payload["agent_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert payload["agent_slug"] == "reconciler"
    assert payload["api_base_url"] == _TEST_API
    assert payload["runtime_config"]["interval_seconds"] == 10

    # POSIX-only: file mode is 0600. Skip perm assertion on Windows.
    if os.name != "nt":
        mode = oct(cred_path.stat().st_mode)[-3:]
        assert mode == "600", f"expected 0600, got {mode}"


@respx.mock
def test_register_rejects_existing_credential_without_force(
    monkeypatch: pytest.MonkeyPatch,
):
    """Refuses to clobber an existing credential — operator might be
    typing the wrong host."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    # Pre-existing credential.
    cfg_mod.write_deployment_credential(
        {
            "deployment_id": "old-deployment",
            "deployment_token": "dep-old",
            "agent_id": "old-agent",
            "agent_slug": "reconciler",
            "api_base_url": _TEST_API,
            "runtime_config": {},
        }
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "already exists" in result.stdout
    assert "force" in result.stdout.lower()
    # Nothing should have been POSTed.
    assert not respx.routes


@respx.mock
def test_register_force_overwrites_existing(monkeypatch: pytest.MonkeyPatch):
    """--force replaces the existing credential."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    cfg_mod.write_deployment_credential(
        {
            "deployment_id": "old-deployment",
            "deployment_token": "dep-old",
            "agent_id": "old-agent",
            "agent_slug": "reconciler",
            "api_base_url": _TEST_API,
            "runtime_config": {},
        }
    )
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ok_register_response(
            deployment_id="new-deployment",
        ))
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN, "--force"])
    assert result.exit_code == 0, result.stdout

    payload = cfg_mod.read_deployment_credential()
    assert payload["deployment_id"] == "new-deployment"


@respx.mock
def test_register_invalid_token_401_clear_message(
    monkeypatch: pytest.MonkeyPatch,
):
    """401 → actionable error, exit 1, no credential written."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(401, json={"detail": "invalid token"})
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "invalid" in result.stdout.lower() or "expired" in result.stdout.lower()
    assert not cfg_mod.deployment_credential_path().exists()


@respx.mock
def test_register_redeemed_token_404(monkeypatch: pytest.MonkeyPatch):
    """404 (server's behavior for already-redeemed) → same error path."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(404)
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1


@respx.mock
def test_register_unexpected_status_code(monkeypatch: pytest.MonkeyPatch):
    """500 → bail with the status code surfaced."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(500, text="db down")
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "500" in result.stdout


@respx.mock
def test_register_network_error(monkeypatch: pytest.MonkeyPatch):
    """httpx-level error (DNS, connection refused, etc.) → exit 1
    + clear message."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "Network" in result.stdout or "network" in result.stdout


@respx.mock
def test_register_missing_required_fields(monkeypatch: pytest.MonkeyPatch):
    """Server returned 200 but with a malformed body (missing
    deployment_token) — exit 1, don't write a half-credential."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(
            200,
            json={"deployment_id": "x", "agent_id": "y"},  # missing deployment_token
        )
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "deployment_token" in result.stdout
    assert not cfg_mod.deployment_credential_path().exists()


@respx.mock
def test_register_api_url_override(monkeypatch: pytest.MonkeyPatch):
    """--api-url overrides POWERLOOM_API_BASE_URL."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", "http://wrong.test")
    custom_api = "http://custom.test"
    respx.post(f"{custom_api}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ok_register_response())
    )

    result = runner.invoke(
        app, ["register", "--token", _TOKEN, "--api-url", custom_api]
    )
    assert result.exit_code == 0, result.stdout

    payload = cfg_mod.read_deployment_credential()
    assert payload["api_base_url"] == custom_api


@respx.mock
def test_register_output_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """--output writes to the named path instead of the default."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    custom_path = tmp_path / "custom-deployment.json"
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ok_register_response())
    )

    result = runner.invoke(
        app, ["register", "--token", _TOKEN, "--output", str(custom_path)]
    )
    assert result.exit_code == 0, result.stdout

    assert custom_path.exists()
    payload = json.loads(custom_path.read_text())
    assert payload["deployment_token"].startswith("dep-")


def test_register_strips_trailing_slash_from_api_url(
    monkeypatch: pytest.MonkeyPatch,
):
    """Trailing-slash hygiene: api_base_url stored without trailing /."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API + "/")
    with respx.mock(assert_all_called=True) as router:
        # Match either form — the rstrip happens before httpx posts so
        # the URL hitting respx will be slash-free.
        router.post(f"{_TEST_API}/deployments/register").mock(
            return_value=httpx.Response(200, json=_ok_register_response())
        )

        result = runner.invoke(app, ["register", "--token", _TOKEN])
        assert result.exit_code == 0, result.stdout

    payload = cfg_mod.read_deployment_credential()
    # Stored URL should not have the trailing slash.
    assert payload["api_base_url"] == _TEST_API
