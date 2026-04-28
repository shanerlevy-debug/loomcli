"""Shared fixtures for CLI tests.

No control plane needed — we intercept the HTTP layer with respx so
tests run in isolation from the API container.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point POWERLOOM_HOME at a scratch dir so tests don't read/write
    the real user's credentials."""
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path / "powerloom-home"))
    # A fake token so auth-gated commands don't bail with "not signed in".
    creds_dir = tmp_path / "powerloom-home"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / "credentials").write_text("test-token")
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", "http://api.test")
    # Clear agent-mode markers so tests default to human/table output
    # (prevents auto-JSON switch when tests are run from a GEMINI_CLI
    # or Claude session).
    # v0.7.7 token-efficiency: force no-auto-json for the test suite.
    monkeypatch.setenv("POWERLOOM_NO_AUTO_JSON", "1")
    for var in (
        "GEMINI_CLI",
        "CLAUDE_CODE",
        "AGENT_MODE",
        "POWERLOOM_ACTIVE_SUBPRINCIPAL_ID",
        "POWERLOOM_FORMAT",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_ou_tree() -> list[dict]:
    """Realistic-ish OU tree matching what the seed script produces."""
    return [
        {
            "id": "00000000-0000-0000-0000-00000000dddd",
            "name": "dev-org",
            "display_name": "Dev Org",
            "parent_id": None,
            "children": [
                {
                    "id": "00000000-0000-0000-0000-0000000000aa",
                    "name": "engineering",
                    "display_name": "Engineering",
                    "parent_id": "00000000-0000-0000-0000-00000000dddd",
                    "children": [],
                },
                {
                    "id": "00000000-0000-0000-0000-0000000000bb",
                    "name": "accounting",
                    "display_name": "Accounting",
                    "parent_id": "00000000-0000-0000-0000-00000000dddd",
                    "children": [],
                },
            ],
        },
    ]
