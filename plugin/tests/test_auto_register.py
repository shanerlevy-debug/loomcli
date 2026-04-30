"""Tests for the Claude Code MCP server's auto-register module
(plugin/mcp-server/powerloom_home/auto_register.py).

Auto-register kicks in when a Claude Code deployment credential
exists at ``~/.config/powerloom/deployment-claude_code.json``
(written by ``weave register`` after M2-P2). The module mints a
session against ``POST /agent-sessions`` using the deployment_token
and tracks the session_id for end-of-process cleanup.

Coverage:
  * Credential discovery from per-user XDG path (M2-P2 shape).
  * Legacy single-file ``deployment.json`` accepted for back-compat
    when its credential_kind is null/default/claude_code.
  * Wrong-kind credentials (e.g. gemini_cli) ignored.
  * Malformed JSON / missing required fields → returns None.
  * No credential present → no-op (None).
  * open_session: 200 happy path + 401 (deployment archived) +
    network error all return None / clear-session-state semantics.
  * Scope override via POWERLOOM_SESSION_SCOPE env var.

All HTTP intercepted with respx — no live control plane needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import respx

# Add mcp-server/ to the path so `import powerloom_home.auto_register` resolves.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "mcp-server"))

from powerloom_home import auto_register


_TEST_API = "http://api.test"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point POWERLOOM_HOME at a scratch dir so tests don't read real
    credentials. auto_register's _config_dir() honors the env var."""
    home = tmp_path / "powerloom-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POWERLOOM_HOME", str(home))
    yield home


def _seed_credential(
    home: Path,
    *,
    filename: str = "deployment-claude_code.json",
    deployment_id: str = "11111111-2222-3333-4444-555555555555",
    deployment_token: str = "dep-test-token-LongEnough",
    agent_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    agent_slug: str = "claude_code_session",
    credential_kind: str | None = "claude_code",
    api_base_url: str = _TEST_API,
):
    payload = {
        "deployment_id": deployment_id,
        "deployment_token": deployment_token,
        "agent_id": agent_id,
        "agent_slug": agent_slug,
        "api_base_url": api_base_url,
        "runtime_config": {},
    }
    if credential_kind is not None:
        payload["credential_kind"] = credential_kind
    (home / filename).write_text(json.dumps(payload), encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------
def test_returns_none_when_no_credential_exists(_isolated_home):
    assert auto_register.read_deployment_credential() is None


def test_finds_per_kind_claude_code_credential(_isolated_home):
    """M2 shape — deployment-claude_code.json with credential_kind set."""
    seeded = _seed_credential(_isolated_home)
    found = auto_register.read_deployment_credential()
    assert found is not None
    assert found["deployment_id"] == seeded["deployment_id"]
    assert found["credential_kind"] == "claude_code"


def test_finds_legacy_deployment_json_when_kind_unset(_isolated_home):
    """Back-compat: a v0.7.12 deployment.json (no credential_kind
    field) is accepted as a Claude Code credential when it's the
    only thing on disk."""
    _seed_credential(
        _isolated_home,
        filename="deployment.json",
        credential_kind=None,
    )
    found = auto_register.read_deployment_credential()
    assert found is not None


def test_skips_wrong_kind_credential(_isolated_home):
    """A gemini_cli credential should NOT be picked up by Claude
    Code's auto-register — wrong kind."""
    _seed_credential(
        _isolated_home,
        filename="deployment-gemini_cli.json",
        credential_kind="gemini_cli",
    )
    assert auto_register.read_deployment_credential() is None


def test_prefers_claude_code_over_legacy_when_both_exist(_isolated_home):
    """When both deployment.json (legacy) and deployment-claude_code.json
    exist, the per-kind one wins because it's more specific."""
    _seed_credential(
        _isolated_home,
        filename="deployment-claude_code.json",
        deployment_id="cc-specific",
    )
    _seed_credential(
        _isolated_home,
        filename="deployment.json",
        deployment_id="legacy",
        credential_kind=None,
    )
    found = auto_register.read_deployment_credential()
    assert found["deployment_id"] == "cc-specific"


def test_malformed_json_returns_none(_isolated_home):
    (_isolated_home / "deployment-claude_code.json").write_text("not-json{")
    assert auto_register.read_deployment_credential() is None


def test_missing_required_fields_returns_none(_isolated_home):
    (_isolated_home / "deployment-claude_code.json").write_text(
        json.dumps({"credential_kind": "claude_code"})  # no deployment_token
    )
    assert auto_register.read_deployment_credential() is None


# ---------------------------------------------------------------------------
# Session POST
# ---------------------------------------------------------------------------
@respx.mock
def test_open_session_happy_path():
    """200 → returns the session row dict."""
    creds = {
        "deployment_id": "dep-1",
        "deployment_token": "dep-token-x",
        "agent_id": "agent-1",
        "agent_slug": "claude_code_session",
        "api_base_url": _TEST_API,
    }
    respx.post(f"{_TEST_API}/agent-sessions").mock(
        return_value=httpx.Response(
            200,
            json={"id": "session-1", "scope": "test-scope", "actor_kind": "claude_code"},
        )
    )

    result = auto_register.open_session(creds)
    assert result is not None
    assert result["id"] == "session-1"


@respx.mock
def test_open_session_401_returns_none():
    """Deployment archived server-side → 401 → return None.
    Caller logs the warning and the MCP server keeps serving local
    tools without an active session."""
    creds = {
        "deployment_id": "dep-1",
        "deployment_token": "dep-token-revoked",
        "agent_id": "agent-1",
        "agent_slug": "claude_code_session",
        "api_base_url": _TEST_API,
    }
    respx.post(f"{_TEST_API}/agent-sessions").mock(
        return_value=httpx.Response(401, json={"detail": "deployment archived"})
    )

    result = auto_register.open_session(creds)
    assert result is None


@respx.mock
def test_open_session_network_error_returns_none():
    """Network error (DNS failure, connection refused) → None.
    MCP server starts up regardless; auto-register is best-effort."""
    creds = {
        "deployment_id": "dep-1",
        "deployment_token": "dep-token-x",
        "agent_id": "agent-1",
        "agent_slug": "claude_code_session",
        "api_base_url": _TEST_API,
    }
    respx.post(f"{_TEST_API}/agent-sessions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    result = auto_register.open_session(creds)
    assert result is None


@respx.mock
def test_open_session_includes_deployment_token_as_bearer():
    """Auth header must use the deployment_token, not a PAT."""
    creds = {
        "deployment_id": "dep-1",
        "deployment_token": "dep-specific-token",
        "agent_id": "agent-1",
        "agent_slug": "claude_code_session",
        "api_base_url": _TEST_API,
    }
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "session-1"})

    respx.post(f"{_TEST_API}/agent-sessions").mock(side_effect=_capture)
    auto_register.open_session(creds)

    assert captured["authorization"] == "Bearer dep-specific-token"


@respx.mock
def test_open_session_passes_scope_from_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Scope defaults to the cwd's leaf name."""
    monkeypatch.chdir(tmp_path)
    creds = {
        "deployment_id": "dep-1",
        "deployment_token": "tok",
        "agent_id": "agent-1",
        "agent_slug": "claude_code_session",
        "api_base_url": _TEST_API,
    }
    captured = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id": "session-1"})

    respx.post(f"{_TEST_API}/agent-sessions").mock(side_effect=_capture)
    auto_register.open_session(creds)

    assert captured["body"]["scope"] == tmp_path.name


@respx.mock
def test_open_session_scope_override_via_env(monkeypatch: pytest.MonkeyPatch):
    """POWERLOOM_SESSION_SCOPE overrides the cwd-derived scope."""
    monkeypatch.setenv("POWERLOOM_SESSION_SCOPE", "explicit-scope-override")
    creds = {
        "deployment_id": "dep-1",
        "deployment_token": "tok",
        "agent_id": "agent-1",
        "agent_slug": "claude_code_session",
        "api_base_url": _TEST_API,
    }
    captured = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id": "session-1"})

    respx.post(f"{_TEST_API}/agent-sessions").mock(side_effect=_capture)
    auto_register.open_session(creds)

    assert captured["body"]["scope"] == "explicit-scope-override"


# ---------------------------------------------------------------------------
# Session close
# ---------------------------------------------------------------------------
@respx.mock
def test_close_session_swallows_errors():
    """Network failure during close shouldn't propagate — MCP server
    is shutting down anyway, no value in raising."""
    creds = {
        "deployment_token": "tok",
        "api_base_url": _TEST_API,
    }
    respx.post(f"{_TEST_API}/agent-sessions/session-1/end").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    # Just shouldn't raise.
    auto_register.close_session(creds, "session-1")


@respx.mock
def test_close_session_calls_correct_endpoint():
    creds = {
        "deployment_token": "tok",
        "api_base_url": _TEST_API,
    }
    route = respx.post(f"{_TEST_API}/agent-sessions/session-xyz/end").mock(
        return_value=httpx.Response(204)
    )

    auto_register.close_session(creds, "session-xyz")
    assert route.called
