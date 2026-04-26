"""Milestone 0 (v0.5.1) — auth CLI tests.

Covers:
  - Top-level aliases (weave login, weave logout, weave whoami) route
    to the same functions as weave auth <cmd>.
  - --pat injection writes credentials + verifies via /me.
  - Browser-paste flow prompts for input + writes credentials.
  - Dev-mode impersonation preserved.
  - PAT management commands (create/list/revoke) hit the right paths.
  - Verification failure clears credentials.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomApiError

runner = CliRunner()


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Isolate POWERLOOM_HOME so tests don't touch user's real credentials."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", "https://api.test.example.com")
    yield tmp_path


# ---------------------------------------------------------------------------
# Top-level aliases
# ---------------------------------------------------------------------------


def test_login_alias_appears_in_root_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "logout" in result.stdout
    assert "whoami" in result.stdout


def test_weave_auth_login_help_exists():
    """Original weave auth login path still works."""
    result = runner.invoke(app, ["auth", "login", "--help"])
    assert result.exit_code == 0
    assert "--pat" in result.stdout
    assert "--dev-as" in result.stdout
    assert "--no-browser" in result.stdout


def test_weave_login_help_matches_auth_login_help():
    """Top-level alias should list the same flags as the auth subcommand."""
    aliased = runner.invoke(app, ["login", "--help"])
    nested = runner.invoke(app, ["auth", "login", "--help"])
    assert aliased.exit_code == 0
    assert nested.exit_code == 0
    assert "--pat" in aliased.stdout
    assert "--dev-as" in aliased.stdout
    assert "--no-browser" in aliased.stdout


# ---------------------------------------------------------------------------
# --pat injection
# ---------------------------------------------------------------------------


def test_login_pat_writes_credentials_on_success(isolated_home):
    """--pat verifies via /me and persists the token."""
    fake_me = {
        "id": "user-123",
        "email": "test@example.com",
        "organization_id": "org-abc",
    }
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = fake_me
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["login", "--pat", "test-token-xyz"])

    assert result.exit_code == 0, result.stdout
    assert "test@example.com" in result.stdout
    # Verify credentials file exists + contains the token
    creds = isolated_home / "credentials"
    assert creds.exists()
    assert creds.read_text(encoding="utf-8").strip() == "test-token-xyz"


def test_login_pat_clears_creds_on_verification_failure(isolated_home):
    """If /me fails, credentials must be cleared — no half-state."""
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.side_effect = PowerloomApiError(
            401, "invalid token", method="GET", path="/me"
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["login", "--pat", "bad-token"])

    assert result.exit_code != 0
    assert "failed" in result.stdout.lower() or "invalid" in result.stdout.lower()
    creds = isolated_home / "credentials"
    assert not creds.exists(), "credentials should be cleared on failure"


def test_login_rejects_empty_pat(isolated_home):
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        # Empty string is falsy in Typer's view, so the flag resolves to
        # None and the browser flow kicks in. Test that the explicit
        # whitespace-only value is rejected by login_pat instead.
        from loomcli.auth import login_pat
        from loomcli.config import load_runtime_config

        with pytest.raises(PowerloomApiError) as excinfo:
            login_pat(load_runtime_config(), "   ")
        assert "empty" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Browser-paste flow
# ---------------------------------------------------------------------------


def test_login_browser_flow_prompts_and_persists(isolated_home):
    """Default login opens browser (suppressed in test) + accepts pasted token."""
    fake_me = {
        "id": "user-123",
        "email": "test@example.com",
        "organization_id": "org-abc",
    }
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls, patch(
        "loomcli.auth.webbrowser.open"
    ) as mock_browser:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = fake_me
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["login", "--no-browser"],
            input="pasted-token-abc\n",
        )

    assert result.exit_code == 0, result.stdout
    assert "test@example.com" in result.stdout
    # --no-browser suppressed the actual webbrowser call
    mock_browser.assert_not_called()
    # But the PAT mint URL should still be printed
    assert "access-tokens" in result.stdout
    # And credentials were written
    creds = isolated_home / "credentials"
    assert creds.exists()
    assert creds.read_text(encoding="utf-8").strip() == "pasted-token-abc"


def test_login_browser_flow_localhost_warning(isolated_home, monkeypatch):
    """Localhost API URL surfaces a --dev-as suggestion."""
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", "http://localhost:8000")
    fake_me = {
        "id": "u",
        "email": "e@e.com",
        "organization_id": "o",
    }
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = fake_me
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["login", "--no-browser"],
            input="some-token\n",
        )

    # Even though it warns, it still proceeds if the user has a token.
    assert "localhost" in result.stdout.lower()
    assert "dev-as" in result.stdout.lower()


def test_login_web_url_env_override(isolated_home, monkeypatch):
    """POWERLOOM_WEB_URL env var overrides the default web URL."""
    monkeypatch.setenv("POWERLOOM_WEB_URL", "https://staging.powerloom.org")
    fake_me = {"id": "u", "email": "e@e.com", "organization_id": "o"}
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls, patch(
        "loomcli.auth.webbrowser.open"
    ):
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = fake_me
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            ["login", "--no-browser"],
            input="t\n",
        )
    assert "staging.powerloom.org/settings/access-tokens" in result.stdout


# ---------------------------------------------------------------------------
# Dev-mode impersonation preserved
# ---------------------------------------------------------------------------


def test_login_dev_as_preserved(isolated_home):
    """--dev-as still works through the new unified login entry point."""
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.dev_login.return_value = "dev-token-from-server"
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app, ["login", "--dev-as", "admin@dev.local"]
        )

    assert result.exit_code == 0, result.stdout
    assert "admin@dev.local" in result.stdout
    creds = isolated_home / "credentials"
    assert creds.exists()
    assert creds.read_text(encoding="utf-8").strip() == "dev-token-from-server"


def test_login_dev_as_via_auth_subgroup_preserved(isolated_home):
    """`weave auth login --dev-as` path still works (backward compat)."""
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.dev_login.return_value = "dev-token-2"
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app, ["auth", "login", "--dev-as", "admin@dev.local"]
        )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Logout + whoami aliases
# ---------------------------------------------------------------------------


def test_logout_alias_clears_credentials(isolated_home):
    (isolated_home / "credentials").write_text("some-token", encoding="utf-8")
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0
    assert not (isolated_home / "credentials").exists()


def test_whoami_alias_when_signed_out(isolated_home):
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 1
    assert "not signed in" in result.stdout.lower()


def test_whoami_alias_when_signed_in(isolated_home):
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = {
            "id": "u-1",
            "email": "me@test.com",
            "organization_id": "o-1",
        }
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0, result.stdout
    assert "me@test.com" in result.stdout
    assert "u-1" in result.stdout


# ---------------------------------------------------------------------------
# PAT management — create / list / revoke
# ---------------------------------------------------------------------------


def test_pat_create_requires_signin(isolated_home):
    result = runner.invoke(
        app, ["auth", "pat", "create", "--name", "my-pat"]
    )
    assert result.exit_code == 1
    assert "sign in" in result.stdout.lower() or "login" in result.stdout.lower()


def test_pat_create_shows_raw_token_once(isolated_home):
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    pat_id = str(uuid.uuid4())
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = {
            "id": pat_id,
            "name": "my-laptop",
            "token_prefix": "pk_live_abc",
            "raw_token": "pk_live_abcdefghijklmnop_SECRET",
            "created_at": "2026-04-24T00:00:00Z",
            "last_used_at": None,
            "expires_at": None,
            "revoked_at": None,
        }
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app, ["auth", "pat", "create", "--name", "my-laptop"]
        )

    assert result.exit_code == 0, result.stdout
    assert "pk_live_abcdefghijklmnop_SECRET" in result.stdout
    assert "shown once" in result.stdout.lower()
    assert pat_id in result.stdout
    # Verify the API was called with the expected body
    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "/users/me/personal-access-tokens"
    assert args[1]["name"] == "my-laptop"


def test_pat_create_with_expires_at(isolated_home):
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = {
            "id": str(uuid.uuid4()),
            "name": "ci",
            "token_prefix": "pk_live_x",
            "raw_token": "pk_live_xsecret",
            "expires_at": "2027-01-01T00:00:00Z",
            "created_at": "2026-04-24T00:00:00Z",
            "last_used_at": None,
            "revoked_at": None,
        }
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "auth",
                "pat",
                "create",
                "--name",
                "ci",
                "--expires-at",
                "2027-01-01T00:00:00Z",
            ],
        )
    assert result.exit_code == 0
    args, _ = mock_client.post.call_args
    assert args[1]["expires_at"] == "2027-01-01T00:00:00Z"


def test_pat_list_empty(isolated_home):
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = []
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["auth", "pat", "list"])
    assert result.exit_code == 0, result.stdout
    assert "no pats" in result.stdout.lower()


def test_pat_list_renders_table(isolated_home, monkeypatch):
    """Rich auto-shrinks columns to fit terminal width. Give it room."""
    monkeypatch.setenv("COLUMNS", "200")
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    items = [
        {
            "id": "pat-uuid-1",
            "name": "my-laptop",
            "token_prefix": "pk_live_1",
            "created_at": "2026-04-20T12:00:00Z",
            "last_used_at": "2026-04-24T08:00:00Z",
            "expires_at": None,
            "revoked_at": None,
        },
        {
            "id": "pat-uuid-2",
            "name": "ci-prod",
            "token_prefix": "pk_live_2",
            "created_at": "2026-04-21T12:00:00Z",
            "last_used_at": None,
            "expires_at": "2027-04-21T12:00:00Z",
            "revoked_at": None,
        },
    ]
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = items
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app, ["auth", "pat", "list"], env={"COLUMNS": "200"}
        )
    assert result.exit_code == 0, result.stdout
    # The API was called with the expected path.
    mock_client.get.assert_called_once_with("/users/me/personal-access-tokens")
    # Content checks — at least one row's distinctive data appears.
    # (Rich may still truncate even at width=200 depending on the runner's
    # terminal detection; the API-call assertion is the load-bearing check.)
    combined = result.stdout
    assert "my-laptop" in combined or "ci-prod" in combined or "pk_live" in combined, (
        f"expected some PAT data in output, got: {combined!r}"
    )


def test_pat_revoke(isolated_home):
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    with patch("loomcli.auth.PowerloomClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.delete.return_value = None
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app, ["auth", "pat", "revoke", "pat-uuid-to-revoke"]
        )
    assert result.exit_code == 0, result.stdout
    mock_client.delete.assert_called_once_with(
        "/users/me/personal-access-tokens/pat-uuid-to-revoke"
    )


# ---------------------------------------------------------------------------
# --oidc still stubbed
# ---------------------------------------------------------------------------


def test_login_oidc_still_stubbed(isolated_home):
    result = runner.invoke(app, ["login", "--oidc"])
    assert result.exit_code == 1
    # Message should mention v056 as the target release.
    assert "v056" in result.stdout.lower() or "device-code" in result.stdout.lower()


# ---------------------------------------------------------------------------
# M1 — weave auth mcp-url + weave auth token (weave-claude-code-setup)
# ---------------------------------------------------------------------------

FAKE_ME_WITH_PROXY = {
    "id": "user-abc",
    "email": "dev@example.com",
    "organization_id": "org-xyz",
    "mcp_proxy_id": "cf2b72ba-af17-4e71-b10a-86a8cae45e49",
}


def test_auth_mcp_url_prints_full_url(isolated_home):
    """`weave auth mcp-url` prints the complete MCP proxy URL."""
    (isolated_home / "credentials").write_text("test-token", encoding="utf-8")
    with patch("loomcli.auth.PowerloomClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = FAKE_ME_WITH_PROXY
        mock_cls.return_value = mock_client

        result = runner.invoke(app, ["auth", "mcp-url"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        "https://cf2b72ba-af17-4e71-b10a-86a8cae45e49.mcp.powerloom.org/mcp"
    )


def test_auth_mcp_url_not_signed_in(isolated_home):
    """`weave auth mcp-url` exits non-zero when not authenticated."""
    result = runner.invoke(app, ["auth", "mcp-url"])
    assert result.exit_code != 0
    assert "signed in" in result.output.lower() or "login" in result.output.lower()


def test_auth_mcp_url_missing_proxy_id(isolated_home):
    """`weave auth mcp-url` exits non-zero when /me has no mcp_proxy_id."""
    (isolated_home / "credentials").write_text("test-token", encoding="utf-8")
    me_without_proxy = {k: v for k, v in FAKE_ME_WITH_PROXY.items() if k != "mcp_proxy_id"}
    with patch("loomcli.auth.PowerloomClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = me_without_proxy
        mock_cls.return_value = mock_client

        result = runner.invoke(app, ["auth", "mcp-url"])

    assert result.exit_code != 0
    assert "proxy" in result.output.lower() or "support" in result.output.lower()


def test_auth_token_prints_pat(isolated_home):
    """`weave auth token` prints the stored PAT to stdout."""
    (isolated_home / "credentials").write_text("pat_abc123", encoding="utf-8")
    result = runner.invoke(app, ["auth", "token"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "pat_abc123"


def test_auth_token_not_signed_in(isolated_home):
    """`weave auth token` exits non-zero when credentials are absent."""
    result = runner.invoke(app, ["auth", "token"])
    assert result.exit_code != 0
    assert "signed in" in result.output.lower() or "login" in result.output.lower()
