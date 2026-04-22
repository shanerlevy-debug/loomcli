"""CLI smoke tests via Typer's CliRunner.

No network needed — we invoke the Typer app directly and check exit
codes + stdout for the most common paths (version, help, parse error
on bad manifest).
"""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()


def test_version_prints_and_exits_zero():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip().startswith("0.")


def test_root_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    stdout = result.stdout
    for cmd in ("apply", "plan", "destroy", "get", "describe", "import", "auth", "agent-session", "workflow"):
        assert cmd in stdout, f"missing subcommand help: {cmd}"


def test_plan_rejects_malformed_manifest(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a: proper: yaml: document")
    result = runner.invoke(app, ["plan", str(bad)])
    assert result.exit_code != 0  # parse error is non-zero exit


def test_apply_without_any_signed_in_user_bails(tmp_path, monkeypatch):
    """If no credentials file exists, apply/plan should print a friendly
    message and exit rather than trying to POST unauthenticated."""
    # Override the autouse fixture's token.
    home = tmp_path / "no-creds"
    home.mkdir()
    monkeypatch.setenv("POWERLOOM_HOME", str(home))
    good = tmp_path / "good.yaml"
    good.write_text(
        "apiVersion: powerloom/v1\nkind: OU\nmetadata: { name: x }\nspec: { display_name: X }\n"
    )
    result = runner.invoke(app, ["plan", str(good)])
    assert result.exit_code != 0


def test_get_help_lists_kinds():
    result = runner.invoke(app, ["get", "--help"])
    assert result.exit_code == 0
    # At least the core kinds should appear in the help text.
    for kind in ("agents", "skills", "mcp-deployments"):
        assert kind in result.stdout
