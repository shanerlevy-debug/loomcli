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
    for cmd in ("create", "pluck", "reply", "done", "close", "wont-do", "list", "show", "update", "my-work"):
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
    mock_client.post.return_value = {"id": "r1", "thread_id": "11111111-1111-1111-1111-111111111111", "content": "hi"}
    result = runner.invoke(app, ["thread", "reply", "11111111-1111-1111-1111-111111111111", "hi"])
    assert result.exit_code == 0
    args, _ = mock_client.post.call_args
    assert args[0] == "/threads/11111111-1111-1111-1111-111111111111/replies"
    assert args[1]["content"] == "hi"
    assert args[1]["reply_type"] == "comment"


def test_reply_requires_content(mock_client) -> None:
    """No content + no --from-stdin → exit 2."""
    result = runner.invoke(app, ["thread", "reply", "11111111-1111-1111-1111-111111111111"])
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
    result = runner.invoke(app, ["thread", verb, "11111111-1111-1111-1111-111111111111"])
    assert result.exit_code == 0, result.stdout
    args, _ = mock_client.patch.call_args
    assert args[0] == "/threads/11111111-1111-1111-1111-111111111111"
    assert args[1] == {"status": expected_status}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_combines_fields(mock_client) -> None:
    mock_client.patch.return_value = _seed_thread(status="review", priority="critical")
    result = runner.invoke(
        app,
        ["thread", "update", "11111111-1111-1111-1111-111111111111", "--status", "review", "--priority", "critical"],
    )
    assert result.exit_code == 0
    args, _ = mock_client.patch.call_args
    body = args[1]
    assert body == {"status": "review", "priority": "critical"}


def test_update_assigned_to_empty_string_unassigns(mock_client) -> None:
    """--assigned-to '' → null in body (unassign)."""
    mock_client.patch.return_value = _seed_thread()
    result = runner.invoke(app, ["thread", "update", "11111111-1111-1111-1111-111111111111", "--assigned-to", ""])
    assert result.exit_code == 0
    args, _ = mock_client.patch.call_args
    assert args[1] == {"assigned_to": None}


def test_update_invalid_status_rejected(mock_client) -> None:
    result = runner.invoke(app, ["thread", "update", "11111111-1111-1111-1111-111111111111", "--status", "bogus"])
    assert result.exit_code == 2


def test_update_no_fields_short_circuits(mock_client) -> None:
    result = runner.invoke(app, ["thread", "update", "11111111-1111-1111-1111-111111111111"])
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
    result = runner.invoke(app, ["thread", "show", "11111111-1111-1111-1111-111111111111"])
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
    result = runner.invoke(app, ["thread", "show", "11111111-1111-1111-1111-111111111111", "--no-replies"])
    assert result.exit_code == 0
    # Only one GET call (the thread itself), not two
    assert mock_client.get.call_count == 1


# ---------------------------------------------------------------------------
# W1.5.1 — slug resolution
# ---------------------------------------------------------------------------


def test_resolve_thread_uuid_passthrough(mock_client) -> None:
    """A UUID arg skips slug lookup entirely — no /projects fetch, no
    /by-slug fetch."""
    mock_client.patch.return_value = _seed_thread(status="done")
    result = runner.invoke(
        app,
        ["thread", "done", "11111111-1111-1111-1111-111111111111"],
    )
    assert result.exit_code == 0, result.stdout
    # The PATCH call should target the UUID directly
    args, _ = mock_client.patch.call_args
    assert args[0] == "/threads/11111111-1111-1111-1111-111111111111"
    # And no /projects fetch happened (mock_client.get.side_effect would
    # only have been called for /projects, but it wasn't called at all)
    assert mock_client.get.call_count == 0


def test_resolve_thread_bare_slug_uses_default_project(mock_client) -> None:
    """A bare slug like 'ki-004' resolves via default project 'powerloom'."""
    resolved = _seed_thread(id="22222222-2222-2222-2222-222222222222")
    # First GET = project list, second GET = by-slug lookup
    mock_client.get.side_effect = [
        [{"id": "00000000-0000-0000-0000-0000000000aa", "slug": "powerloom"}],
        resolved,
    ]
    mock_client.patch.return_value = _seed_thread(
        id="22222222-2222-2222-2222-222222222222", status="done",
    )
    result = runner.invoke(app, ["thread", "done", "ki-004"])
    assert result.exit_code == 0, result.stdout
    # PATCH targeted the resolved UUID, not the slug
    args, _ = mock_client.patch.call_args
    assert args[0] == "/threads/22222222-2222-2222-2222-222222222222"
    # Slug-lookup endpoint was hit
    by_slug_calls = [
        c for c in mock_client.get.call_args_list
        if c.args and "/by-slug/ki-004" in c.args[0]
    ]
    assert len(by_slug_calls) == 1


def test_resolve_thread_project_colon_slug(mock_client) -> None:
    """`proj:slug` form picks the project explicitly."""
    resolved = _seed_thread(id="33333333-3333-3333-3333-333333333333")
    mock_client.get.side_effect = [
        [{"id": "p2-uuid", "slug": "loomcli"}],
        resolved,
    ]
    mock_client.patch.return_value = _seed_thread(
        id="33333333-3333-3333-3333-333333333333", status="done",
    )
    result = runner.invoke(app, ["thread", "done", "loomcli:t1-foo"])
    assert result.exit_code == 0, result.stdout
    # By-slug call was made against the loomcli project
    by_slug_calls = [
        c for c in mock_client.get.call_args_list
        if c.args and "/projects/p2-uuid/threads/by-slug/t1-foo" in c.args[0]
    ]
    assert len(by_slug_calls) == 1


def test_resolve_thread_invalid_slug_shape_rejected(mock_client) -> None:
    """Slugs with uppercase / spaces / leading hyphen → exit 2."""
    result = runner.invoke(app, ["thread", "done", "INVALID SLUG"])
    assert result.exit_code == 2
    assert "Invalid" in result.stdout or "invalid" in result.stdout.lower()


def test_resolve_thread_404_friendly_message(mock_client) -> None:
    """404 from by-slug renders a project + slug specific error."""
    mock_client.get.side_effect = [
        [{"id": "p1", "slug": "powerloom"}],
        PowerloomApiError(404, "not found"),
    ]
    # Make the 2nd `get` raise — side_effect with mixed value/exception
    def _get(path, **kw):
        if path == "/projects":
            return [{"id": "p1", "slug": "powerloom"}]
        raise PowerloomApiError(404, "not found")
    mock_client.get.side_effect = _get
    result = runner.invoke(app, ["thread", "done", "missing-slug"])
    assert result.exit_code == 1
    out = result.stdout.lower()
    assert "no thread" in out and "missing-slug" in out


# === PR #25: my-work watch tests ===

def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = "fake-token"
    cfg.api_base_url = "https://api.powerloom.org"
    cfg.request_timeout_seconds = 30
    return cfg


def test_thread_help_lists_my_work():
    result = runner.invoke(app, ["thread", "--help"])
    assert result.exit_code == 0
    assert "my-work" in result.stdout


@patch("loomcli.commands.thread_cmd.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_thread_my_work_renders_table(
    mock_load_cfg,
    mock_client_cls,
):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = [
        {
            "id": "thread-1",
            "sequence_number": 31,
            "status": "open",
            "priority": "high",
            "title": "Fix loom MCP stdio packaging",
            "updated_at": "2026-04-26T12:00:00Z",
        }
    ]
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["thread", "my-work", "--status", "open"])

    assert result.exit_code == 0, result.stdout
    assert "Fix loom MCP stdio packaging" in result.stdout
    client.get.assert_called_once_with("/threads/my-work", limit=50, status="open")


@patch("loomcli.commands.thread_cmd.PowerloomClient")
@patch("loomcli.commands.agent_cmd.load_runtime_config")
def test_thread_my_work_watch_once_prints_summary(
    mock_load_cfg,
    mock_client_cls,
):
    mock_load_cfg.return_value = _cfg()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = [
        {
            "sequence_number": 31,
            "status": "open",
            "priority": "high",
            "title": "Fix loom MCP stdio packaging",
            "updated_at": "2026-04-26T12:00:00Z",
        },
        {
            "sequence_number": 44,
            "status": "blocked",
            "priority": "medium",
            "title": "Default behavior on new sub-principal",
            "updated_at": "2026-04-26T12:01:00Z",
        },
    ]
    mock_client_cls.return_value = client

    result = runner.invoke(app, ["thread", "my-work", "--watch", "--once"])

    assert result.exit_code == 0, result.stdout
    assert "my-work total=2" in result.stdout
    assert "blocked=1" in result.stdout
    assert "open=1" in result.stdout


# ---------------------------------------------------------------------------
# W1.3 / Friendly-names Layer 4 — auto-stamp session_attribution
# ---------------------------------------------------------------------------


def _attribution_test_subprincipal() -> dict:
    return {
        "id": "sp-uuid-aaaa",
        "principal_id": "principal-uuid-bbbb",
        "user_id": "user-uuid-cccc",
        "name": "Test Claude Code Session",
        "client_kind": "claude-code",
    }


def test_create_no_env_no_attribution(monkeypatch, mock_client) -> None:
    """When POWERLOOM_ACTIVE_SUBPRINCIPAL_ID is unset, no extra calls + no
    attribution stamping happens. Pure backward compat for direct human
    callers who haven't opted in."""
    monkeypatch.delenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", raising=False)
    mock_client.get.side_effect = [[{"id": "p1", "slug": "powerloom"}]]
    mock_client.post.return_value = _seed_thread()
    result = runner.invoke(app, ["thread", "create", "--title", "x"])
    assert result.exit_code == 0, result.stdout
    # No /me/agents/<id> lookup because env var is unset
    assert not any(
        "/me/agents/" in c.args[0] for c in mock_client.get.call_args_list if c.args
    )
    # No PATCH (no stamp-then-refresh dance)
    assert not mock_client.patch.called


def test_create_with_env_stamps_attribution(monkeypatch, mock_client) -> None:
    """When POWERLOOM_ACTIVE_SUBPRINCIPAL_ID is set, the CLI fetches the
    sub-principal + PATCHes the new thread with session_attribution metadata."""
    monkeypatch.setenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "sp-uuid-aaaa")
    sp = _attribution_test_subprincipal()
    created = _seed_thread()
    # client.get is called for: 1. /projects (slug→UUID), 2. /me/agents/<id>.
    # The PATCH return value is what the caller uses (no re-fetch needed).
    mock_client.get.side_effect = [
        [{"id": "p1", "slug": "powerloom"}],
        sp,
    ]
    mock_client.post.return_value = created
    mock_client.patch.return_value = {
        **created,
        "metadata_json": {"session_attribution": {"subprincipal_name": sp["name"]}},
    }

    result = runner.invoke(app, ["thread", "create", "--title", "x"])
    assert result.exit_code == 0, result.stdout

    # /me/agents/<id> lookup happened
    me_agents_calls = [c for c in mock_client.get.call_args_list if c.args and "/me/agents/" in c.args[0]]
    assert len(me_agents_calls) == 1
    assert "sp-uuid-aaaa" in me_agents_calls[0].args[0]

    # PATCH on /threads/<id> with session_attribution metadata
    assert mock_client.patch.called
    patch_args, _ = mock_client.patch.call_args
    assert patch_args[0].startswith("/threads/")
    metadata = patch_args[1]["metadata_json"]
    assert "session_attribution" in metadata
    sa = metadata["session_attribution"]
    assert sa["subprincipal_id"] == "sp-uuid-aaaa"
    assert sa["subprincipal_name"] == "Test Claude Code Session"
    assert sa["client_kind"] == "claude-code"
    assert sa["parent_user_id"] == "user-uuid-cccc"
    assert "stamped_at" in sa


def test_create_no_attribution_flag_skips_stamping(monkeypatch, mock_client) -> None:
    """--no-attribution opts out even when env var is set."""
    monkeypatch.setenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "sp-uuid-aaaa")
    mock_client.get.side_effect = [[{"id": "p1", "slug": "powerloom"}]]
    mock_client.post.return_value = _seed_thread()
    result = runner.invoke(app, ["thread", "create", "--title", "x", "--no-attribution"])
    assert result.exit_code == 0, result.stdout
    # No /me/agents fetch + no PATCH because --no-attribution short-circuits
    assert not any("/me/agents/" in c.args[0] for c in mock_client.get.call_args_list if c.args)
    assert not mock_client.patch.called


def test_create_subprincipal_lookup_failure_warns_but_succeeds(monkeypatch, mock_client) -> None:
    """If /me/agents/<id> fails (404, 403, network), the thread is still
    created without attribution + a warning prints. Best-effort, never blocks
    the create."""
    monkeypatch.setenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "sp-stale")
    mock_client.get.side_effect = [
        [{"id": "p1", "slug": "powerloom"}],
        PowerloomApiError(404, "HTTP 404 GET /me/agents/sp-stale: not found"),
    ]
    mock_client.post.return_value = _seed_thread()
    result = runner.invoke(app, ["thread", "create", "--title", "x"])
    assert result.exit_code == 0, result.stdout
    # Warning printed
    assert "could not fetch sub-principal" in result.stdout.lower() or "warning" in result.stdout.lower()
    # PATCH NOT called (no attribution stamped)
    assert not mock_client.patch.called


def test_reply_with_env_stamps_attribution_inline(monkeypatch, mock_client) -> None:
    """`weave thread reply` stamps session_attribution into the reply's own
    metadata_json at create time (no separate PATCH needed because
    ReplyCreate accepts metadata_json directly)."""
    monkeypatch.setenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "sp-uuid-aaaa")
    sp = _attribution_test_subprincipal()
    mock_client.get.side_effect = [sp]  # /me/agents/<id> lookup
    mock_client.post.return_value = {"id": "r1", "thread_id": "11111111-1111-1111-1111-111111111111", "content": "hi"}

    result = runner.invoke(app, ["thread", "reply", "11111111-1111-1111-1111-111111111111", "decision: option A"])
    assert result.exit_code == 0, result.stdout
    # POST body carries metadata_json.session_attribution
    args, _ = mock_client.post.call_args
    body = args[1]
    assert body["content"] == "decision: option A"
    assert "metadata_json" in body
    sa = body["metadata_json"]["session_attribution"]
    assert sa["subprincipal_id"] == "sp-uuid-aaaa"
    assert sa["subprincipal_name"] == "Test Claude Code Session"


def test_reply_no_env_skips_stamping(monkeypatch, mock_client) -> None:
    """No env -> no metadata_json on the reply body. Direct human-call path
    unchanged."""
    monkeypatch.delenv("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", raising=False)
    mock_client.post.return_value = {"id": "r1", "thread_id": "11111111-1111-1111-1111-111111111111", "content": "hi"}
    result = runner.invoke(app, ["thread", "reply", "11111111-1111-1111-1111-111111111111", "hi"])
    assert result.exit_code == 0
    args, _ = mock_client.post.call_args
    body = args[1]
    assert "metadata_json" not in body


# ---------------------------------------------------------------------------
# W1.5.3 — `weave thread tree` / `sprint-tree` / `orphans`
# ---------------------------------------------------------------------------


def _tree_payload(thread_id="11111111-1111-1111-1111-111111111111", title="Root", slug="ki-001", children=None):
    return {
        "thread": {
            "id": thread_id, "title": title, "slug": slug,
            "status": "open", "priority": "high",
        },
        "depth": 0,
        "children": children or [],
        "truncated_at_depth": False,
    }


def test_thread_tree_renders(mock_client) -> None:
    """`weave thread tree <uuid>` renders root + children."""
    child_id = "22222222-2222-2222-2222-222222222222"
    payload = _tree_payload(
        children=[{
            "thread": {
                "id": child_id, "title": "Child A", "slug": "ki-002",
                "status": "open", "priority": "medium",
            },
            "depth": 1,
            "children": [],
            "truncated_at_depth": False,
        }],
    )
    mock_client.get.side_effect = [payload]
    result = runner.invoke(app, ["thread", "tree", "11111111-1111-1111-1111-111111111111"])
    assert result.exit_code == 0, result.stdout
    assert "ki-001" in result.stdout
    assert "ki-002" in result.stdout
    # The GET was hit on the tree endpoint
    call = mock_client.get.call_args
    assert "/threads/11111111-1111-1111-1111-111111111111/tree" in call.args[0]


def test_thread_tree_truncated_marker(mock_client) -> None:
    """`truncated_at_depth=True` renders as (more...)."""
    payload = _tree_payload(
        children=[{
            "thread": {
                "id": "33333333-3333-3333-3333-333333333333",
                "title": "Has hidden kids",
                "slug": "deep-1",
                "status": "in_progress",
                "priority": "medium",
            },
            "depth": 1,
            "children": [],
            "truncated_at_depth": True,
        }],
    )
    mock_client.get.side_effect = [payload]
    result = runner.invoke(app, ["thread", "tree", "11111111-1111-1111-1111-111111111111"])
    assert result.exit_code == 0
    assert "more" in result.stdout.lower()


def test_thread_tree_max_depth_passed_to_api(mock_client) -> None:
    mock_client.get.side_effect = [_tree_payload()]
    result = runner.invoke(
        app, ["thread", "tree", "11111111-1111-1111-1111-111111111111", "--max-depth", "3"],
    )
    assert result.exit_code == 0
    call = mock_client.get.call_args
    # max_depth comes through as a kwarg
    assert call.kwargs.get("max_depth") == 3


def test_sprint_tree_rejects_non_uuid(mock_client) -> None:
    """Sprint slug-resolution isn't shipped yet — non-UUID args exit 2."""
    result = runner.invoke(app, ["thread", "sprint-tree", "not-a-uuid"])
    assert result.exit_code == 2


def test_sprint_tree_renders_top_level(mock_client) -> None:
    sprint_uuid = "99999999-9999-9999-9999-999999999999"
    payload = {
        "sprint": {
            "id": sprint_uuid, "name": "v064 cleanup",
            "slug": "v064", "status": "active",
            "project_id": "p1",
            "created_at": "2026-04-26T00:00:00Z",
            "updated_at": "2026-04-26T00:00:00Z",
        },
        "trees": [_tree_payload(), _tree_payload(thread_id="55555555-5555-5555-5555-555555555555", title="Other root", slug="ki-007")],
    }
    mock_client.get.side_effect = [payload]
    result = runner.invoke(app, ["thread", "sprint-tree", sprint_uuid])
    assert result.exit_code == 0, result.stdout
    assert "v064 cleanup" in result.stdout
    assert "ki-001" in result.stdout
    assert "ki-007" in result.stdout


def test_orphans_lists_open_threads(mock_client) -> None:
    """`weave thread orphans` lists threads with no parent + no sprint."""
    mock_client.get.side_effect = [
        [{"id": "p1", "slug": "powerloom"}],
        [
            {"id": "t1", "title": "Loose thread A", "slug": "ki-100", "status": "open", "priority": "high"},
            {"id": "t2", "title": "Loose thread B", "slug": "ki-101", "status": "in_progress", "priority": "medium"},
        ],
    ]
    result = runner.invoke(app, ["thread", "orphans"])
    assert result.exit_code == 0, result.stdout
    assert "ki-100" in result.stdout
    assert "ki-101" in result.stdout
    # Default --include-done is False → not in the params
    last_call = mock_client.get.call_args_list[-1]
    assert "/orphans" in last_call.args[0]
    assert last_call.kwargs.get("include_done") is None


def test_orphans_include_done_flag(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": "p1", "slug": "powerloom"}],
        [],
    ]
    result = runner.invoke(app, ["thread", "orphans", "--include-done"])
    assert result.exit_code == 0
    last_call = mock_client.get.call_args_list[-1]
    assert last_call.kwargs.get("include_done") is True
