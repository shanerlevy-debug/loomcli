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
    typing the wrong host.

    M2-P2 change: the server call happens first (we can't know the
    target path pre-call without burning some discovery cycle). The
    refusal lands AFTER the response, against the specific resolved
    path. Token is server-marked redeemed in this case; operator
    archives the orphaned deployment via the UI."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    # Pre-existing reconciler-style credential at the host/default path.
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

    # Server returns host/default — collides with the existing credential.
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ok_register_response())
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "already exists" in result.stdout
    assert "force" in result.stdout.lower()
    # Old credential still intact.
    payload = cfg_mod.read_deployment_credential()
    assert payload["deployment_id"] == "old-deployment"


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


# ---------------------------------------------------------------------------
# M2-P2: per-user XDG path + multi-credential support
# ---------------------------------------------------------------------------
def _ide_register_response(kind: str = "claude_code"):
    """Server response for an IDE-template-backed deployment.

    Matches what M2-P1's register_endpoint returns when the agent's
    template_slug indicates an IDE kind:
    credential_scope='user', credential_kind=<kind>.
    """
    base = _ok_register_response()
    base["credential_scope"] = "user"
    base["credential_kind"] = kind
    return base


@respx.mock
def test_register_writes_per_user_path_when_credential_scope_user(
    monkeypatch: pytest.MonkeyPatch,
):
    """M2-P2: server returns credential_scope='user' + kind='claude_code'
    -> credential lands at <XDG>/powerloom/deployment-claude_code.json
    (per-kind, not the bare deployment.json)."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ide_register_response("claude_code"))
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 0, result.stdout

    # Per-kind file should exist; bare deployment.json should NOT.
    expected_path = cfg_mod.deployment_credential_path(scope="user", kind="claude_code")
    bare_path = cfg_mod.deployment_credential_path(scope="user", kind="default")
    assert expected_path.exists(), f"expected {expected_path} to exist"
    assert not bare_path.exists(), (
        f"bare deployment.json should NOT exist when scope=user "
        f"with non-default kind; found: {bare_path}"
    )
    payload = json.loads(expected_path.read_text())
    assert payload["credential_scope"] == "user"
    assert payload["credential_kind"] == "claude_code"
    # Stdout should mention the scope so the operator sees where it landed.
    assert "user/claude_code" in result.stdout


@respx.mock
def test_register_two_ide_kinds_coexist_on_same_host(
    monkeypatch: pytest.MonkeyPatch,
):
    """M2-P2: registering two different IDE kinds on the same machine
    creates two separate credential files. Operator can have Claude Code
    + Codex paired without one clobbering the other."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)

    # First: register Claude Code.
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{_TEST_API}/deployments/register").mock(
            return_value=httpx.Response(200, json=_ide_register_response("claude_code"))
        )
        r1 = runner.invoke(app, ["register", "--token", _TOKEN])
        assert r1.exit_code == 0, r1.stdout

    # Second: register Codex CLI (different token, different kind).
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{_TEST_API}/deployments/register").mock(
            return_value=httpx.Response(
                200,
                json=_ide_register_response("codex_cli"),
            )
        )
        r2 = runner.invoke(app, ["register", "--token", "pat-deploy-second"])
        assert r2.exit_code == 0, r2.stdout

    # Both files should now exist.
    cc_path = cfg_mod.deployment_credential_path(scope="user", kind="claude_code")
    codex_path = cfg_mod.deployment_credential_path(scope="user", kind="codex_cli")
    assert cc_path.exists()
    assert codex_path.exists()

    # list_deployment_credentials should surface both kinds.
    creds = cfg_mod.list_deployment_credentials()
    assert "claude_code" in creds
    assert "codex_cli" in creds


@respx.mock
def test_register_ide_kind_does_not_conflict_with_existing_host_credential(
    monkeypatch: pytest.MonkeyPatch,
):
    """M2-P2: a host with an existing reconciler-style credential
    (host/default at /etc/powerloom/deployment.json or ~/.config/powerloom/
    deployment.json) doesn't refuse to register an IDE kind. The
    paths don't collide."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)

    # Seed a host/default credential (the M1 reconciler shape).
    cfg_mod.write_deployment_credential(
        {
            "deployment_id": "old-reconciler",
            "deployment_token": "dep-reconciler",
            "agent_id": "old-agent",
            "agent_slug": "reconciler",
            "api_base_url": _TEST_API,
            "runtime_config": {},
        }
    )
    # Now register a Claude Code IDE deployment — should succeed.
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{_TEST_API}/deployments/register").mock(
            return_value=httpx.Response(200, json=_ide_register_response("claude_code"))
        )
        result = runner.invoke(app, ["register", "--token", _TOKEN])
        assert result.exit_code == 0, result.stdout

    # Reconciler creds untouched.
    reconciler = cfg_mod.read_deployment_credential(kind="default")
    assert reconciler["deployment_id"] == "old-reconciler"
    # Claude Code creds present.
    cc = cfg_mod.read_deployment_credential(kind="claude_code")
    assert cc is not None
    assert cc["credential_kind"] == "claude_code"


@respx.mock
def test_register_user_scope_refuses_clobber_of_same_kind(
    monkeypatch: pytest.MonkeyPatch,
):
    """M2-P2: operator already has a Claude Code credential, tries to
    register another Claude Code deployment without --force -> refused
    AFTER the server call (post-response check)."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)

    # Seed an existing claude_code credential.
    cfg_mod.write_deployment_credential(
        {
            "deployment_id": "existing-cc",
            "deployment_token": "dep-cc-old",
            "agent_id": "old",
            "agent_slug": "claude_code_session",
            "api_base_url": _TEST_API,
            "runtime_config": {},
        },
        scope="user",
        kind="claude_code",
    )

    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=_ide_register_response("claude_code"))
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 1
    assert "already exists" in result.stdout

    # Old credential still intact.
    payload = cfg_mod.read_deployment_credential(kind="claude_code")
    assert payload["deployment_id"] == "existing-cc"


@respx.mock
def test_register_unknown_credential_scope_falls_back_to_host(
    monkeypatch: pytest.MonkeyPatch,
):
    """M2-P2 defense: server returns malformed credential_scope -> client
    treats as host (M1 default behavior). Don't crash."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", _TEST_API)
    bad_response = _ok_register_response()
    bad_response["credential_scope"] = "rogue_value"
    bad_response["credential_kind"] = "claude_code"
    respx.post(f"{_TEST_API}/deployments/register").mock(
        return_value=httpx.Response(200, json=bad_response)
    )

    result = runner.invoke(app, ["register", "--token", _TOKEN])
    assert result.exit_code == 0, result.stdout
    # Should have written to the host/default path.
    payload = cfg_mod.read_deployment_credential(kind="default")
    assert payload is not None


def test_list_deployment_credentials_returns_empty_when_none_exist():
    """list_deployment_credentials() returns {} when no credentials
    exist on the host."""
    creds = cfg_mod.list_deployment_credentials()
    assert creds == {}


def test_list_deployment_credentials_returns_all_kinds():
    """Multiple IDE credentials all surface in list_deployment_credentials."""
    cfg_mod.write_deployment_credential(
        {"deployment_id": "cc-1", "deployment_token": "dep-cc"},
        scope="user",
        kind="claude_code",
    )
    cfg_mod.write_deployment_credential(
        {"deployment_id": "codex-1", "deployment_token": "dep-codex"},
        scope="user",
        kind="codex_cli",
    )

    creds = cfg_mod.list_deployment_credentials()
    assert set(creds.keys()) == {"claude_code", "codex_cli"}
    assert creds["claude_code"]["deployment_id"] == "cc-1"
    assert creds["codex_cli"]["deployment_id"] == "codex-1"
