"""Tests for the ``weave doctor`` launch-readiness section.

Sprint polish-doctor-resume-20260430, thread f3ebdda4.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.commands.doctor_cmd import _append_launch_readiness_checks


runner = CliRunner()


# Helper — mock client that returns the given /organizations/me/settings response.
def _client_returning(settings_resp, sessions_resp=None):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    def _get(path: str, **kw):
        if path == "/organizations/me/settings":
            return settings_resp
        if path == "/agent-sessions":
            return sessions_resp or []
        return None

    client.get.side_effect = _get
    return client


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """POWERLOOM_HOME under tmp so machine-credential reads don't see real data."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.delenv("POWERLOOM_ACCESS_TOKEN", raising=False)
    yield tmp_path


# ---------------------------------------------------------------------------
# Direct calls into _append_launch_readiness_checks
# ---------------------------------------------------------------------------


def test_clone_auth_mode_ok_when_settings_returns_value(
    isolated_home,
) -> None:
    cfg = MagicMock(api_base_url="https://api.example.com", access_token="pat_x")
    fake_client = _client_returning(
        {"organization_id": "org-1", "clone_auth_mode": "server_minted"},
    )
    checks: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks, cfg)
    by_key = {c["key"]: c for c in checks}
    assert by_key["launch.org.clone_auth_mode"]["status"] == "ok"
    assert (
        by_key["launch.org.clone_auth_mode"]["detail"] == "server_minted"
    )


def test_clone_auth_mode_warn_when_settings_unavailable(
    isolated_home,
) -> None:
    cfg = MagicMock(api_base_url="https://api.example.com", access_token="pat_x")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.side_effect = Exception("404")
    checks: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks, cfg)
    by_key = {c["key"]: c for c in checks}
    assert by_key["launch.org.clone_auth_mode"]["status"] == "warn"


def test_machine_credential_present_renders_id_and_expiry(
    isolated_home,
) -> None:
    from loomcli.config import write_machine_credential

    expires = (datetime.now(timezone.utc) + timedelta(days=80)).isoformat()
    write_machine_credential(
        {
            "credential_id": "abc12345-cred-id-here",
            "token": "mcred_" + "x" * 32,
            "expires_at": expires,
            "refresh_at": expires,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "machine_fingerprint": None,
            "name": None,
        },
    )

    cfg = MagicMock(api_base_url="https://api.example.com", access_token="pat_x")
    fake_client = _client_returning(
        {"clone_auth_mode": "server_minted"},
    )
    checks: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks, cfg)
    by_key = {c["key"]: c for c in checks}
    assert by_key["launch.machine_credential"]["status"] == "ok"
    assert "abc12345" in by_key["launch.machine_credential"]["detail"]


def test_machine_credential_absent_emits_info(isolated_home) -> None:
    cfg = MagicMock(api_base_url="https://api.example.com", access_token="pat_x")
    fake_client = _client_returning({"clone_auth_mode": "server_minted"})
    checks: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks, cfg)
    by_key = {c["key"]: c for c in checks}
    assert by_key["launch.machine_credential"]["status"] == "info"


def test_local_creds_check_only_when_org_mode_is_local(isolated_home) -> None:
    cfg = MagicMock(api_base_url="https://api.example.com", access_token="pat_x")

    # server_minted → no local-creds check appended
    fake_client = _client_returning({"clone_auth_mode": "server_minted"})
    checks: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks, cfg)
    keys = [c["key"] for c in checks]
    assert "launch.local_clone_credentials" not in keys

    # local_credentials → check IS appended (status depends on env, but
    # the row exists)
    fake_client = _client_returning({"clone_auth_mode": "local_credentials"})
    checks_local: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks_local, cfg)
    keys_local = [c["key"] for c in checks_local]
    assert "launch.local_clone_credentials" in keys_local


def test_active_sessions_count_rendered(isolated_home) -> None:
    cfg = MagicMock(api_base_url="https://api.example.com", access_token="pat_x")
    fake_client = _client_returning(
        {"clone_auth_mode": "server_minted"},
        sessions_resp=[
            {"id": "s1", "status": "active"},
            {"id": "s2", "status": "active"},
            {"id": "s3", "status": "active"},
        ],
    )
    checks: list[dict] = []
    with patch(
        "loomcli.client.PowerloomClient", return_value=fake_client
    ):
        _append_launch_readiness_checks(checks, cfg)
    by_key = {c["key"]: c for c in checks}
    assert "launch.active_sessions" in by_key
    assert "3 active" in by_key["launch.active_sessions"]["detail"]
