from __future__ import annotations

from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.command_registry import list_commands


runner = CliRunner()


def test_command_registry_includes_new_cli_surfaces():
    commands = {row["command"] for row in list_commands()}
    assert "weave agent config" in commands
    assert "weave agent set-model" in commands
    assert "weave agent-session status" in commands
    assert "weave agent-session watch" in commands
    assert "weave thread my-work" in commands
    assert "weave doctor" in commands
    assert "weave plugin doctor" in commands
    assert "weave profile set" in commands
    assert "weave approval wait" in commands


def test_commands_command_exports_json():
    result = runner.invoke(app, ["commands", "--prefix", "weave agent", "--json"])

    assert result.exit_code == 0, result.stdout
    assert "weave agent config" in result.stdout
    assert "weave agent-session status" in result.stdout
    assert "weave profile set" not in result.stdout
