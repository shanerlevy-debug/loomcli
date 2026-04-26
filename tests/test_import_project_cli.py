"""Tests for `weave import-project` (v059 self-import MVP, slice 6 CLI shim).

The CLI's job is small: walk the checkout for known source files,
package them into the engine's expected dict shape, POST to
/projects/import/from-source, and render the result. Most of the
hard work lives engine-side; these tests cover the file-walking,
auth gate, error rendering, and the basic happy-path round trip.

HTTP layer is mocked via patching PowerloomClient — the actual round
trip against a live engine is verified in the Powerloom repo's
integration tests.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomApiError


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_minimal_sources(tmp_path: Path) -> Path:
    """Tmp checkout with the minimum files build_import_plan recognizes."""
    (tmp_path / "Project.md").write_text(
        textwrap.dedent(
            """\
            # Powerloom

            ## 2. Current build state

            ### Phase lifecycle

            | # | Name | Status | Detail |
            |---|---|---|---|
            | 0 | Foundation | ✓ v001 | link |
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "KNOWN_ISSUES.md").write_text(
        "# Known Issues\n\n## Active\n\n### KI-001 — fixture\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / "ReadMe.md").write_text("# ReadMe\n\nbody\n", encoding="utf-8")
    docs_phases = tmp_path / "docs" / "phases"
    docs_phases.mkdir(parents=True)
    (docs_phases / "phase-foo.md").write_text("# Phase Foo\n", encoding="utf-8")
    (docs_phases / "phase-bar.md").write_text("# Phase Bar\n", encoding="utf-8")
    docs_handoffs = tmp_path / "docs" / "handoffs"
    docs_handoffs.mkdir(parents=True)
    (docs_handoffs / "handoff-2026-04-25.md").write_text("# Handoff\n", encoding="utf-8")
    (tmp_path / "docs" / "out-of-scope.md").write_text(
        "# Out of scope\n\n## Bucket\n- something\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def mock_client():
    """Patch PowerloomClient as used inside import_project_cmd. The
    returned MagicMock represents the open client; tests configure
    .post.return_value to shape the engine response."""
    with patch("loomcli.commands.import_project_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        # Default: a passing response with non-trivial counters
        client.post.return_value = {
            "project_id": "00000000-0000-0000-0000-000000000001",
            "project_created": True,
            "milestones_created": 1,
            "milestones_updated": 0,
            "threads_created": 1,
            "threads_updated": 0,
            "tags_created": 0,
            "import_source_replies_written": 1,
            "skipped_dedupe_match": 0,
            "errors": [],
            "summary": "ApplyResult(project=created, milestones=+1/~0, threads=+1/~0, tags=+0, replies=+1)",
            "dry_run": False,
        }
        yield client


# ---------------------------------------------------------------------------
# Help + discovery
# ---------------------------------------------------------------------------


def test_import_project_command_registered() -> None:
    result = runner.invoke(app, ["import-project", "--help"])
    assert result.exit_code == 0
    assert "Import a Powerloom-shaped repo" in result.stdout or "Powerloom" in result.stdout
    # Options surface
    assert "--dry-run" in result.stdout
    assert "--slug" in result.stdout
    assert "--name" in result.stdout
    assert "--slug-override" in result.stdout


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_requires_signin(monkeypatch, tmp_path: Path) -> None:
    # Override the autouse signed-in fixture by emptying credentials
    import os
    creds = Path(os.environ["POWERLOOM_HOME"]) / "credentials"
    creds.write_text("", encoding="utf-8")
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    (repo / "Project.md").write_text("# x\n", encoding="utf-8")

    result = runner.invoke(app, ["import-project", str(repo)])
    assert result.exit_code == 1
    assert "sign in" in result.stdout.lower() or "login" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Validation: empty / missing source tree
# ---------------------------------------------------------------------------


def test_empty_repo_fails_with_helpful_message(tmp_path: Path, mock_client) -> None:
    """No recognized files → exit 1 with a list of expected paths."""
    empty = tmp_path / "no-sources"
    empty.mkdir()
    result = runner.invoke(app, ["import-project", str(empty)])
    assert result.exit_code == 1
    assert "no recognized source files" in result.stdout.lower()
    # Network was never called
    mock_client.post.assert_not_called()


def test_nonexistent_path_rejected_by_typer(tmp_path: Path) -> None:
    bogus = tmp_path / "does-not-exist"
    result = runner.invoke(app, ["import-project", str(bogus)])
    # Typer's exists=True check → exit 2
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Source collection — verifies the right files are walked
# ---------------------------------------------------------------------------


def test_collects_known_source_files(repo_with_minimal_sources: Path, mock_client) -> None:
    """Walks all the file types the engine knows about — Project.md,
    KNOWN_ISSUES.md, ReadMe.md, docs/out-of-scope.md, phase-*.md,
    docs/handoffs/*.md."""
    result = runner.invoke(app, ["import-project", str(repo_with_minimal_sources)])
    assert result.exit_code == 0, result.stdout

    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    path, body = args
    assert path == "/projects/import/from-source"
    files = body["source_files"]
    # All seven files should be present (3 top-level + 1 optional + 2 phase + 1 handoff)
    assert "Project.md" in files
    assert "KNOWN_ISSUES.md" in files
    assert "ReadMe.md" in files
    assert "docs/out-of-scope.md" in files
    assert "docs/phases/phase-foo.md" in files
    assert "docs/phases/phase-bar.md" in files
    assert "docs/handoffs/handoff-2026-04-25.md" in files
    assert len(files) == 7


def test_skips_missing_optional_files(tmp_path: Path, mock_client) -> None:
    """Only Project.md present — still uploads, doesn't fail on the
    other missing files."""
    (tmp_path / "Project.md").write_text("# x\n", encoding="utf-8")
    result = runner.invoke(app, ["import-project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    body = mock_client.post.call_args.args[1]
    assert list(body["source_files"].keys()) == ["Project.md"]


# ---------------------------------------------------------------------------
# Wire body shape
# ---------------------------------------------------------------------------


def test_body_includes_slug_name_and_dry_run(repo_with_minimal_sources: Path, mock_client) -> None:
    result = runner.invoke(
        app,
        [
            "import-project",
            str(repo_with_minimal_sources),
            "--slug",
            "acme",
            "--name",
            "Acme Roadmap",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    body = mock_client.post.call_args.args[1]
    assert body["project_slug"] == "acme"
    assert body["project_name"] == "Acme Roadmap"
    assert body["dry_run"] is True
    # Slug override wasn't passed → not in body
    assert "project_slug_override" not in body


def test_slug_override_added_to_body(repo_with_minimal_sources: Path, mock_client) -> None:
    result = runner.invoke(
        app,
        [
            "import-project",
            str(repo_with_minimal_sources),
            "--slug-override",
            "powerloom-sandbox",
        ],
    )
    assert result.exit_code == 0
    body = mock_client.post.call_args.args[1]
    assert body["project_slug_override"] == "powerloom-sandbox"


def test_default_slug_is_powerloom(repo_with_minimal_sources: Path, mock_client) -> None:
    """Default --slug=powerloom matches the canonical project."""
    result = runner.invoke(app, ["import-project", str(repo_with_minimal_sources)])
    assert result.exit_code == 0
    body = mock_client.post.call_args.args[1]
    assert body["project_slug"] == "powerloom"
    assert body["project_name"] == "Powerloom"
    assert body["dry_run"] is False


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------


def test_renders_summary_on_success(repo_with_minimal_sources: Path, mock_client) -> None:
    result = runner.invoke(app, ["import-project", str(repo_with_minimal_sources)])
    assert result.exit_code == 0
    assert "ApplyResult" in result.stdout
    # Per-counter detail lines for non-zero values
    assert "milestones_created" in result.stdout
    assert "threads_created" in result.stdout


def test_renders_dry_run_banner(repo_with_minimal_sources: Path, mock_client) -> None:
    mock_client.post.return_value = {
        **mock_client.post.return_value,
        "dry_run": True,
    }
    result = runner.invoke(
        app, ["import-project", str(repo_with_minimal_sources), "--dry-run"]
    )
    assert result.exit_code == 0
    assert "DRY RUN" in result.stdout


def test_renders_per_op_errors(repo_with_minimal_sources: Path, mock_client) -> None:
    mock_client.post.return_value = {
        **mock_client.post.return_value,
        "errors": ["thread X failed: invalid status", "thread Y failed: invalid priority"],
    }
    result = runner.invoke(app, ["import-project", str(repo_with_minimal_sources)])
    assert result.exit_code == 0  # Per-op errors don't abort
    assert "2 per-op error" in result.stdout
    assert "invalid status" in result.stdout


# ---------------------------------------------------------------------------
# API error handling
# ---------------------------------------------------------------------------


def test_api_error_prints_and_exits_nonzero(repo_with_minimal_sources: Path, mock_client) -> None:
    mock_client.post.side_effect = PowerloomApiError(
        status_code=413,
        message="HTTP 413 POST /projects/import/from-source: source_files total size exceeds limit",
    )
    result = runner.invoke(app, ["import-project", str(repo_with_minimal_sources)])
    assert result.exit_code == 1
    assert "Import failed" in result.stdout
    assert "413" in result.stdout
