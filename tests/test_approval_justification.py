"""Tests for the --justification flag + POWERLOOM_APPROVAL_JUSTIFICATION
env var support (v0.5.3).

Covers:
  - Config picks up env var on load
  - Client injects X-Approval-Justification header when config has it
  - CLI --justification flag sets the env var + flows through to client
  - Absence of either means no header is sent (backwards compat)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomClient
from loomcli.config import RuntimeConfig, load_runtime_config


runner = CliRunner()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_reads_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.setenv("POWERLOOM_APPROVAL_JUSTIFICATION", "deploying fleet")
    cfg = load_runtime_config()
    assert cfg.approval_justification == "deploying fleet"


def test_config_no_env_var_leaves_none(monkeypatch, tmp_path):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.delenv("POWERLOOM_APPROVAL_JUSTIFICATION", raising=False)
    cfg = load_runtime_config()
    assert cfg.approval_justification is None


def test_config_empty_string_becomes_none(monkeypatch, tmp_path):
    """An empty env var shouldn't trigger the header — treat as unset."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.setenv("POWERLOOM_APPROVAL_JUSTIFICATION", "")
    cfg = load_runtime_config()
    assert cfg.approval_justification is None


# ---------------------------------------------------------------------------
# Client header injection
# ---------------------------------------------------------------------------


def test_client_sends_header_when_set():
    cfg = RuntimeConfig(
        api_base_url="https://test.example.com",
        access_token="tok",
        approval_justification="because the policy requires it",
    )
    client = PowerloomClient(cfg)
    headers = client._http.headers
    assert headers.get("X-Approval-Justification") == "because the policy requires it"
    assert headers.get("Authorization") == "Bearer tok"
    client.close()


def test_client_omits_header_when_unset():
    cfg = RuntimeConfig(
        api_base_url="https://test.example.com",
        access_token="tok",
        approval_justification=None,
    )
    client = PowerloomClient(cfg)
    headers = client._http.headers
    assert "X-Approval-Justification" not in headers
    client.close()


# ---------------------------------------------------------------------------
# CLI --justification flag
# ---------------------------------------------------------------------------


def test_cli_justification_flag_flows_to_env(monkeypatch, tmp_path):
    """The root callback sets the env var from the flag before
    subcommands run — so load_runtime_config() picks it up."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.delenv("POWERLOOM_APPROVAL_JUSTIFICATION", raising=False)

    # Use `weave login --pat` which goes through the flag machinery
    # without needing a live API. We patch the client so no real call
    # is made; we just observe what the config has when the client
    # gets instantiated.
    observed = {}

    def fake_client_init(self, cfg):
        observed["justification"] = cfg.approval_justification
        # Minimal stub so context manager + .get("/me") succeed.
        self._cfg = cfg
        import httpx
        self._http = httpx.Client(base_url=cfg.api_base_url)

    with patch.object(PowerloomClient, "__init__", fake_client_init), patch.object(
        PowerloomClient, "get", return_value={"id": "u", "email": "e@e.com", "organization_id": "o"}
    ):
        result = runner.invoke(
            app,
            ["--justification", "bootstrap fleet", "login", "--pat", "t"],
        )

    assert result.exit_code == 0, result.stdout
    assert observed.get("justification") == "bootstrap fleet"


def test_cli_no_flag_no_header(monkeypatch, tmp_path):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.delenv("POWERLOOM_APPROVAL_JUSTIFICATION", raising=False)

    observed = {}

    def fake_client_init(self, cfg):
        observed["justification"] = cfg.approval_justification
        self._cfg = cfg
        import httpx
        self._http = httpx.Client(base_url=cfg.api_base_url)

    with patch.object(PowerloomClient, "__init__", fake_client_init), patch.object(
        PowerloomClient, "get", return_value={"id": "u", "email": "e@e.com", "organization_id": "o"}
    ):
        result = runner.invoke(
            app, ["login", "--pat", "t"]
        )
    assert result.exit_code == 0, result.stdout
    assert observed.get("justification") is None


def test_version_flag_still_works(monkeypatch):
    """Adding a new global option must not break existing ones."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_justification_flag_in_root_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "justification" in result.stdout.lower()
    assert "approval-gated" in result.stdout.lower() or "X-Approval-Justification".lower() in result.stdout.lower()
