"""Tests for ``loomcli._open.session_reg``.

Sprint cli-weave-open-20260430, thread 5fab82ed.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loomcli._open.session_reg import (
    SESSION_ENV_FILENAME,
    RegisteredSession,
    SessionRegisterError,
    _scope_summary_from_spec,
    ensure_gitignore_entry,
    register_agent_session,
    write_session_env_file,
)
from loomcli.client import PowerloomApiError
from loomcli.schema.launch_spec import LaunchSpec


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _spec(**overrides) -> LaunchSpec:
    base = {
        "schema_version": 1,
        "launch_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-05-01T00:00:00Z",
        "expires_at": "2026-05-01T00:15:00Z",
        "redeemed_at": "2026-05-01T00:00:01Z",
        "actor": {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "email": "shane@bespoke-technology.com",
            "runtime": "claude_code",
        },
        "project": {
            "id": "33333333-3333-3333-3333-333333333333",
            "slug": "powerloom",
            "repo_url": "https://github.com/x/y.git",
            "default_branch": "main",
        },
        "scope": {
            "slug": "cc-test-20260501",
            "friendly_name": None,
            "branch_base": "main",
            "branch_name": "session/cc-test-20260501",
        },
        "runtime": "claude_code",
        "skills": [],
        "capabilities": ["python", "api"],
        "clone_auth": {"mode": "server_minted"},
        "mcp_config": {"servers": []},
        "rules_sync": [],
        "session_attach_token": None,
        "thread_id": None,
    }
    base.update(overrides)
    return LaunchSpec.model_validate(base)


def _engine_response() -> dict:
    """Mirror what /agent-sessions returns."""
    return {
        "session": {
            "id": "ab123456-0000-0000-0000-000000000000",
            "session_slug": "cc-test-20260501",
            "scope_summary": "weave open: cc-test-20260501 (claude_code)",
            "branch_name": "session/cc-test-20260501",
        },
        "work_chain_event_hash": "deadbeef",
        "overlap_warnings": [],
    }


# ---------------------------------------------------------------------------
# scope_summary derivation
# ---------------------------------------------------------------------------


def test_scope_summary_uses_friendly_name_when_present() -> None:
    spec = _spec(scope={
        "slug": "x", "friendly_name": "Shane laptop CC",
        "branch_base": "main", "branch_name": "session/x"
    })
    assert _scope_summary_from_spec(spec) == "Shane laptop CC"


def test_scope_summary_falls_back_to_slug_runtime_blurb() -> None:
    spec = _spec()
    assert "cc-test-20260501" in _scope_summary_from_spec(spec)
    assert "claude_code" in _scope_summary_from_spec(spec)


# ---------------------------------------------------------------------------
# register_agent_session
# ---------------------------------------------------------------------------


def test_register_posts_expected_body() -> None:
    client = MagicMock()
    client.post.return_value = _engine_response()
    spec = _spec(capabilities=["api", "python"])

    result = register_agent_session(client, spec)

    assert result.session_id == "ab123456-0000-0000-0000-000000000000"
    assert result.session_slug == "cc-test-20260501"
    client.post.assert_called_once()
    path, body = client.post.call_args.args
    assert path == "/agent-sessions"
    assert body["session_slug"] == "cc-test-20260501"
    assert body["branch_name"] == "session/cc-test-20260501"
    assert body["actor_kind"] == "claude_code"
    assert body["capabilities"] == ["api", "python"]
    assert "scope_summary" in body and body["scope_summary"]


def test_register_translates_api_error_to_session_register_error() -> None:
    client = MagicMock()
    client.post.side_effect = PowerloomApiError(
        409, "scope already active", body={"existing_session": "..."}
    )
    spec = _spec()

    with pytest.raises(SessionRegisterError) as excinfo:
        register_agent_session(client, spec)
    assert excinfo.value.status_code == 409
    assert excinfo.value.body == {"existing_session": "..."}


def test_register_overlap_warnings_pass_through() -> None:
    client = MagicMock()
    resp = _engine_response()
    resp["overlap_warnings"] = [
        {"message": "scope already plucked by jane@example.com"},
    ]
    client.post.return_value = resp

    result = register_agent_session(client, _spec())
    assert len(result.overlap_warnings) == 1
    assert "jane" in result.overlap_warnings[0]["message"]


# ---------------------------------------------------------------------------
# write_session_env_file
# ---------------------------------------------------------------------------


def test_write_session_env_writes_expected_keys(tmp_path: Path) -> None:
    spec = _spec()
    registered = RegisteredSession(
        session_id="ab123456-0000-0000-0000-000000000000",
        session_slug="cc-test-20260501",
        work_chain_event_hash="deadbeef",
        overlap_warnings=[],
        raw={},
    )

    target = write_session_env_file(tmp_path, registered, spec)
    assert target.name == SESSION_ENV_FILENAME
    body = target.read_text(encoding="utf-8")
    assert "POWERLOOM_SESSION_ID=ab123456-0000-0000-0000-000000000000" in body
    assert "POWERLOOM_SCOPE=cc-test-20260501" in body
    assert "POWERLOOM_PROJECT_ID=33333333-3333-3333-3333-333333333333" in body
    assert "POWERLOOM_RUNTIME=claude_code" in body
    assert "POWERLOOM_BRANCH=session/cc-test-20260501" in body
    assert "POWERLOOM_LAUNCH_TOKEN_REDEEMED_AT=2026-05-01T00:00:01" in body


def test_write_session_env_overwrites_existing(tmp_path: Path) -> None:
    """Resume-on-interrupt refreshes the env file with current values."""
    target = tmp_path / SESSION_ENV_FILENAME
    target.write_text("POWERLOOM_SESSION_ID=stale\n", encoding="utf-8")

    spec = _spec()
    registered = RegisteredSession(
        session_id="fresh-id",
        session_slug=spec.scope.slug,
        work_chain_event_hash=None,
        overlap_warnings=[],
        raw={},
    )
    write_session_env_file(tmp_path, registered, spec)
    body = (tmp_path / SESSION_ENV_FILENAME).read_text(encoding="utf-8")
    assert "stale" not in body
    assert "POWERLOOM_SESSION_ID=fresh-id" in body


# ---------------------------------------------------------------------------
# ensure_gitignore_entry
# ---------------------------------------------------------------------------


def test_ensure_gitignore_creates_when_missing(tmp_path: Path) -> None:
    assert ensure_gitignore_entry(tmp_path) is True
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert SESSION_ENV_FILENAME in body


def test_ensure_gitignore_appends_when_existing(tmp_path: Path) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text("*.pyc\n__pycache__/\n", encoding="utf-8")

    assert ensure_gitignore_entry(tmp_path) is True
    body = gi.read_text(encoding="utf-8")
    assert "*.pyc" in body  # existing entries preserved
    assert "__pycache__/" in body
    assert SESSION_ENV_FILENAME in body


def test_ensure_gitignore_idempotent_when_already_listed(tmp_path: Path) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text(f"*.pyc\n{SESSION_ENV_FILENAME}\n", encoding="utf-8")
    before = gi.read_text(encoding="utf-8")

    assert ensure_gitignore_entry(tmp_path) is False  # not modified
    assert gi.read_text(encoding="utf-8") == before  # byte-identical
