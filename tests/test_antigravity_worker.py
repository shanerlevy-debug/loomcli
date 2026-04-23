import pytest
from typer.testing import CliRunner
from loomcli.cli import app

runner = CliRunner()

def test_antigravity_worker_help():
    result = runner.invoke(app, ["antigravity-worker", "--help"])
    assert result.exit_code == 0
    assert "Daemon to dispatch tasks to local Antigravity IDE" in result.stdout

def test_antigravity_worker_run():
    # Calling it with an agent ID should print the stub outputs
    result = runner.invoke(app, ["antigravity-worker", "--agent-id", "test-agent-123"])
    assert result.exit_code == 0
    assert "Starting Antigravity worker for agent test-agent-123" in result.stdout
    assert "Not fully implemented yet" in result.stdout
