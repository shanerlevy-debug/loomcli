"""Tests for `weave agent run` daemon (PR #5b — universal-self-hosted-
agent reframe, thread aad43ba0).

Coverage:
  * Skill registry: pr_reconciliation registered, double-register fails.
  * Daemon refuses non-self_hosted agents (exit 2).
  * --once with empty queue exits cleanly.
  * --once with a watching item posts /reconciler/decide and (when
    confidence ≥ threshold + decision == rebase) /reconciler/rebase.
  * --dry-run posts /decide but never /rebase or /merge.
  * Low-confidence response stays log-only.
  * pr_reconciliation handler skips items whose status has no template
    mapped (e.g. ``awaiting_approval``).

Tests mock ``PowerloomClient`` exactly like ``test_agent_cmd.py`` does —
no live HTTP, no live Anthropic, fully deterministic.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.commands import agent_daemon


runner = CliRunner()


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = "fake-token"
    cfg.api_base_url = "https://api.powerloom.org"
    cfg.request_timeout_seconds = 30
    cfg.approval_justification = None
    return cfg


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------
def test_pr_reconciliation_skill_registered():
    """Smoke: importing agent_daemon registers the v1 skill."""
    assert "pr_reconciliation" in agent_daemon.get_registered_task_kinds()


def test_register_skill_rejects_double_register():
    """Re-registering the same task_kind raises — catches double-imports."""
    with pytest.raises(ValueError, match="already registered"):
        agent_daemon.register_skill(
            "pr_reconciliation",
            lambda *a, **k: None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# CLI guards
# ---------------------------------------------------------------------------
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_run_without_token_bails(mock_load_cfg):
    cfg = _cfg()
    cfg.access_token = None
    mock_load_cfg.return_value = cfg

    result = runner.invoke(
        app, ["agent", "run", "00000000-0000-0000-0000-000000000001", "--once"]
    )
    assert result.exit_code != 0
    assert "Not signed in" in result.stdout


@patch("loomcli.commands.agent_daemon.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_run_rejects_non_self_hosted_agent(mock_load_cfg, mock_client_cls):
    """The daemon only runs for ``runtime_type='self_hosted'`` — CMA agents
    error out with exit code 2."""
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    agent_id = "00000000-0000-0000-0000-000000000001"
    client.get.return_value = {
        "id": agent_id,
        "name": "alfred",
        "runtime_type": "cma",
        "task_kinds": ["qa"],
    }
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["agent", "run", agent_id, "--once"])
    assert result.exit_code == 2, result.stdout
    assert "self_hosted" in result.stdout


# ---------------------------------------------------------------------------
# End-to-end: --once with empty queue
# ---------------------------------------------------------------------------
@patch("loomcli.commands.agent_daemon.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_run_once_empty_queue_exits_cleanly(mock_load_cfg, mock_client_cls):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    agent_id = "00000000-0000-0000-0000-000000000001"

    def _get(path, **params):
        if path == f"/agents/{agent_id}":
            return {
                "id": agent_id,
                "name": "reconciler",
                "runtime_type": "self_hosted",
                "task_kinds": ["pr_reconciliation"],
            }
        if path == f"/agents/{agent_id}/work-queue":
            return {"items": [], "next_cursor": None}
        raise AssertionError(f"unexpected GET {path}")

    client.get.side_effect = _get
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["agent", "run", agent_id, "--once"])
    assert result.exit_code == 0, result.stdout
    assert "0 item(s)" in result.stdout
    # We should have hit the work-queue endpoint exactly once.
    queue_calls = [
        call for call in client.get.call_args_list
        if call.args[0] == f"/agents/{agent_id}/work-queue"
    ]
    assert len(queue_calls) == 1


# ---------------------------------------------------------------------------
# End-to-end: --once with a watching item → decide → rebase
# ---------------------------------------------------------------------------
@patch("loomcli.commands.agent_daemon.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_run_once_high_confidence_rebase(mock_load_cfg, mock_client_cls):
    """Watching item + decision=rebase + confidence ≥ 0.8 → /rebase
    posted."""
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    agent_id = "00000000-0000-0000-0000-000000000001"
    item = {
        "work_item_id": "pr_reconciliation:abc",
        "task_kind": "pr_reconciliation",
        "agent_id": agent_id,
        "organization_id": "org-1",
        "scope_ref": "shanerlevy-debug/Powerloom#42",
        "status": "watching",
        "last_action_at": None,
        "last_decision_id": None,
        "task_payload": {
            "repo_full_name": "shanerlevy-debug/Powerloom",
            "pr_number": 42,
            "conflict_summary": None,
        },
        "decision_history": [],
    }

    def _get(path, **params):
        if path == f"/agents/{agent_id}":
            return {
                "id": agent_id,
                "name": "reconciler",
                "runtime_type": "self_hosted",
                "task_kinds": ["pr_reconciliation"],
            }
        if path == f"/agents/{agent_id}/work-queue":
            return {"items": [item], "next_cursor": None}
        raise AssertionError(f"unexpected GET {path}")

    posts: list[tuple[str, dict | None]] = []

    def _post(path, body=None):
        posts.append((path, body))
        if path == "/reconciler/decide":
            return {
                "decision_id": "dec-1",
                "template_name": "safe_to_rebase_v1",
                "response": {
                    "decision": "rebase",
                    "reasoning": "small diff, no migrations",
                    "confidence": 0.95,
                },
                "confidence": 0.95,
                "model": "claude-sonnet-4-6-20251022",
                "cost_usd": 0.001,
                "cache_hit": False,
            }
        if path == "/reconciler/rebase":
            return {
                "ok": True,
                "new_status": "awaiting_approval",
                "detail": "rebase queued",
            }
        raise AssertionError(f"unexpected POST {path}")

    client.get.side_effect = _get
    client.post.side_effect = _post
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["agent", "run", agent_id, "--once"])
    assert result.exit_code == 0, result.stdout
    assert "rebased" in result.stdout
    paths_posted = [p for p, _ in posts]
    assert "/reconciler/decide" in paths_posted
    assert "/reconciler/rebase" in paths_posted


# ---------------------------------------------------------------------------
# End-to-end: --dry-run never calls action endpoints
# ---------------------------------------------------------------------------
@patch("loomcli.commands.agent_daemon.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_dry_run_decides_but_does_not_act(mock_load_cfg, mock_client_cls):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    agent_id = "00000000-0000-0000-0000-000000000001"
    item = {
        "work_item_id": "pr_reconciliation:abc",
        "task_kind": "pr_reconciliation",
        "agent_id": agent_id,
        "organization_id": "org-1",
        "scope_ref": "shanerlevy-debug/Powerloom#43",
        "status": "watching",
        "last_action_at": None,
        "last_decision_id": None,
        "task_payload": {
            "repo_full_name": "shanerlevy-debug/Powerloom",
            "pr_number": 43,
        },
        "decision_history": [],
    }

    def _get(path, **params):
        if path == f"/agents/{agent_id}":
            return {
                "id": agent_id,
                "name": "reconciler",
                "runtime_type": "self_hosted",
                "task_kinds": ["pr_reconciliation"],
            }
        if path == f"/agents/{agent_id}/work-queue":
            return {"items": [item], "next_cursor": None}
        raise AssertionError(f"unexpected GET {path}")

    posts: list[tuple[str, dict | None]] = []

    def _post(path, body=None):
        posts.append((path, body))
        if path == "/reconciler/decide":
            return {
                "decision_id": "dec-1",
                "template_name": "safe_to_rebase_v1",
                "response": {
                    "decision": "rebase",
                    "confidence": 0.95,
                },
                "confidence": 0.95,
                "model": "claude-sonnet-4-6-20251022",
                "cost_usd": 0.001,
                "cache_hit": False,
            }
        raise AssertionError(f"unexpected POST {path}")

    client.get.side_effect = _get
    client.post.side_effect = _post
    mock_client_cls.return_value = client

    result = runner.invoke(
        app, ["agent", "run", agent_id, "--once", "--dry-run"]
    )
    assert result.exit_code == 0, result.stdout
    paths_posted = [p for p, _ in posts]
    assert paths_posted == ["/reconciler/decide"], (
        f"dry-run shouldn't have called any action endpoint; got {paths_posted}"
    )


# ---------------------------------------------------------------------------
# End-to-end: low confidence stays log-only
# ---------------------------------------------------------------------------
@patch("loomcli.commands.agent_daemon.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_low_confidence_no_action(mock_load_cfg, mock_client_cls):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    agent_id = "00000000-0000-0000-0000-000000000001"
    item = {
        "work_item_id": "pr_reconciliation:abc",
        "task_kind": "pr_reconciliation",
        "agent_id": agent_id,
        "organization_id": "org-1",
        "scope_ref": "shanerlevy-debug/Powerloom#44",
        "status": "watching",
        "last_action_at": None,
        "last_decision_id": None,
        "task_payload": {
            "repo_full_name": "shanerlevy-debug/Powerloom",
            "pr_number": 44,
        },
        "decision_history": [],
    }

    def _get(path, **params):
        if path == f"/agents/{agent_id}":
            return {
                "id": agent_id,
                "name": "reconciler",
                "runtime_type": "self_hosted",
                "task_kinds": ["pr_reconciliation"],
            }
        if path == f"/agents/{agent_id}/work-queue":
            return {"items": [item], "next_cursor": None}
        raise AssertionError(f"unexpected GET {path}")

    posts: list[tuple[str, dict | None]] = []

    def _post(path, body=None):
        posts.append((path, body))
        if path == "/reconciler/decide":
            return {
                "decision_id": "dec-1",
                "template_name": "safe_to_rebase_v1",
                "response": {
                    "decision": "rebase",
                    "confidence": 0.5,  # below 0.8
                },
                "confidence": 0.5,
                "model": "claude-sonnet-4-6-20251022",
                "cost_usd": 0.001,
                "cache_hit": False,
            }
        raise AssertionError(f"unexpected POST {path}")

    client.get.side_effect = _get
    client.post.side_effect = _post
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["agent", "run", agent_id, "--once"])
    assert result.exit_code == 0, result.stdout
    paths_posted = [p for p, _ in posts]
    assert paths_posted == ["/reconciler/decide"], (
        "low-confidence decision should not trigger /rebase"
    )
    assert "below threshold" in result.stdout


# ---------------------------------------------------------------------------
# Direct skill-handler tests (skip CLI layer)
# ---------------------------------------------------------------------------
def _make_item(status: str, payload: dict | None = None) -> agent_daemon.WorkItem:
    return agent_daemon.WorkItem.from_envelope({
        "work_item_id": f"pr_reconciliation:test-{status}",
        "task_kind": "pr_reconciliation",
        "agent_id": "agent-1",
        "organization_id": "org-1",
        "scope_ref": "shanerlevy-debug/Powerloom#1",
        "status": status,
        "last_action_at": None,
        "last_decision_id": None,
        "task_payload": payload or {
            "repo_full_name": "shanerlevy-debug/Powerloom",
            "pr_number": 1,
        },
        "decision_history": [],
    })


def test_handler_skips_unmapped_status():
    """``awaiting_approval`` and other statuses without a template
    mapping become no-ops (operator's turn)."""
    client = MagicMock()
    item = _make_item("awaiting_approval")
    options = agent_daemon.DaemonOptions(once=True)
    result = asyncio.run(
        agent_daemon._pr_reconciliation_handler(client, item, options)
    )
    assert result.ok is True
    assert "no template" in result.summary
    # Nothing should have been called.
    client.post.assert_not_called()


def test_handler_merge_safe_without_approval_is_log_only():
    """decision=merge_safe but no approval_request_id → stays log-only.
    The operator drives the approval flow separately."""
    client = MagicMock()
    client.post.return_value = {
        "decision_id": "dec-2",
        "template_name": "safe_to_rebase_v1",
        "response": {
            "decision": "merge_safe",
            "confidence": 0.95,
        },
        "confidence": 0.95,
        "model": "claude-sonnet-4-6-20251022",
        "cost_usd": 0.001,
        "cache_hit": False,
    }
    item = _make_item(
        "watching",
        payload={
            "repo_full_name": "shanerlevy-debug/Powerloom",
            "pr_number": 5,
            # NB: no approval_request_id
        },
    )
    options = agent_daemon.DaemonOptions(once=True)

    result = asyncio.run(
        agent_daemon._pr_reconciliation_handler(client, item, options)
    )
    assert result.ok is True
    assert "awaiting operator approval" in result.summary
    # decide() was called once; no merge call.
    posts = [c.args[0] for c in client.post.call_args_list]
    assert posts == ["/reconciler/decide"]
