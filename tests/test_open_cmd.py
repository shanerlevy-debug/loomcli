"""Tests for ``weave open <token>`` (sprint cli-weave-open-20260430,
thread c78ead6d — skeleton + redeem call)."""
from __future__ import annotations

import json
import re
import uuid
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomApiError


runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_spec(**overrides) -> dict:
    """Minimal valid LaunchSpec response dict.

    Mirrors what ``GET /launches/{token}`` returns from the engine. Tests
    that need specific values pass overrides; everything else is
    deterministic and consistent (same UUIDs etc.) so output assertions
    can match exact strings if they need to.
    """
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
            "repo_url": "https://github.com/shanerlevy-debug/Powerloom.git",
            "default_branch": "main",
        },
        "scope": {
            "slug": "cc-test-20260501",
            "friendly_name": None,
            "branch_base": "main",
            "branch_name": "session/cc-test-20260501",
        },
        "runtime": "claude_code",
        "skills": [{"slug": "init", "version": "1.0.0"}],
        "capabilities": ["python", "api"],
        "clone_auth": {
            "mode": "server_minted",
            "token": None,
            "expires_at": None,
            "hint": None,
        },
        "mcp_config": {"servers": []},
        "rules_sync": [
            {
                "scope": "bespoke-technology.powerloom",
                "runtimes": ["claude_code", "codex_cli", "gemini_cli"],
            }
        ],
        "session_attach_token": "sat_aaaaaaaaaaaaaaaaaaaaaa",
        "thread_id": None,
    }
    base.update(overrides)
    return base


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


@pytest.fixture
def mock_client():
    """Patch PowerloomClient where open_cmd imports it."""
    with patch("loomcli.commands.open_cmd.PowerloomClient") as mock_cls:
        with patch(
            "loomcli.commands.open_cmd.load_runtime_config",
            return_value=MagicMock(),
        ):
            client = MagicMock()
            client.__enter__.return_value = client
            mock_cls.return_value = client
            yield client


@pytest.fixture
def mock_bootstrap(tmp_path):
    """Patch subprocess + paths so non-dry-run tests don't run real git.

    Returns the tmp-rooted ``WeaveOpenPaths`` so tests can assert on
    expected worktree locations without monkeypatching ``Path.home``.
    """
    import subprocess as sp

    from loomcli._open.git_ops import WeaveOpenPaths

    paths = WeaveOpenPaths(
        repos_root=tmp_path / "repos",
        worktrees_root=tmp_path / "worktrees",
    )

    def _ok(cmd, **kw):
        return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=_ok):
        with patch("loomcli._open.git_ops.shutil.which", return_value="/usr/bin/git"):
            with patch.object(WeaveOpenPaths, "default", return_value=paths):
                yield paths


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_open_subcommand_registered() -> None:
    result = runner.invoke(app, ["open", "--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    for flag in ("--dry-run", "--reuse", "--resume", "--root"):
        assert flag in out, f"flag {flag!r} not in --help output"


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_redeem_happy_path(mock_client, mock_bootstrap) -> None:
    mock_client.get.return_value = _seed_spec()
    result = runner.invoke(app, ["open", "lt_aaaaaaaaaaaaaaaaaaaaaa"])
    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    # Preview includes scope + runtime + branch + project repo_url
    assert "powerloom" in out
    assert "session/cc-test-20260501" in out
    assert "claude_code" in out
    assert "init" in out  # skill name
    # Bootstrap output (864c55a4) — repo + worktree lines emitted.
    assert "Repo:" in out
    assert "Worktree:" in out
    # Worktree path uses scope-slug + first-4 of launch_id.hex.
    # _seed_spec() uses launch_id="11111111-..." → short_id="1111".
    assert "cc-test-20260501-1111" in out
    # Future-thread TODO marker still present (skill / MCP / register / exec).
    assert "TODO" in out
    # Redeem URL hit with the path token
    mock_client.get.assert_called_once_with(
        "/launches/lt_aaaaaaaaaaaaaaaaaaaaaa"
    )


def test_redeem_dry_run_exits_after_preview(mock_client) -> None:
    mock_client.get.return_value = _seed_spec()
    result = runner.invoke(
        app, ["open", "lt_aaaaaaaaaaaaaaaaaaaaaa", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "powerloom" in out
    # Dry-run skips the TODO-future-thread marker and emits its own banner.
    assert "TODO" not in out
    assert "--dry-run" in out


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def _api_error(status: int) -> PowerloomApiError:
    """Construct PowerloomApiError matching the real client's signature."""
    # PowerloomApiError(status_code, message, body=None) — see client.py.
    return PowerloomApiError(status, f"HTTP {status}")


def test_redeem_404_prints_actionable_message(mock_client) -> None:
    mock_client.get.side_effect = _api_error(404)
    result = runner.invoke(app, ["open", "lt_dead00000000000000000"])
    assert result.exit_code == 1
    err = _strip_ansi(result.output)
    assert "not found" in err.lower() or "expired" in err.lower()
    assert "powerloom.org" in err  # launches URL pointed at


def test_redeem_410_prints_actionable_message(mock_client) -> None:
    mock_client.get.side_effect = _api_error(410)
    result = runner.invoke(app, ["open", "lt_used0000000000000000"])
    assert result.exit_code == 1
    err = _strip_ansi(result.output)
    assert "redeemed" in err.lower()
    assert "powerloom.org" in err


def test_redeem_401_prints_actionable_message(mock_client) -> None:
    mock_client.get.side_effect = _api_error(401)
    result = runner.invoke(app, ["open", "lt_othr0000000000000000"])
    assert result.exit_code == 1
    err = _strip_ansi(result.output)
    assert "authoris" in err.lower() or "authoriz" in err.lower()


def test_redeem_unexpected_error_surfaces_with_message(mock_client) -> None:
    mock_client.get.side_effect = _api_error(503)
    result = runner.invoke(app, ["open", "lt_rrrr0000000000000000"])
    assert result.exit_code == 1
    err = _strip_ansi(result.output)
    assert "redeem failed" in err.lower()


# ---------------------------------------------------------------------------
# forward-compat — extra fields in response are tolerated
# ---------------------------------------------------------------------------


def test_unknown_fields_in_response_are_ignored(
    mock_client, mock_bootstrap
) -> None:
    """LaunchSpec uses extra='ignore' so a newer engine doesn't break old CLIs."""
    spec = _seed_spec()
    spec["future_field_engine_added_later"] = "value-the-cli-doesnt-know-about"
    spec["actor"]["future_actor_field"] = "more-unknown"
    mock_client.get.return_value = spec
    result = runner.invoke(app, ["open", "lt_fwd00000000000000000"])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# unused-flag stubs
# ---------------------------------------------------------------------------


def test_future_flags_are_accepted_and_logged(
    mock_client, mock_bootstrap, tmp_path
) -> None:
    mock_client.get.return_value = _seed_spec()
    result = runner.invoke(
        app,
        [
            "open",
            "lt_aaaaaaaaaaaaaaaaaaaaaa",
            "--reuse",
            "cc-test-20260501",
            "--resume",
            str(uuid.uuid4()),
            "--root",
            str(tmp_path / "alt_worktrees"),
        ],
    )
    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    # --root is wired (worktree lands under the override path); --reuse +
    # --resume are still future-thread stubs and surface in the TODO blurb.
    assert "--reuse" in out
    assert "--resume" in out
    # --root is not in the unused-flags blurb because it IS wired.
    assert "alt_worktrees" in out
