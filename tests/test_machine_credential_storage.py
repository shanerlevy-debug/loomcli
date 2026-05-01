"""Tests for machine credential storage in ``loomcli.config`` + ``loomcli.auth``.

Sprint auth-bootstrap-20260430, thread fbb69176.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from loomcli import auth as auth_api
from loomcli import config as config_module


# ---------------------------------------------------------------------------
# isolation fixture — every test gets its own POWERLOOM_HOME
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_powerloom_home(tmp_path, monkeypatch):
    """Redirect POWERLOOM_HOME so config_dir() returns a tmp dir."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    # Strip any inherited env tokens so they don't muddy resolution-order tests.
    monkeypatch.delenv("POWERLOOM_ACCESS_TOKEN", raising=False)
    yield tmp_path


def _valid_credential(**overrides) -> dict:
    base = {
        "credential_id": "11111111-1111-1111-1111-111111111111",
        "token": "mcred_" + "a" * 32,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(days=90)
        ).isoformat(),
        "refresh_at": (
            datetime.now(timezone.utc) + timedelta(days=76)
        ).isoformat(),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "machine_fingerprint": "fp_test",
        "name": "test laptop",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# config.auth_file() / read / write / clear
# ---------------------------------------------------------------------------


def test_auth_file_path_under_config_dir(isolated_powerloom_home: Path) -> None:
    target = config_module.auth_file()
    assert target.name == "auth.json"
    assert target.parent == isolated_powerloom_home


def test_read_machine_credential_returns_none_when_missing(
    isolated_powerloom_home,
) -> None:
    assert config_module.read_machine_credential() is None


def test_write_then_read_roundtrips(isolated_powerloom_home) -> None:
    cred = _valid_credential()
    config_module.write_machine_credential(cred)
    loaded = config_module.read_machine_credential()
    assert loaded is not None
    assert loaded["token"] == cred["token"]
    assert loaded["credential_id"] == cred["credential_id"]


def test_write_sets_0600_on_posix(isolated_powerloom_home) -> None:
    if sys.platform == "win32":
        pytest.skip("0600 is a POSIX permission concept")
    config_module.write_machine_credential(_valid_credential())
    mode = os.stat(config_module.auth_file()).st_mode & 0o777
    assert mode == 0o600


def test_read_returns_none_for_expired(isolated_powerloom_home) -> None:
    expired = _valid_credential(
        expires_at=(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
    )
    config_module.write_machine_credential(expired)
    assert config_module.read_machine_credential() is None


def test_read_returns_none_for_missing_token_field(
    isolated_powerloom_home,
) -> None:
    config_module.auth_file().write_text(
        json.dumps({"credential_id": "abc"}), encoding="utf-8"
    )
    assert config_module.read_machine_credential() is None


def test_read_returns_none_for_malformed_json(isolated_powerloom_home) -> None:
    config_module.auth_file().write_text("not json {{", encoding="utf-8")
    assert config_module.read_machine_credential() is None


def test_clear_removes_file(isolated_powerloom_home) -> None:
    config_module.write_machine_credential(_valid_credential())
    assert config_module.auth_file().exists()
    config_module.clear_machine_credential()
    assert not config_module.auth_file().exists()


def test_clear_idempotent_on_missing_file(isolated_powerloom_home) -> None:
    # No file written — should not raise.
    config_module.clear_machine_credential()


def test_read_tolerates_z_suffix_in_expires_at(isolated_powerloom_home) -> None:
    """Engine emits ISO with trailing 'Z'; tolerate it via fromisoformat."""
    cred = _valid_credential(
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=90)
        )
        .isoformat()
        .replace("+00:00", "Z")
    )
    config_module.write_machine_credential(cred)
    assert config_module.read_machine_credential() is not None


# ---------------------------------------------------------------------------
# resolution order in _read_credentials_file
# ---------------------------------------------------------------------------


def test_resolution_env_var_beats_machine_credential(
    isolated_powerloom_home, monkeypatch
) -> None:
    monkeypatch.setenv("POWERLOOM_ACCESS_TOKEN", "env_token_xyz")
    config_module.write_machine_credential(_valid_credential())
    assert config_module._read_credentials_file() == "env_token_xyz"


def test_resolution_machine_credential_beats_pat_file(
    isolated_powerloom_home,
) -> None:
    config_module.write_machine_credential(_valid_credential())
    config_module.credentials_file().write_text("pat_legacy", encoding="utf-8")
    token = config_module._read_credentials_file()
    assert token is not None
    assert token.startswith("mcred_")


def test_resolution_falls_through_to_pat_when_no_machine_credential(
    isolated_powerloom_home,
) -> None:
    config_module.credentials_file().write_text("pat_legacy", encoding="utf-8")
    assert config_module._read_credentials_file() == "pat_legacy"


def test_resolution_returns_none_when_no_creds(isolated_powerloom_home) -> None:
    assert config_module._read_credentials_file() is None


def test_expired_machine_credential_falls_through_to_pat(
    isolated_powerloom_home,
) -> None:
    """Expired mcred → resolver returns the PAT instead, not 'mcred_...'."""
    expired = _valid_credential(
        expires_at=(
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
    )
    config_module.write_machine_credential(expired)
    config_module.credentials_file().write_text("pat_legacy", encoding="utf-8")
    assert config_module._read_credentials_file() == "pat_legacy"


# ---------------------------------------------------------------------------
# auth.load_machine_credential / credential_origin
# ---------------------------------------------------------------------------


def test_load_machine_credential_returns_token(isolated_powerloom_home) -> None:
    cred = _valid_credential()
    config_module.write_machine_credential(cred)
    assert auth_api.load_machine_credential() == cred["token"]


def test_load_machine_credential_returns_none_when_expired(
    isolated_powerloom_home,
) -> None:
    config_module.write_machine_credential(
        _valid_credential(
            expires_at=(
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
        )
    )
    assert auth_api.load_machine_credential() is None


def test_credential_origin_none_when_no_creds(isolated_powerloom_home) -> None:
    info = auth_api.credential_origin()
    assert info["origin"] == auth_api.CREDENTIAL_ORIGIN_NONE


def test_credential_origin_machine_credential(isolated_powerloom_home) -> None:
    cred = _valid_credential()
    config_module.write_machine_credential(cred)
    info = auth_api.credential_origin()
    assert info["origin"] == auth_api.CREDENTIAL_ORIGIN_MACHINE
    assert info["credential_id"] == cred["credential_id"]
    assert info["token_prefix"] == cred["token"][:12]
    assert info["name"] == "test laptop"


def test_credential_origin_env_var_takes_precedence(
    isolated_powerloom_home, monkeypatch
) -> None:
    monkeypatch.setenv("POWERLOOM_ACCESS_TOKEN", "env_token_xyz")
    config_module.write_machine_credential(_valid_credential())
    info = auth_api.credential_origin()
    assert info["origin"] == auth_api.CREDENTIAL_ORIGIN_ENV_VAR


def test_credential_origin_pat_when_only_pat(isolated_powerloom_home) -> None:
    config_module.credentials_file().write_text("pat_legacy", encoding="utf-8")
    info = auth_api.credential_origin()
    assert info["origin"] == auth_api.CREDENTIAL_ORIGIN_PAT


# ---------------------------------------------------------------------------
# compute_machine_fingerprint
# ---------------------------------------------------------------------------


def test_machine_fingerprint_is_stable_across_calls() -> None:
    a = auth_api.compute_machine_fingerprint()
    b = auth_api.compute_machine_fingerprint()
    assert a == b
    assert len(a) == 64  # SHA-256 hex
    assert all(c in "0123456789abcdef" for c in a)


# ---------------------------------------------------------------------------
# clear_all_credentials
# ---------------------------------------------------------------------------


def test_clear_all_removes_both_files(isolated_powerloom_home) -> None:
    config_module.write_machine_credential(_valid_credential())
    config_module.credentials_file().write_text("pat_legacy", encoding="utf-8")
    auth_api.clear_all_credentials()
    assert not config_module.auth_file().exists()
    assert not config_module.credentials_file().exists()
