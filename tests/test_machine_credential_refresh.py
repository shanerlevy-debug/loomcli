"""Tests for machine-credential refresh-on-use + expiry handling.

Sprint auth-bootstrap-20260430, thread 648bca84.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loomcli import auth as auth_api
from loomcli import config as config_module


@pytest.fixture
def isolated_powerloom_home(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.delenv("POWERLOOM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("POWERLOOM_DISABLE_AUTO_REFRESH", raising=False)
    yield tmp_path


def _cred(*, refresh_offset_days: int = 76, expires_offset_days: int = 90) -> dict:
    """Build a credential where refresh_at is N days in the future
    (negative = past) and expires_at follows."""
    now = datetime.now(timezone.utc)
    return {
        "credential_id": "cred-id-aaaa",
        "token": "mcred_" + "a" * 32,
        "expires_at": (now + timedelta(days=expires_offset_days)).isoformat(),
        "refresh_at": (now + timedelta(days=refresh_offset_days)).isoformat(),
        "issued_at": now.isoformat(),
        "machine_fingerprint": "fp_test",
        "name": "test laptop",
    }


def _engine_refresh_response() -> dict:
    """Mirror the engine's refresh endpoint response shape."""
    now = datetime.now(timezone.utc)
    return {
        "credential_id": "cred-id-aaaa",
        "token": "mcred_" + "b" * 32,  # ROTATED
        "expires_at": (now + timedelta(days=90)).isoformat(),
        "refresh_at": (now + timedelta(days=76)).isoformat(),
    }


# ---------------------------------------------------------------------------
# is_in_refresh_window
# ---------------------------------------------------------------------------


class TestIsInRefreshWindow:
    def test_not_due_yet(self) -> None:
        cred = _cred(refresh_offset_days=5)  # 5 days in future
        assert auth_api.is_in_refresh_window(cred) is False

    def test_in_window(self) -> None:
        cred = _cred(refresh_offset_days=-1)  # 1 day past refresh
        assert auth_api.is_in_refresh_window(cred) is True

    def test_at_exact_boundary(self) -> None:
        # refresh_at = now → eligible
        now = datetime.now(timezone.utc)
        cred = {"refresh_at": (now - timedelta(seconds=1)).isoformat()}
        assert auth_api.is_in_refresh_window(cred) is True

    def test_no_refresh_at_field(self) -> None:
        assert auth_api.is_in_refresh_window({}) is False

    def test_unparseable_refresh_at(self) -> None:
        assert auth_api.is_in_refresh_window({"refresh_at": "not a date"}) is False

    def test_z_suffix_tolerated(self) -> None:
        now = datetime.now(timezone.utc)
        cred = {
            "refresh_at": (now - timedelta(days=1))
            .isoformat()
            .replace("+00:00", "Z")
        }
        assert auth_api.is_in_refresh_window(cred) is True


# ---------------------------------------------------------------------------
# refresh_machine_credential — happy + failure paths
# ---------------------------------------------------------------------------


class TestRefreshMachineCredential:
    def test_returns_none_when_no_credential_on_disk(
        self, isolated_powerloom_home
    ) -> None:
        cfg = MagicMock(api_base_url="https://api.example.com")
        cfg.active_profile = "default"
        result = auth_api.refresh_machine_credential(cfg)
        assert result is None

    def test_happy_path_rotates_token(self, isolated_powerloom_home) -> None:
        original = _cred()
        config_module.write_machine_credential(original)

        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.post.return_value = _engine_refresh_response()
        cfg = MagicMock(api_base_url="https://api.example.com")
        cfg.active_profile = "default"

        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            result = auth_api.refresh_machine_credential(cfg)

        assert result is not None
        assert result["token"].startswith("mcred_b")  # rotated
        # Disk reflects new token.
        loaded = config_module.read_machine_credential()
        assert loaded["token"] == result["token"]
        # Identity fields preserved.
        assert loaded["credential_id"] == original["credential_id"]
        assert loaded["machine_fingerprint"] == original["machine_fingerprint"]
        assert loaded["name"] == original["name"]
        # Engine called with credential's path.
        path, _body = fake_client.post.call_args.args
        assert path == "/auth/machine-credentials/cred-id-aaaa/refresh"

    def test_engine_401_returns_none_no_disk_write(
        self, isolated_powerloom_home
    ) -> None:
        original = _cred()
        config_module.write_machine_credential(original)

        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        from loomcli.client import PowerloomApiError
        fake_client.post.side_effect = PowerloomApiError(401, "expired")

        cfg = MagicMock(api_base_url="https://api.example.com")
        cfg.active_profile = "default"

        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            result = auth_api.refresh_machine_credential(cfg)

        assert result is None
        # On-disk credential is unchanged — current token still valid until expires_at.
        loaded = config_module.read_machine_credential()
        assert loaded["token"] == original["token"]

    def test_unexpected_exception_returns_none(
        self, isolated_powerloom_home
    ) -> None:
        config_module.write_machine_credential(_cred())
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.post.side_effect = RuntimeError("network blip")
        cfg = MagicMock(api_base_url="https://api.example.com")
        cfg.active_profile = "default"

        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            result = auth_api.refresh_machine_credential(cfg)

        assert result is None


# ---------------------------------------------------------------------------
# _read_credentials_file — refresh-on-read integration
# ---------------------------------------------------------------------------


class TestRefreshOnRead:
    def test_no_refresh_when_not_due(self, isolated_powerloom_home) -> None:
        config_module.write_machine_credential(_cred(refresh_offset_days=5))
        fake_client = MagicMock()
        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            token = config_module._read_credentials_file()
        assert token is not None
        # No refresh attempted — we're not in the window.
        fake_client.post.assert_not_called()

    def test_refresh_when_in_window(self, isolated_powerloom_home) -> None:
        config_module.write_machine_credential(_cred(refresh_offset_days=-1))
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.post.return_value = _engine_refresh_response()
        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            token = config_module._read_credentials_file()
        # Token is the *rotated* one from the engine response.
        assert token.startswith("mcred_b")
        path, _body = fake_client.post.call_args.args
        assert path.endswith("/refresh")

    def test_refresh_failure_returns_existing_token(
        self, isolated_powerloom_home
    ) -> None:
        original = _cred(refresh_offset_days=-1)
        config_module.write_machine_credential(original)
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        from loomcli.client import PowerloomApiError
        fake_client.post.side_effect = PowerloomApiError(500, "boom")
        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            token = config_module._read_credentials_file()
        # Refresh failed; original token still returned (it's still valid until expires_at).
        assert token == original["token"]

    def test_disable_env_var_short_circuits_refresh(
        self, isolated_powerloom_home, monkeypatch
    ) -> None:
        monkeypatch.setenv("POWERLOOM_DISABLE_AUTO_REFRESH", "1")
        config_module.write_machine_credential(_cred(refresh_offset_days=-1))
        fake_client = MagicMock()
        with patch("loomcli.auth.PowerloomClient", return_value=fake_client):
            token = config_module._read_credentials_file()
        assert token is not None
        fake_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# expired_machine_credential_meta
# ---------------------------------------------------------------------------


class TestExpiredCredentialMeta:
    def test_returns_none_when_no_file(self, isolated_powerloom_home) -> None:
        assert auth_api.expired_machine_credential_meta() is None

    def test_returns_none_when_credential_still_valid(
        self, isolated_powerloom_home
    ) -> None:
        config_module.write_machine_credential(_cred(expires_offset_days=30))
        assert auth_api.expired_machine_credential_meta() is None

    def test_returns_credential_when_expired(
        self, isolated_powerloom_home
    ) -> None:
        # write_machine_credential persists the dict verbatim — we
        # can't go through it because read_machine_credential would
        # have stripped expired creds first. Write directly.
        expired = _cred(expires_offset_days=-1)
        config_module.auth_file().write_text(
            json.dumps(expired), encoding="utf-8"
        )
        result = auth_api.expired_machine_credential_meta()
        assert result is not None
        assert result["credential_id"] == expired["credential_id"]


# ---------------------------------------------------------------------------
# expired credential surfaces in `weave whoami` UI message
# ---------------------------------------------------------------------------


def test_whoami_origin_message_for_expired_credential(
    isolated_powerloom_home, capsys
) -> None:
    """When mcred expired and there's no other auth, _print_credential_origin
    surfaces the actionable expiry message instead of the generic 'unknown'."""
    from loomcli.commands import auth_cmd

    expired = _cred(expires_offset_days=-1)
    config_module.auth_file().write_text(
        json.dumps(expired), encoding="utf-8"
    )

    auth_cmd._print_credential_origin()
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "expired" in out.lower()
    assert "weave open" in out
