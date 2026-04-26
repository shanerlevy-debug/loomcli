"""Tests for `weave thread …` subcommands.

Mirrors the test pattern in test_skill_upload_cli.py + test_import_project_cli.py:
patch PowerloomClient + a config helper to keep tests offline. Engine round-trip
is verified by the Powerloom repo's tracker route tests; these tests only
verify CLI argument parsing, body shape, and output rendering.
"""
from __future__ import annotations

import json
import uuid
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
def mock_client():
    """Patch PowerloomClient as used inside thread_cmd. Returns the mock so
    tests can configure per-method return values."""
    with patch("loomcli.commands.thread_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        # Default project resolution — single project named 'powerloom'
        client.get.side_effect = lambda path, **kw: (
            [{"id": "00000000-0000-0000-0000-0000000000aa", "slug": "powerloom", "name": "Powerloom"}]
            if path == "/projects" else None
        )
        yield client


def _seed_thread(**overrides) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "test thread",
        "status": "open",
        "priority": "medium",
        "created_by": "u1",
        "assigned_to": None,
        "metadata_json": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Help + discovery
# ---------------------------------------------------------------------------


def test_thread_subgroup_registered() -> None:
    result = runner.invoke(app, ["thread", "--help"])
    assert result.exit_code == 0
    for cmd in ("create", "pluck", "reply", "done", "close", "wont-do", "list", "show", "update"):
        assert cmd in result.stdout


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_basic(mock_client) -> None:
    """Happy-path create: resolves slug → UUID, posts body, prints summary."""
    created = _seed_thread(title="Fix Alfred", priority="high")
    # First call is /projects (slug resolution), second is POST /projects/{id}/threads
    mock_client.get.side_effect = [
        [{"id": "00000000-0000-0000-0000-0000000000aa", "slug": "powerloom"}],
    ]
    mock_client.post.return_value = created

    result = runner.invoke(
        app,
        ["thread", "create", "--project", "powerloom", "--title", "Fix Alfred", "--priority", "high", "--description", "body text"],
    )
    assert result.exit_code == 0, result.stdout
    # POST shape
    args, _ = mock_client.post.call_args
    path, body = args
    assert path == "/projects/00000000-0000-0000-0000-0000000000aa/threads"
    assert body["title"] == "Fix Alfred"
    assert body["priority"] == "high"
    assert body["description"] == "body text"
    assert "Thread created" in result.stdout


def test_create_uuid_project_skips_slug_lookup(mock_client) -> None:
    """When --project is a UUID, no /projects lookup happens."""
    pid = "12345678-1234-1234-1234-123456789012"
    mock_client.post.return_value = _seed_thread()
    result = runner.invoke(
        app,
        ["thread", "create", "--project", pid, "--title", "x", "--priority", "low"],
    )
    assert result.exit_code == 0, result.stdout
    # Only the POST call, no /projects GET
    assert all("/projects" != c.args[0] for c in mock_client.get.call_args_list if c.args)
    args, _ = mock_client.post.call_args
    assert args[0] == f"/projects/{pid}/threads"


def test_create_invalid_priority_rejected(mock_client) -> None:
    """Bad --priority short-circuits with exit 2."""
    result = runner.invoke(app, ["thread", "create", "--title", "x", "--priority", "bogus"])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.output or "")
    assert "Invalid priority" in combined


def test_create_unknown_slug_lists_available(mock_client) -> None:
    """Unknown slug → exit 1 with a list of available slugs."""
    mock_client.get.side_effect = [
        [{"id": "x", "slug": "powerloom"}, {"id": "y", "slug": "other"}],
    ]
    result = runner.invoke(app, ["thread", "create", "--project", "nope", "--title", "x"])
    assert result.exit_code == 1
    assert "nope" in result.stdout
    assert "powerloom" in result.stdout
    assert "other" in result.stdout


def test_create_json_output(mock_client) -> None:
    """--json prints the created thread as JSON."""
    created = _seed_thread(id="abc-123")
    mock_client.get.side_effect = [[{"id": "p1", "slug": "powerloom"}]]
    mock_client.post.return_value = created
    result = runner.invoke(app, ["thread", "create", "--title", "x", "--json"])
    assert result.exit_code == 0
    # JSON ends up in stdout — find it
    assert "abc-123" in result.stdout


# ---------------------------------------------------------------------------
# pluck
# ---------------------------------------------------------------------------


def test_pluck_happy_path(mock_client) -> None:
    plucked = _seed_thread(status="in_progress")
    mock_client.post.return_value = plucked
    result = runner.invoke(app, ["thread", "pluck", "11111111-1111-1111-1111-111111111111"])
    assert result.exit_code == 0
    args, _ = mock_client.post.call_args
    assert args[0] == "/threads/11111111-1111-1111-1111-111111111111/pluck"
    assert args[1] == {}  # no agent_id passed
    assert "Plucked" in result.stdout


def test_pluck_409_renders_friendly_message(mock_client) -> None:
    mock_client.post.side_effect = PowerloomApiError(409, "HTTP 409 ... Thread already plucked")
    result = runner.invoke(app, ["thread", "pluck", "11111111-1111-1111-1111-111111111111"])
    assert result.exit_code == 1
    assert "already plucked" in result.stdout.lower()


# ---------------------------------------------------------------------------
# reply
# ---------------------------------------------------------------------------


def test_reply_happy_path(mock_client) -> None:
    mock_client.post.return_value = {"id": "r1", "thread_id": "t1", "content": "hi"}
    result = runner.invoke(app, ["thread", "reply", "t1", "hi"])
    assert result.exit_code == 0
    args, _ = mock_client.post.call_args
    assert args[0] == "/threads/t1/replies"
    assert args[1]["content"] == "hi"
    assert args[1]["reply_type"] == "comment"


def test_reply_requires_content(mock_client) -> None:
    """No content + no --from-stdin → exit 2."""
    result = runner.invoke(app, ["thread", "reply", "t1"])
    assert result.exit_code == 2
    assert "required" in result.stdout.lower() or "from-stdin" in result.stdout.lower()


# ---------------------------------------------------------------------------
# status verbs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,expected_status",
    [
        ("done", "done"),
        ("close", "closed"),
        ("wont-do", "wont_do"),
    ],
)
def test_status_verb_patches_correctly(mock_client, verb, expected_status) -> None:
    mock_client.patch.return_value = _seed_thread(status=expected_status)
    result = runner.invoke(app, ["thread", verb, "t1"])
    assert result.exit_code == 0, result.stdout
    args, _ = mock_client.patch.call_args
    assert args[0] == "/threads/t1"
    assert args[1] == {"status": expected_status}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_combines_fields(mock_client) -> None:
    mock_client.patch.return_value = _seed_thread(status="review", priority="critical")
    result = runner.invoke(
        app,
        ["thread", "update", "t1", "--status", "review", "--priority", "critical"],
    )
    assert result.exit_code == 0
    args, _ = mock_client.patch.call_args
    body = args[1]
    assert body == {"status": "review", "priority": "critical"}


def test_update_assigned_to_empty_string_unassigns(mock_client) -> None:
    """--assigned-to '' → null in body (unassign)."""
    mock_client.patch.return_value = _seed_thread()
    result = runner.invoke(app, ["thread", "update", "t1", "--assigned-to", ""])
    assert result.exit_code == 0
    args, _ = mock_client.patch.call_args
    assert args[1] == {"assigned_to": None}


def test_update_invalid_status_rejected(mock_client) -> None:
    result = runner.invoke(app, ["thread", "update", "t1", "--status", "bogus"])
    assert result.exit_code == 2


def test_update_no_fields_short_circuits(mock_client) -> None:
    result = runner.invoke(app, ["thread", "update", "t1"])
    assert result.exit_code == 2
    assert "No fields" in result.stdout or "no fields" in result.stdout.lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_mine_uses_my_work_endpoint(mock_client) -> None:
    mock_client.get.side_effect = None
    mock_client.get.return_value = [_seed_thread(title="A"), _seed_thread(title="B", id="22222222-2222-2222-2222-222222222222")]
    result = runner.invoke(app, ["thread", "list", "--mine"])
    assert result.exit_code == 0, result.stdout
    args, kwargs = mock_client.get.call_args
    assert args[0] == "/threads/my-work"
    assert "A" in result.stdout
    assert "B" in result.stdout


def test_list_project_required_without_mine(mock_client) -> None:
    result = runner.invoke(app, ["thread", "list"])
    assert result.exit_code == 2
    assert "--project" in result.stdout or "--mine" in result.stdout


def test_list_renders_subprincipal_in_owner_column(mock_client) -> None:
    """When metadata_json.session_attribution.subprincipal_name is present, it
    surfaces in the Owner column instead of the raw user UUID prefix."""
    mock_client.get.side_effect = [
        [{"id": "p1", "slug": "powerloom"}],
        [_seed_thread(metadata_json={"session_attribution": {"subprincipal_name": "Claude Code Session"}})],
    ]
    result = runner.invoke(app, ["thread", "list", "--project", "powerloom"])
    assert result.exit_code == 0, result.stdout
    assert "Claude Code Session" in result.stdout


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_renders_title_metadata_replies(mock_client) -> None:
    thread = _seed_thread(
        title="Hello",
        description="Body of the thread.",
        metadata_json={"session_attribution": {"subprincipal_name": "Claude Code"}},
    )
    replies = [
        {"id": "r1", "content": "first reply", "reply_type": "comment", "created_at": "2026-04-26T22:00:00"},
        {"id": "r2", "content": "second reply", "reply_type": "system", "created_at": "2026-04-26T22:05:00"},
    ]
    mock_client.get.side_effect = [thread, replies]
    result = runner.invoke(app, ["thread", "show", "t1"])
    assert result.exit_code == 0, result.stdout
    assert "Hello" in result.stdout
    assert "Body of the thread" in result.stdout
    assert "Claude Code" in result.stdout
    assert "first reply" in result.stdout
    assert "second reply" in result.stdout


def test_show_no_replies_flag(mock_client) -> None:
    mock_client.get.side_effect = None
    """--no-replies skips the replies fetch."""
    thread = _seed_thread()
    mock_client.get.return_value = thread
    result = runner.invoke(app, ["thread", "show", "t1", "--no-replies"])
    assert result.exit_code == 0
    # Only one GET call (the thread itself), not two
    assert mock_client.get.call_count == 1
