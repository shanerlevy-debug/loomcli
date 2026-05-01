"""Tests for the powerloom_platform stdio→HTTP MCP bridge.

Filed under tracker ``e1e61ca6`` ("Plugin: bridge hosted Powerloom MCP
tools into CC sessions outside the monorepo (Fix B)"). Companion to
PR #290's CMA push gating refactor.

Coverage:

  * Credential discovery — same paths as ``powerloom_home.auto_register``
    so paired hosts are detected by both servers identically.
  * Refuses credentials that lack ``api_base_url`` (the bridge needs
    the URL to proxy; home server's hardcoded fallback doesn't apply).
  * Refuses credentials with mismatched ``credential_kind`` (e.g.
    ``gemini_cli`` shouldn't pair with the Claude Code bridge).
  * Boots clean in zero-tools mode when no credential exists — no
    raised exception, no upstream connection attempted.
  * MCP URL composition — strips trailing slashes off ``api_base_url``
    and appends ``/mcp`` so a credential carrying either form works.

Stateless: no MCP SDK calls (just the credential-resolution + URL-
composition logic). The full upstream-bridge behavior would require a
live MCP server fixture; we verify the discrete pieces here and let
the integration check happen at deploy time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add mcp-server/ to the path so ``import powerloom_platform`` resolves —
# mirrors the existing test_auto_register.py pattern.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "mcp-server"))

from powerloom_platform import credential as cred_module  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point POWERLOOM_HOME at a scratch dir so tests don't read real
    credentials. credential._config_dir() honors the env var."""
    home = tmp_path / "powerloom-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POWERLOOM_HOME", str(home))
    yield home


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------
def _write_credential(home: Path, payload: dict) -> Path:
    """Helper: write a credential file at the canonical Claude Code path."""
    path = home / "deployment-claude_code.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_returns_none_when_no_credential_file(_isolated_home: Path):
    """No-credential is the dominant case for fresh installs. Bridge
    must boot quietly and expose zero tools — never raise."""
    assert cred_module.read_deployment_credential() is None


def test_reads_valid_claude_code_credential(_isolated_home: Path):
    """Happy path — full credential matches the M2-P2 shape from
    ``weave register --token=pat-deploy-...``."""
    _write_credential(
        _isolated_home,
        {
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "agent_slug": "powerloomdevcc2",
            "deployment_token": "pat-deploy-test123",
            "api_base_url": "https://api.powerloom.org",
            "credential_kind": "claude_code",
            "credential_scope": "user",
        },
    )
    cred = cred_module.read_deployment_credential()
    assert cred is not None
    assert cred["deployment_token"] == "pat-deploy-test123"
    assert cred["api_base_url"] == "https://api.powerloom.org"


def test_rejects_credential_without_api_base_url(_isolated_home: Path):
    """Bridge-specific guardrail: ``api_base_url`` is mandatory.

    The home server's auto_register has a hardcoded fallback
    (``https://api.powerloom.org``) but the bridge can't safely
    assume — a deployment credential without the URL probably means
    the operator's on a custom platform deploy and the wrong URL
    would silently send their tokens to the wrong place."""
    _write_credential(
        _isolated_home,
        {
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "deployment_token": "pat-deploy-test123",
            # api_base_url MISSING
        },
    )
    assert cred_module.read_deployment_credential() is None


def test_rejects_credential_without_token(_isolated_home: Path):
    """Without the bearer token the bridge has nothing to authenticate
    with. Even with a valid api_base_url, refuse + fall back to
    zero-tools mode."""
    _write_credential(
        _isolated_home,
        {
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "api_base_url": "https://api.powerloom.org",
            # deployment_token MISSING
        },
    )
    assert cred_module.read_deployment_credential() is None


def test_rejects_credential_with_wrong_kind(_isolated_home: Path):
    """A credential file authored for a different runtime (e.g.
    ``gemini_cli``) MUST NOT be hijacked by the Claude Code bridge.
    Each runtime gets its own kind."""
    _write_credential(
        _isolated_home,
        {
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "deployment_token": "pat-deploy-test123",
            "api_base_url": "https://api.powerloom.org",
            "credential_kind": "gemini_cli",
        },
    )
    assert cred_module.read_deployment_credential() is None


def test_accepts_credential_with_default_kind(_isolated_home: Path):
    """Pre-M2-P2 / legacy credentials had ``credential_kind=default``
    (or absent). Both should still pair with the bridge for back-
    compat — no hard cutover required."""
    _write_credential(
        _isolated_home,
        {
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "deployment_token": "pat-deploy-test123",
            "api_base_url": "https://api.powerloom.org",
            "credential_kind": "default",
        },
    )
    assert cred_module.read_deployment_credential() is not None


def test_accepts_credential_without_explicit_kind(_isolated_home: Path):
    """v0.7.12 single-file shape (no ``credential_kind``) still
    accepted for operators who registered before M2 landed."""
    _write_credential(
        _isolated_home,
        {
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "deployment_token": "pat-deploy-test123",
            "api_base_url": "https://api.powerloom.org",
        },
    )
    assert cred_module.read_deployment_credential() is not None


def test_falls_back_to_legacy_deployment_json(_isolated_home: Path):
    """When ``deployment-claude_code.json`` is missing but the legacy
    ``deployment.json`` exists, accept it. The credential paths list
    is ordered so the canonical name wins when both exist."""
    legacy_path = _isolated_home / "deployment.json"
    legacy_path.write_text(
        json.dumps({
            "deployment_id": "d3a16c2b",
            "agent_id": "a-12345",
            "deployment_token": "pat-deploy-legacy",
            "api_base_url": "https://api.powerloom.org",
        }),
        encoding="utf-8",
    )
    cred = cred_module.read_deployment_credential()
    assert cred is not None
    assert cred["deployment_token"] == "pat-deploy-legacy"


def test_canonical_path_wins_over_legacy(_isolated_home: Path):
    """When both files exist, ``deployment-claude_code.json`` takes
    priority over ``deployment.json`` — guards against a stale legacy
    file shadowing a freshly re-registered host."""
    _isolated_home.joinpath("deployment.json").write_text(
        json.dumps({
            "deployment_id": "OLD",
            "agent_id": "a-old",
            "deployment_token": "old-token",
            "api_base_url": "https://api.powerloom.org",
        }),
        encoding="utf-8",
    )
    _isolated_home.joinpath("deployment-claude_code.json").write_text(
        json.dumps({
            "deployment_id": "NEW",
            "agent_id": "a-new",
            "deployment_token": "new-token",
            "api_base_url": "https://api.powerloom.org",
            "credential_kind": "claude_code",
        }),
        encoding="utf-8",
    )
    cred = cred_module.read_deployment_credential()
    assert cred is not None
    assert cred["deployment_token"] == "new-token"


def test_handles_malformed_json(_isolated_home: Path):
    """Garbage-in produces no-credential rather than a crash."""
    _isolated_home.joinpath("deployment-claude_code.json").write_text(
        "{this is not json",
        encoding="utf-8",
    )
    assert cred_module.read_deployment_credential() is None


def test_handles_non_dict_json(_isolated_home: Path):
    """Valid JSON that isn't an object (e.g. a list) is rejected
    cleanly — the read function checks ``isinstance(payload, dict)``."""
    _isolated_home.joinpath("deployment-claude_code.json").write_text(
        json.dumps(["not", "a", "dict"]),
        encoding="utf-8",
    )
    assert cred_module.read_deployment_credential() is None


# ---------------------------------------------------------------------------
# MCP URL composition
# ---------------------------------------------------------------------------
def test_mcp_url_appends_mcp_path():
    """The credential carries the API root; the bridge connects to
    ``/mcp`` on that root (mirrors the project-local .mcp.json
    convention used in the powerloom monorepo)."""
    from powerloom_platform import __main__ as bridge

    assert bridge._build_mcp_url("https://api.powerloom.org") == "https://api.powerloom.org/mcp"


def test_mcp_url_strips_trailing_slash():
    """Operators sometimes write ``api_base_url`` with or without a
    trailing slash; both must produce the same MCP URL so the
    composition is robust."""
    from powerloom_platform import __main__ as bridge

    assert bridge._build_mcp_url("https://api.powerloom.org/") == "https://api.powerloom.org/mcp"
    assert bridge._build_mcp_url("https://api.powerloom.org") == "https://api.powerloom.org/mcp"


def test_mcp_url_handles_subpath():
    """A platform-deploy operator might host the API at ``/api`` —
    ``/api`` + ``/mcp`` should compose to ``/api/mcp``."""
    from powerloom_platform import __main__ as bridge

    assert (
        bridge._build_mcp_url("https://example.com/api")
        == "https://example.com/api/mcp"
    )
