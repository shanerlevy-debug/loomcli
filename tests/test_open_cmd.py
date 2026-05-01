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
    """Patch PowerloomClient where open_cmd imports it.

    Defaults ``.post('/agent-sessions', ...)`` to a sensible registered-
    session payload so happy-path tests don't need to wire it; tests
    that exercise the register-failure path can override
    ``client.post.side_effect``.
    """
    with patch("loomcli.commands.open_cmd.PowerloomClient") as mock_cls:
        with patch(
            "loomcli.commands.open_cmd.load_runtime_config",
            return_value=MagicMock(),
        ):
            client = MagicMock()
            client.__enter__.return_value = client
            client.post.return_value = {
                "session": {
                    "id": "ab123456-0000-0000-0000-000000000000",
                    "session_slug": "cc-test-20260501",
                },
                "work_chain_event_hash": "deadbeef",
                "overlap_warnings": [],
            }
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

    def _fake_run(cmd, **kw):
        # Mimic git's filesystem side-effects so downstream steps
        # (env-file write into the worktree) find the directory.
        if len(cmd) >= 4 and cmd[:3] == ["git", "worktree", "add"]:
            worktree_target = __import__("pathlib").Path(cmd[3])
            worktree_target.mkdir(parents=True, exist_ok=True)
        return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    def _exec_aborts(file, args, env):
        # Real exec replaces the process; in tests we abort cleanly so
        # `runner.invoke` records a normal exit.
        raise SystemExit(0)

    def _noop_sync(**kwargs):
        # rules_sync.apply_directives() defaults to the real conventions
        # sync command which would hit the engine. Stub it for tests.
        return None

    # Auth bootstrap (sprint auth-bootstrap-20260430) — by default the
    # open flow calls maybe_bootstrap_machine_credential after redeem,
    # which would otherwise hit the real engine. Stub it to a "skipped:
    # already have credential" no-op so happy-path tests stay focused
    # on the bootstrap pipeline. Tests that exercise auth-bootstrap
    # specifically (test_open_auth_bootstrap.py) call the helper
    # directly instead of going through `weave open`.
    from loomcli._open.auth_bootstrap import BootstrapResult as _BR

    def _stub_bootstrap(*args, **kwargs):
        return _BR(minted=False, skipped_reason="already_have_machine_credential")

    with patch(
        "loomcli.commands.open_cmd.maybe_bootstrap_machine_credential",
        side_effect=_stub_bootstrap,
    ), patch(
        "loomcli.commands.conventions_cmd.sync",
        side_effect=_noop_sync,
    ):
        with patch(
            "loomcli._open.git_ops.subprocess.run", side_effect=_fake_run
        ):
            with patch(
                "loomcli._open.git_ops.shutil.which",
                return_value="/usr/bin/git",
            ):
                with patch(
                    "loomcli._open.runtime_exec.shutil.which",
                    return_value="/usr/bin/claude",
                ):
                    with patch("loomcli._open.runtime_exec.os.chdir"):
                        with patch(
                            "loomcli._open.runtime_exec.os.execvpe",
                            side_effect=_exec_aborts,
                        ):
                            with patch.object(
                                WeaveOpenPaths,
                                "default",
                                return_value=paths,
                            ):
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
    # Rules sync output (53fddf29) — one directive line per scope.
    assert "Rules synced from" in out
    assert "bespoke-technology.powerloom" in out
    # Session register output (5fab82ed) — registered + env file emitted.
    assert "Session registered" in out
    assert "ab123456" in out
    assert ".powerloom-session.env" in out
    # Runtime hand-off output (53573d73) — Launching banner before exec.
    assert "Launching claude_code" in out
    # TODO marker now points at next-sprint work (skills + MCP).
    assert "TODO" in out
    # Redeem URL hit with the path token
    mock_client.get.assert_any_call(
        "/launches/lt_aaaaaaaaaaaaaaaaaaaaaa"
    )
    # Register POST'd to /agent-sessions
    register_call = next(
        c for c in mock_client.post.call_args_list
        if c.args and c.args[0] == "/agent-sessions"
    )
    body = register_call.args[1]
    assert body["session_slug"] == "cc-test-20260501"
    assert body["actor_kind"] == "claude_code"


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


def test_runtime_pre_flight_missing_binary_exits_before_clone(
    mock_client, mock_bootstrap
) -> None:
    """Missing runtime binary → fail-fast pre-flight, no clone, no register."""
    mock_client.get.return_value = _seed_spec()
    # Override runtime-exec's shutil.which to simulate a missing binary;
    # mock_bootstrap's default returns a path so we have to override
    # within this test scope.
    with patch("loomcli._open.runtime_exec.shutil.which", return_value=None):
        result = runner.invoke(app, ["open", "lt_norunt00000000000000"])
    assert result.exit_code == 1
    out = _strip_ansi(result.output)
    assert "claude" in out
    assert "PATH" in out
    # Session register POST should NOT have been issued.
    register_calls = [
        c for c in mock_client.post.call_args_list
        if c.args and c.args[0] == "/agent-sessions"
    ]
    assert register_calls == []


def test_antigravity_runtime_returns_after_register_no_exec(
    mock_client, mock_bootstrap
) -> None:
    """Antigravity launch doesn't exec — it instructs the user to start the worker."""
    spec = _seed_spec(runtime="antigravity")
    spec["actor"]["runtime"] = "antigravity"
    mock_client.get.return_value = spec

    # exec must NOT be called for antigravity.
    with patch("loomcli._open.runtime_exec.os.execvpe") as mock_exec:
        result = runner.invoke(app, ["open", "lt_antigrav0000000000000"])

    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "antigravity-worker" in out
    mock_exec.assert_not_called()


def test_resume_finds_worktree_and_execs_runtime(
    mock_client, mock_bootstrap
) -> None:
    """--resume <session-id> short-circuits redeem + clone + register."""
    # Pre-create a worktree with a matching env file under the bootstrap paths.
    target_dir = mock_bootstrap.worktrees_root / "cc-test-20260501-1111"
    target_dir.mkdir(parents=True)
    (target_dir / ".powerloom-session.env").write_text(
        "POWERLOOM_SESSION_ID=resume-sess-id\n"
        "POWERLOOM_RUNTIME=claude_code\n"
        "POWERLOOM_SCOPE=cc-test-20260501\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["open", "--resume", "resume-sess-id"]
    )
    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "Resuming" in out
    assert "claude_code" in out
    # No redeem call.
    mock_client.get.assert_not_called()
    # No register call.
    register_calls = [
        c for c in mock_client.post.call_args_list
        if c.args and c.args[0] == "/agent-sessions"
    ]
    assert register_calls == []


def test_reuse_picks_latest_worktree_for_scope(
    mock_client, mock_bootstrap
) -> None:
    """--reuse <scope> picks the latest matching worktree."""
    import os
    import time

    older = mock_bootstrap.worktrees_root / "cc-test-20260501-aaaa"
    older.mkdir(parents=True)
    (older / ".powerloom-session.env").write_text(
        "POWERLOOM_SESSION_ID=old-sess\n"
        "POWERLOOM_RUNTIME=claude_code\n",
        encoding="utf-8",
    )
    old_t = time.time() - 600
    os.utime(older, (old_t, old_t))

    newer = mock_bootstrap.worktrees_root / "cc-test-20260501-bbbb"
    newer.mkdir(parents=True)
    (newer / ".powerloom-session.env").write_text(
        "POWERLOOM_SESSION_ID=new-sess\n"
        "POWERLOOM_RUNTIME=claude_code\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["open", "--reuse", "cc-test-20260501"])
    assert result.exit_code == 0, result.output
    # Output normalisation: Rich line-wraps long paths with \n at terminal
    # width. Strip whitespace before substring-checking so the path on
    # disk vs. the path in the rendered output compare cleanly.
    out = "".join(_strip_ansi(result.output).split())
    assert str(newer).replace(" ", "") in out
    mock_client.get.assert_not_called()


def test_resume_unknown_session_id_exits_1(mock_client, mock_bootstrap) -> None:
    result = runner.invoke(app, ["open", "--resume", "no-such-session"])
    assert result.exit_code == 1
    out = _strip_ansi(result.output)
    assert "session_id" in out or "no worktree" in out.lower()


def test_resume_with_token_argued_rejects(mock_client, mock_bootstrap) -> None:
    """Pass either a token or --resume, not both."""
    result = runner.invoke(
        app, ["open", "lt_aaaaaaaaaaaaaaaaaaaaaa", "--resume", "x"]
    )
    assert result.exit_code == 2
    assert "not both" in _strip_ansi(result.output).lower()


def test_token_required_without_resume_or_reuse(mock_client) -> None:
    """No token, no --resume, no --reuse — error out before doing anything."""
    result = runner.invoke(app, ["open"])
    assert result.exit_code == 2
    out = _strip_ansi(result.output)
    assert "token" in out.lower() or "resume" in out.lower()


def test_session_register_failure_surfaces_actionable_message(
    mock_client, mock_bootstrap
) -> None:
    """Worktree on disk + register fails → error tells user worktree is intact."""
    mock_client.get.return_value = _seed_spec()
    mock_client.post.side_effect = PowerloomApiError(
        409, "scope already active in another session"
    )
    result = runner.invoke(app, ["open", "lt_regfail00000000000000"])
    assert result.exit_code == 1
    out = _strip_ansi(result.output)
    assert "Session registration failed" in out
    assert "HTTP 409" in out
    # Hint mentions the worktree is still intact + cache-window retry.
    assert "intact" in out.lower() or "5min" in out


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


def test_root_flag_overrides_worktree_root_path(
    mock_client, tmp_path
) -> None:
    """``--root <path>`` overrides the default worktree root for this run.

    Note: this test deliberately does NOT use mock_bootstrap because we
    want WeaveOpenPaths.default() *not* patched — we want to verify that
    --root takes precedence over whatever default would have been used.
    """
    import subprocess as sp

    mock_client.get.return_value = _seed_spec()
    alt_root = tmp_path / "alt_worktrees"

    def _fake_run(cmd, **kw):
        if len(cmd) >= 4 and cmd[:3] == ["git", "worktree", "add"]:
            __import__("pathlib").Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    def _exec_aborts(file, args, env):
        raise SystemExit(0)

    with patch("loomcli._open.git_ops.subprocess.run", side_effect=_fake_run):
        with patch(
            "loomcli._open.git_ops.shutil.which", return_value="/usr/bin/git"
        ):
            with patch(
                "loomcli._open.runtime_exec.shutil.which",
                return_value="/usr/bin/claude",
            ):
                with patch("loomcli._open.runtime_exec.os.chdir"):
                    with patch(
                        "loomcli._open.runtime_exec.os.execvpe",
                        side_effect=_exec_aborts,
                    ):
                        result = runner.invoke(
                            app,
                            [
                                "open",
                                "lt_aaaaaaaaaaaaaaaaaaaaaa",
                                "--root",
                                str(alt_root),
                            ],
                        )

    assert result.exit_code == 0, result.output
    out = "".join(_strip_ansi(result.output).split())
    # The worktree path the bootstrap reported should sit under alt_root.
    assert "alt_worktrees" in out
