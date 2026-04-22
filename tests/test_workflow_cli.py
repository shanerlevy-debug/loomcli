"""Tests for `weave workflow *` and `weave agent-session tasks/task-complete`.

Mocks PowerloomClient so tests are pure-CLI — no network, no DB.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()


def test_workflow_help():
    result = runner.invoke(app, ["workflow", "--help"])
    assert result.exit_code == 0
    for sub in ("apply", "run", "status", "ls", "cancel"):
        assert sub in result.stdout


@patch("loomcli.commands.workflow_cmd.PowerloomClient")
def test_workflow_apply_reads_yaml_and_posts(
    mock_client_cls, monkeypatch, tmp_path: Path
):
    home = tmp_path / "creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    manifest = tmp_path / "wf.yaml"
    manifest.write_text(
        """
kind: Workflow
apiVersion: powerloom.app/v1
metadata:
  name: demo-wf
spec:
  nodes:
    - id: t
      kind: trigger
    - id: o
      kind: output
  edges:
    - from: t
      to: o
""".strip(),
        encoding="utf-8",
    )

    mock = MagicMock()
    mock.post.return_value = {
        "definition": {
            "id": "d1",
            "name": "demo-wf",
            "version": 1,
            "organization_id": "org-1",
            "definition_json": {"nodes": [], "edges": []},
            "created_at": "2026-04-22T00:00:00Z",
            "ou_id": None,
            "deprecated_at": None,
        },
        "created_new": True,
    }
    mock_client_cls.return_value = mock

    result = runner.invoke(
        app, ["workflow", "apply", "-f", str(manifest)]
    )
    assert result.exit_code == 0, result.stdout
    assert "created" in result.stdout
    assert "demo-wf" in result.stdout
    args, _ = mock.post.call_args
    assert args[0] == "/workflows"
    assert args[1]["name"] == "demo-wf"
    assert "nodes" in args[1]["definition"]


@patch("loomcli.commands.workflow_cmd.PowerloomClient")
def test_workflow_run_posts_to_workflow_runs(
    mock_client_cls, monkeypatch, tmp_path: Path
):
    home = tmp_path / "creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock = MagicMock()
    mock.post.return_value = {
        "id": "run-1",
        "definition_id": "def-1",
        "triggered_by": "user:admin@dev.local",
        "status": "queued",
        "started_at": "2026-04-22T00:00:00Z",
    }
    mock_client_cls.return_value = mock

    result = runner.invoke(app, ["workflow", "run", "demo-wf"])
    assert result.exit_code == 0
    assert "Queued" in result.stdout
    args, _ = mock.post.call_args
    assert args[0] == "/workflow-runs"
    assert args[1] == {"workflow_name": "demo-wf"}


@patch("loomcli.commands.workflow_cmd.PowerloomClient")
def test_workflow_status_renders_steps(
    mock_client_cls, monkeypatch, tmp_path: Path
):
    home = tmp_path / "creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock = MagicMock()
    mock.get.return_value = {
        "id": "run-1",
        "definition_id": "def-1",
        "triggered_by": "user:admin@dev.local",
        "status": "done",
        "started_at": "2026-04-22T00:00:00Z",
        "completed_at": "2026-04-22T00:00:05Z",
        "steps": [
            {"node_id": "start", "node_kind": "trigger", "status": "done"},
            {"node_id": "end", "node_kind": "output", "status": "done"},
        ],
    }
    mock_client_cls.return_value = mock

    result = runner.invoke(app, ["workflow", "status", "run-1"])
    assert result.exit_code == 0
    assert "done" in result.stdout
    assert "start" in result.stdout
    assert "end" in result.stdout


@patch("loomcli.commands.workflow_cmd.PowerloomClient")
def test_workflow_ls_runs_renders_table(
    mock_client_cls, monkeypatch, tmp_path: Path
):
    home = tmp_path / "creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock = MagicMock()
    mock.get.return_value = {
        "runs": [
            {
                "id": "run-abc123",
                "status": "done",
                "triggered_by": "user:x",
                "started_at": "2026-04-22T00:00:00Z",
            }
        ],
        "total": 1,
    }
    mock_client_cls.return_value = mock

    result = runner.invoke(app, ["workflow", "ls", "--runs"])
    assert result.exit_code == 0
    assert "run-abc" in result.stdout


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_agent_session_tasks_lists_assignments(
    mock_client_cls, monkeypatch, tmp_path: Path
):
    home = tmp_path / "creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock = MagicMock()
    mock.get.return_value = {
        "tasks": [
            {
                "id": "step-1",
                "run_id": "run-1",
                "node_id": "work",
                "node_kind": "agent",
                "workflow_name": "demo-wf",
                "status": "running",
            }
        ],
        "total": 1,
    }
    mock_client_cls.return_value = mock

    result = runner.invoke(app, ["agent-session", "tasks", "sess-1"])
    assert result.exit_code == 0
    assert "demo-wf" in result.stdout
    assert "work" in result.stdout


@patch("loomcli.commands.agent_session_cmd.PowerloomClient")
def test_agent_session_task_complete_posts_outputs(
    mock_client_cls, monkeypatch, tmp_path: Path
):
    home = tmp_path / "creds"
    home.mkdir()
    (home / "credentials").write_text("fake-token\n", encoding="utf-8")
    monkeypatch.setenv("POWERLOOM_HOME", str(home))

    mock = MagicMock()
    mock.post.return_value = {
        "id": "step-1",
        "run_id": "run-1",
        "node_id": "work",
        "node_kind": "agent",
        "status": "done",
    }
    mock_client_cls.return_value = mock

    result = runner.invoke(
        app,
        [
            "agent-session",
            "task-complete",
            "sess-1",
            "step-1",
            "--outcome",
            "done",
            "--output",
            "result=ok",
            "--output",
            "lines_changed=42",
        ],
    )
    assert result.exit_code == 0
    args, _ = mock.post.call_args
    assert args[0] == "/agent-sessions/sess-1/tasks/step-1/complete"
    assert args[1]["outcome"] == "done"
    assert args[1]["outputs"] == {"result": "ok", "lines_changed": "42"}
