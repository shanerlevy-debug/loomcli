"""Tests for ``loomcli._open.auth_bootstrap``.

Sprint auth-bootstrap-20260430, thread fbb69176.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loomcli import config as config_module
from loomcli._open.auth_bootstrap import (
    BootstrapResult,
    maybe_bootstrap_machine_credential,
)
from loomcli.client import PowerloomApiError


@pytest.fixture
def isolated_powerloom_home(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.delenv("POWERLOOM_ACCESS_TOKEN", raising=False)
    yield tmp_path


def _cfg() -> object:
    cfg = MagicMock()
    cfg.api_base_url = "https://api.example.com"
    cfg.access_token = "pat_existing"  # caller's existing PAT
    cfg.active_profile = "default"
    return cfg


def _engine_response() -> dict:
    return {
        "credential_id": "abc-1234",
        "token": "mcred_" + "x" * 32,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(days=90)
        ).isoformat(),
        "refresh_at": (
            datetime.now(timezone.utc) + timedelta(days=76)
        ).isoformat(),
    }


# ---------------------------------------------------------------------------
# happy path — fresh machine, mints + persists
# ---------------------------------------------------------------------------


def test_bootstrap_mints_and_persists_on_fresh_host(
    isolated_powerloom_home,
) -> None:
    """auth.json doesn't exist → exchange called → file written → minted=True."""
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = _engine_response()
    with patch(
        "loomcli.auth.PowerloomClient", return_value=fake_client
    ):
        result = maybe_bootstrap_machine_credential(
            _cfg(), launch_token="lt_aaaaaaaaaaaaaaaaaaaa", name="laptop"
        )
    assert isinstance(result, BootstrapResult)
    assert result.minted
    assert result.credential_id == "abc-1234"
    assert result.error is None
    assert result.skipped_reason is None
    # File written.
    assert config_module.auth_file().exists()
    loaded = config_module.read_machine_credential()
    assert loaded is not None
    assert loaded["token"].startswith("mcred_")
    # Engine call shape.
    path, body = fake_client.post.call_args.args
    assert path == "/auth/machine-credentials/exchange"
    assert body["launch_token"] == "lt_aaaaaaaaaaaaaaaaaaaa"
    assert body["name"] == "laptop"
    assert "machine_fingerprint" in body  # auto-computed


# ---------------------------------------------------------------------------
# idempotency — already have credential → skip
# ---------------------------------------------------------------------------


def test_bootstrap_skips_when_credential_already_exists(
    isolated_powerloom_home,
) -> None:
    """Pre-existing valid credential → no engine call, skipped_reason set."""
    config_module.write_machine_credential(
        {
            "credential_id": "old-id",
            "token": "mcred_" + "z" * 32,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(days=80)
            ).isoformat(),
            "refresh_at": (
                datetime.now(timezone.utc) + timedelta(days=66)
            ).isoformat(),
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "machine_fingerprint": None,
            "name": None,
        }
    )
    fake_client = MagicMock()
    with patch(
        "loomcli.auth.PowerloomClient", return_value=fake_client
    ):
        result = maybe_bootstrap_machine_credential(
            _cfg(), launch_token="lt_aaaaaaaaaaaaaaaaaaaa"
        )
    assert not result.minted
    assert result.skipped_reason == "already_have_machine_credential"
    assert result.error is None
    fake_client.post.assert_not_called()
    # Existing credential preserved.
    loaded = config_module.read_machine_credential()
    assert loaded["credential_id"] == "old-id"


def test_bootstrap_treats_expired_credential_as_missing(
    isolated_powerloom_home,
) -> None:
    """Expired auth.json → bootstrap proceeds + replaces."""
    config_module.write_machine_credential(
        {
            "credential_id": "old-expired",
            "token": "mcred_" + "z" * 32,
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
            "refresh_at": (
                datetime.now(timezone.utc) - timedelta(days=14)
            ).isoformat(),
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "machine_fingerprint": None,
            "name": None,
        }
    )
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = _engine_response()
    with patch(
        "loomcli.auth.PowerloomClient", return_value=fake_client
    ):
        result = maybe_bootstrap_machine_credential(
            _cfg(), launch_token="lt_aaaaaaaaaaaaaaaaaaaa"
        )
    assert result.minted
    # Replaced.
    loaded = config_module.read_machine_credential()
    assert loaded["credential_id"] == "abc-1234"


# ---------------------------------------------------------------------------
# engine errors are non-fatal — recorded as `error`, no exception
# ---------------------------------------------------------------------------


def test_bootstrap_410_already_exchanged_returns_error_not_raise(
    isolated_powerloom_home,
) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.side_effect = PowerloomApiError(
        410, "already exchanged"
    )
    with patch(
        "loomcli.auth.PowerloomClient", return_value=fake_client
    ):
        result = maybe_bootstrap_machine_credential(
            _cfg(), launch_token="lt_aaaaaaaaaaaaaaaaaaaa"
        )
    assert not result.minted
    assert result.error is not None
    assert "410" in result.error
    # No file written.
    assert not config_module.auth_file().exists()


def test_bootstrap_404_unknown_token_returns_error_not_raise(
    isolated_powerloom_home,
) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.side_effect = PowerloomApiError(
        404, "launch not found"
    )
    with patch(
        "loomcli.auth.PowerloomClient", return_value=fake_client
    ):
        result = maybe_bootstrap_machine_credential(
            _cfg(), launch_token="lt_aaaaaaaaaaaaaaaaaaaa"
        )
    assert not result.minted
    assert "404" in result.error


def test_bootstrap_unexpected_exception_returns_error(
    isolated_powerloom_home,
) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.side_effect = RuntimeError("network hiccup")
    with patch(
        "loomcli.auth.PowerloomClient", return_value=fake_client
    ):
        result = maybe_bootstrap_machine_credential(
            _cfg(), launch_token="lt_aaaaaaaaaaaaaaaaaaaa"
        )
    assert not result.minted
    assert "network hiccup" in result.error
