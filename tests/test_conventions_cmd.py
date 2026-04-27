"""Tests for `weave conventions {sync,show,list}` (Powerloom v064 +
2026-04-27 conventions-sync work).

Patches PowerloomClient as used by conventions_cmd; relies on
conftest.py's autouse fixture to set POWERLOOM_HOME + write a fake
credentials file (so auth-gated commands don't bail with "Not signed
in"). File-write paths use tmp_path for isolation.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


@pytest.fixture
def mock_client():
    """Patch PowerloomClient as used by conventions_cmd."""
    with patch("loomcli.commands.conventions_cmd.PowerloomClient") as cls:
        client = MagicMock()
        client.__enter__.return_value = client
        cls.return_value = client
        yield client


def _seed_conventions(*specs):
    """specs: tuples of (name, mode, scope, summary, items)."""
    out = []
    for name, mode, scope, summary, items in specs:
        out.append({
            "id": f"id-{name}",
            "name": name,
            "display_name": name.replace("-", " ").title(),
            "enforcement_mode": mode,
            "applies_to_scope_ref": scope,
            "additional_scope_refs": [],
            "body": {"summary": summary, "items": list(items)},
            "status": "active",
        })
    return out


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_conventions_subgroup_registered() -> None:
    result = runner.invoke(app, ["conventions", "--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    for cmd in ("sync", "show", "list"):
        assert cmd in out


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_renders_table(mock_client) -> None:
    """Use a short slug because the table column is fixed-width and rich
    will line-wrap longer names — the assertion below should target a
    substring that survives wrapping."""
    mock_client.get.return_value = _seed_conventions(
        ("pytest-required", "warn",
         "bespoke-technology.powerloom.engineering",
         "All engine PRs must include a passing pytest run.",
         ["Run `docker compose exec api pytest`."]),
    )
    result = runner.invoke(
        app, ["conventions", "show", "--scope", "bespoke-technology.powerloom.engineering"],
    )
    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "pytest-required" in out
    assert "warn" in out


def test_show_handles_empty(mock_client) -> None:
    mock_client.get.return_value = []
    result = runner.invoke(
        app, ["conventions", "show", "--scope", "x.y.z"],
    )
    assert result.exit_code == 0
    assert "no conventions" in _strip_ansi(result.output).lower()


# ---------------------------------------------------------------------------
# sync — file write paths
# ---------------------------------------------------------------------------


def test_sync_creates_claude_md_when_missing(mock_client, tmp_path) -> None:
    """No CLAUDE.md exists -> sync creates it with header + autosync block."""
    mock_client.get.return_value = _seed_conventions(
        ("no-direct-main", "enforce", "x.y.z", "Never commit to main.", []),
    )
    workdir = tmp_path / "ws"
    workdir.mkdir()
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert result.exit_code == 0, result.output
    target = workdir / "CLAUDE.md"
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN" in body
    assert "POWERLOOM_CONVENTIONS_AUTOSYNC_END" in body
    assert "no-direct-main" in body
    assert "Never commit to main" in body


def test_sync_replaces_existing_block(mock_client, tmp_path) -> None:
    """Existing autosync block gets replaced; surrounding hand-edits preserved."""
    workdir = tmp_path / "ws"
    workdir.mkdir()
    target = workdir / "CLAUDE.md"
    target.write_text(
        "# CLAUDE.md\n\n"
        "## Hand-edited above the block\n\n"
        "Some user-written rules.\n\n"
        "<!-- POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN -->\n"
        "old content here\n"
        "<!-- POWERLOOM_CONVENTIONS_AUTOSYNC_END -->\n"
        "\n## Hand-edited below the block\n\nMore user content.\n",
        encoding="utf-8",
    )
    mock_client.get.return_value = _seed_conventions(
        ("new-rule", "advisory", "x.y.z", "Brand new rule.", ["item one"]),
    )
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert result.exit_code == 0, result.output
    body = target.read_text(encoding="utf-8")
    assert "Hand-edited above the block" in body
    assert "Hand-edited below the block" in body
    assert "old content here" not in body
    assert "new-rule" in body
    assert "Brand new rule" in body


def test_sync_appends_when_no_markers(mock_client, tmp_path) -> None:
    """Existing CLAUDE.md without markers -> append at end."""
    workdir = tmp_path / "ws"
    workdir.mkdir()
    target = workdir / "CLAUDE.md"
    target.write_text(
        "# CLAUDE.md\n\n## Existing content\n\nNo markers here.\n",
        encoding="utf-8",
    )
    mock_client.get.return_value = _seed_conventions(
        ("rule-x", "warn", "x.y.z", "Rule X summary.", []),
    )
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert result.exit_code == 0, result.output
    body = target.read_text(encoding="utf-8")
    assert "Existing content" in body
    assert "POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN" in body
    assert "rule-x" in body


def test_sync_dry_run_does_not_write(mock_client, tmp_path) -> None:
    workdir = tmp_path / "ws"
    workdir.mkdir()
    mock_client.get.return_value = _seed_conventions(
        ("rule-y", "advisory", "x.y.z", "Y summary.", []),
    )
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert not (workdir / "CLAUDE.md").exists()
    assert "dry-run" in _strip_ansi(result.output).lower()


def test_sync_runtime_all_writes_three_distinct_files(mock_client, tmp_path) -> None:
    """--runtime all writes CLAUDE.md, AGENTS.md, GEMINI.md (gemini_cli + antigravity dedupe to one GEMINI.md)."""
    workdir = tmp_path / "ws"
    workdir.mkdir()
    mock_client.get.return_value = _seed_conventions(
        ("test-rule", "advisory", "x.y.z", "Summary.", []),
    )
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "all"],
    )
    assert result.exit_code == 0, result.output
    assert (workdir / "CLAUDE.md").exists()
    assert (workdir / "AGENTS.md").exists()
    assert (workdir / "GEMINI.md").exists()


def test_sync_uses_cached_scope_when_omitted(mock_client, tmp_path) -> None:
    """Second sync without --scope falls back to the cached one from the first."""
    workdir = tmp_path / "ws"
    workdir.mkdir()
    mock_client.get.return_value = _seed_conventions(
        ("cached-rule", "advisory", "x.y.z", "S.", []),
    )
    r1 = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert r1.exit_code == 0
    r2 = runner.invoke(
        app,
        ["conventions", "sync",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert r2.exit_code == 0, r2.output
    assert mock_client.get.call_count == 2
    # v0.7.0: --scope passes through as ou_path to /effective. Dotted
    # `x.y.z` becomes `/x/y/z` (the engine resolves the dotted path).
    for call in mock_client.get.call_args_list:
        assert call.args[0] == "/memory/semantic/conventions/effective"
        assert call.kwargs.get("ou_path") == "/x/y/z"


def test_sync_no_scope_no_cache_exits_2(mock_client, tmp_path) -> None:
    workdir = tmp_path / "ws"
    workdir.mkdir()
    result = runner.invoke(
        app,
        ["conventions", "sync",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert result.exit_code == 2
    # v0.7.3 — error message mentions OU scope and the auto-detect chain.
    out = _strip_ansi(result.output).lower()
    assert "scope" in out
    assert "--scope" in out


def test_sync_invalid_runtime_exits_2(mock_client, tmp_path) -> None:
    """Bad --runtime value rejected. Output may be empty (typer routes
    error to stderr in some click versions) — just verify the exit code."""
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z", "--runtime", "bogus"],
    )
    assert result.exit_code != 0


def test_sync_quiet_suppresses_success_output(mock_client, tmp_path) -> None:
    """SessionStart-hook context: --quiet on the happy path means no stdout."""
    workdir = tmp_path / "ws"
    workdir.mkdir()
    mock_client.get.return_value = _seed_conventions(
        ("q-rule", "advisory", "x.y.z", "S.", []),
    )
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code", "--quiet"],
    )
    assert result.exit_code == 0, result.output
    assert _strip_ansi(result.output).strip() == ""


def test_sync_corrupted_half_marker_skips_safely(mock_client, tmp_path) -> None:
    """Only one of the marker pair present -> refuse to overwrite."""
    workdir = tmp_path / "ws"
    workdir.mkdir()
    target = workdir / "CLAUDE.md"
    target.write_text(
        "# CLAUDE.md\n<!-- POWERLOOM_CONVENTIONS_AUTOSYNC_BEGIN -->\n"
        "(no end marker)\n",
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")
    mock_client.get.return_value = _seed_conventions(
        ("rule", "advisory", "x.y.z", "S.", []),
    )
    result = runner.invoke(
        app,
        ["conventions", "sync", "--scope", "x.y.z",
         "--workdir", str(workdir), "--runtime", "claude_code"],
    )
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == original
    # Rich line-wrap can split "refusing to overwrite" with a newline at
    # narrow terminals — match the substring that always survives.
    assert "refusing" in _strip_ansi(result.output).lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_renders_table(mock_client) -> None:
    mock_client.get.return_value = _seed_conventions(
        ("rule-1", "warn", "scope.a", "S1", []),
        ("rule-2", "enforce", "scope.b", "S2", []),
    )
    result = runner.invoke(app, ["conventions", "list"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    assert "rule-1" in out
    assert "rule-2" in out


def test_list_filters_status(mock_client) -> None:
    mock_client.get.return_value = []
    result = runner.invoke(app, ["conventions", "list", "--status", "archived"])
    assert result.exit_code == 0
    last_call = mock_client.get.call_args
    assert last_call.kwargs.get("status") == "archived"
