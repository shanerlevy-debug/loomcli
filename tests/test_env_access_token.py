"""Tests for ``POWERLOOM_ACCESS_TOKEN`` env-var resolution in
``loomcli.config._read_credentials_file``.

Containerized + systemd deployments (the EC2 reconciler box, customer
self-hosted setups, CI workers) inject the PAT via env var rather than
writing a credentials file into a config dir. This test pins the
resolution order:

  1. ``POWERLOOM_ACCESS_TOKEN`` (env var) wins if set.
  2. ``<POWERLOOM_HOME>/credentials`` file is the legacy fallback.
  3. Returns ``None`` when neither is present.
"""
from __future__ import annotations

import pytest

from loomcli import config as cfg


def test_env_var_wins_over_file(monkeypatch, tmp_path):
    """ENV var set + file present → env var value returned (file is
    ignored, no merge). Symmetric with how 12-factor apps usually treat
    secrets."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    (tmp_path / "credentials").write_text("pat_from_file", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_ACCESS_TOKEN", "pat_from_env")

    assert cfg._read_credentials_file() == "pat_from_env"


def test_env_var_alone_returns_value(monkeypatch, tmp_path):
    """ENV var set + no file → env var value returned. The container
    case: no bind-mounted config dir, just the env var."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))  # empty dir
    monkeypatch.setenv("POWERLOOM_ACCESS_TOKEN", "pat_only_env")

    assert cfg._read_credentials_file() == "pat_only_env"


def test_file_fallback_when_env_absent(monkeypatch, tmp_path):
    """ENV var unset + file present → file value returned. The TTY-host
    case: operator ran ``weave login``, file wrote, no container env."""
    monkeypatch.delenv("POWERLOOM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    (tmp_path / "credentials").write_text("pat_from_file_only", encoding="utf-8")

    assert cfg._read_credentials_file() == "pat_from_file_only"


def test_blank_env_var_falls_through_to_file(monkeypatch, tmp_path):
    """ENV var set to empty string → falls through to file lookup. Catches
    the common Docker pitfall of an unset variable that gets exported as
    an empty string by docker-compose's variable interpolation."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    (tmp_path / "credentials").write_text("pat_from_file", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_ACCESS_TOKEN", "   ")  # whitespace-only

    assert cfg._read_credentials_file() == "pat_from_file"


def test_neither_returns_none(monkeypatch, tmp_path):
    """Neither set → None. The "Not signed in" case the CLI surfaces
    via _require_config()."""
    monkeypatch.delenv("POWERLOOM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))  # empty dir

    assert cfg._read_credentials_file() is None


def test_env_var_value_is_stripped(monkeypatch, tmp_path):
    """Trailing whitespace / newlines (from `cat token | docker run -e`
    style invocations) get stripped. Defense against the classic
    "I copy-pasted the token and a newline came along" footgun."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.setenv(
        "POWERLOOM_ACCESS_TOKEN", "pat_with_trailing_newline\n"
    )

    assert cfg._read_credentials_file() == "pat_with_trailing_newline"
