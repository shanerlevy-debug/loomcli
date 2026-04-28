"""Tests for `weave sprint …` subcommands.

Mirrors the test_thread_cmd pattern: patches PowerloomClient + load_runtime_config
so the suite is fully offline. Engine round-trip is verified by Powerloom's
test_tracker.TestSprints / TestThreadDependencies; these tests only verify
CLI argument parsing, body shape, slug-resolution flow, and output rendering.
"""
from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomApiError


runner = CliRunner()

PROJECT_UUID = "00000000-0000-0000-0000-0000000000aa"
SPRINT_UUID = "11111111-1111-1111-1111-111111111111"
THREAD_UUID = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Patch PowerloomClient as used inside sprint_cmd. Default GET routes
    /projects to a single 'powerloom' project; everything else is per-test."""
    with patch("loomcli.commands.sprint_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        client.get.side_effect = lambda path, **kw: (
            [{"id": PROJECT_UUID, "slug": "powerloom"}]
            if path == "/projects" else None
        )
        yield client


def _seed_sprint(**overrides) -> dict:
    base = {
        "id": SPRINT_UUID,
        "project_id": PROJECT_UUID,
        "slug": "v064",
        "name": "v064 cleanup",
        "description": None,
        "status": "planned",
        "start_date": None,
        "end_date": None,
        "goal": None,
        "created_by": "u1",
        "metadata_json": None,
        "created_at": "2026-04-26T00:00:00Z",
        "updated_at": "2026-04-26T00:00:00Z",
        "closed_at": None,
    }
    base.update(overrides)
    return base


def _seed_thread(**overrides) -> dict:
    base = {
        "id": THREAD_UUID,
        "title": "Test thread",
        "slug": "ki-004",
        "status": "open",
        "priority": "high",
    }
    base.update(overrides)
    return base


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ---------------------------------------------------------------------------
# Help + discovery
# ---------------------------------------------------------------------------


def test_sprint_subgroup_registered() -> None:
    result = runner.invoke(app, ["sprint", "--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    for cmd in (
        "create", "list", "show", "update",
        "activate", "complete", "archive",
        "delete", "add-thread", "remove-thread", "threads",
    ):
        assert cmd in out, f"subcommand {cmd!r} not in --help output"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_basic(mock_client) -> None:
    """Resolves project slug → UUID, posts body, prints summary."""
    created = _seed_sprint(name="v064 cleanup")
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
    ]
    mock_client.post.return_value = created

    result = runner.invoke(
        app,
        ["sprint", "create", "--project", "powerloom", "--name", "v064 cleanup"],
    )
    assert result.exit_code == 0, result.output
    args, _ = mock_client.post.call_args
    path, body = args
    assert path == f"/projects/{PROJECT_UUID}/sprints"
    assert body["name"] == "v064 cleanup"
    assert body["status"] == "planned"
    assert _strip_ansi(result.output).find("Sprint created") != -1


def test_create_with_dates_and_slug(mock_client) -> None:
    """All optional fields land in the POST body when passed."""
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
    ]
    mock_client.post.return_value = _seed_sprint()
    result = runner.invoke(
        app,
        [
            "sprint", "create",
            "--name", "v065 phase",
            "--slug", "v065",
            "--description", "Ship hosted MCP",
            "--start-date", "2026-05-01",
            "--end-date", "2026-05-15",
            "--goal", "Land Phase 35",
            "--status", "active",
        ],
    )
    assert result.exit_code == 0, result.output
    body = mock_client.post.call_args.args[1]
    assert body["slug"] == "v065"
    assert body["start_date"] == "2026-05-01"
    assert body["end_date"] == "2026-05-15"
    assert body["goal"] == "Land Phase 35"
    assert body["description"] == "Ship hosted MCP"
    assert body["status"] == "active"


def test_create_invalid_status_short_circuits(mock_client) -> None:
    result = runner.invoke(
        app,
        ["sprint", "create", "--name", "x", "--status", "BOGUS"],
    )
    assert result.exit_code == 2
    assert "Invalid status" in _strip_ansi(result.output)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_renders_table(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
        [
            _seed_sprint(slug="v064", status="active"),
            _seed_sprint(slug="v065", name="v065 phase", status="planned", id="33333333-3333-3333-3333-333333333333"),
        ],
    ]
    result = runner.invoke(app, ["sprint", "list"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    assert "v064" in out
    assert "v065" in out
    assert "2 sprint" in out


def test_list_filters_by_status(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
        [],
    ]
    result = runner.invoke(app, ["sprint", "list", "--status", "active"])
    assert result.exit_code == 0
    last_call = mock_client.get.call_args_list[-1]
    assert last_call.kwargs.get("status") == "active"


# ---------------------------------------------------------------------------
# slug resolution
# ---------------------------------------------------------------------------


def test_show_uuid_passthrough(mock_client) -> None:
    """A UUID arg skips slug lookup entirely."""
    mock_client.get.side_effect = [_seed_sprint()]
    result = runner.invoke(app, ["sprint", "show", SPRINT_UUID])
    assert result.exit_code == 0, result.output
    # Only one GET (the sprint itself), no /projects lookup
    assert mock_client.get.call_count == 1
    assert mock_client.get.call_args_list[-1].args[0] == f"/sprints/{SPRINT_UUID}"


def test_show_bare_slug_uses_default_project(mock_client) -> None:
    """`weave sprint show v064` → /projects lookup → /by-slug → /sprints/<uuid>."""
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],   # project resolution
        _seed_sprint(),                                # by-slug lookup
        _seed_sprint(),                                # /sprints/<uuid> show
    ]
    result = runner.invoke(app, ["sprint", "show", "v064"])
    assert result.exit_code == 0, result.output
    paths = [c.args[0] for c in mock_client.get.call_args_list]
    assert any("/by-slug/v064" in p for p in paths)
    assert any(f"/sprints/{SPRINT_UUID}" in p for p in paths)


def test_show_project_colon_slug(mock_client) -> None:
    """`weave sprint show loomcli:v064` resolves loomcli project explicitly."""
    mock_client.get.side_effect = [
        [{"id": "loomcli-uuid", "slug": "loomcli"}],
        _seed_sprint(),
        _seed_sprint(),
    ]
    result = runner.invoke(app, ["sprint", "show", "loomcli:v064"])
    assert result.exit_code == 0, result.output
    paths = [c.args[0] for c in mock_client.get.call_args_list]
    assert any("/projects/loomcli-uuid/sprints/by-slug/v064" in p for p in paths)


def test_show_invalid_slug_shape_rejected(mock_client) -> None:
    result = runner.invoke(app, ["sprint", "show", "INVALID SLUG"])
    assert result.exit_code == 2
    assert "Invalid" in _strip_ansi(result.output)


def test_show_slug_404_friendly_message(mock_client) -> None:
    def _get(path, **kw):
        if path == "/projects":
            return [{"id": PROJECT_UUID, "slug": "powerloom"}]
        raise PowerloomApiError(404, "not found")
    mock_client.get.side_effect = _get
    result = runner.invoke(app, ["sprint", "show", "missing-slug"])
    assert result.exit_code == 1
    out = _strip_ansi(result.output).lower()
    assert "no sprint" in out and "missing-slug" in out


# ---------------------------------------------------------------------------
# lifecycle: activate / complete / archive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,expected_status",
    [
        ("activate", "active"),
        ("complete", "completed"),
        ("archive", "archived"),
    ],
)
def test_lifecycle_verbs_patch_correct_status(mock_client, verb, expected_status) -> None:
    mock_client.patch.return_value = _seed_sprint(status=expected_status)
    result = runner.invoke(app, ["sprint", verb, SPRINT_UUID])
    assert result.exit_code == 0, result.output
    args, _ = mock_client.patch.call_args
    assert args[0] == f"/sprints/{SPRINT_UUID}"
    assert args[1] == {"status": expected_status}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_combines_fields(mock_client) -> None:
    mock_client.patch.return_value = _seed_sprint(name="renamed", status="active")
    result = runner.invoke(
        app,
        ["sprint", "update", SPRINT_UUID, "--name", "renamed", "--status", "active"],
    )
    assert result.exit_code == 0, result.output
    body = mock_client.patch.call_args.args[1]
    assert body == {"name": "renamed", "status": "active"}


def test_update_no_fields_short_circuits(mock_client) -> None:
    result = runner.invoke(app, ["sprint", "update", SPRINT_UUID])
    assert result.exit_code == 2
    assert "No fields" in _strip_ansi(result.output) or "no fields" in _strip_ansi(result.output).lower()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_requires_yes(mock_client) -> None:
    """Default refuses without --yes."""
    result = runner.invoke(app, ["sprint", "delete", SPRINT_UUID])
    assert result.exit_code == 2
    assert "yes" in _strip_ansi(result.output).lower()
    assert not mock_client.delete.called


def test_delete_with_yes_proceeds(mock_client) -> None:
    mock_client.delete.return_value = None
    result = runner.invoke(app, ["sprint", "delete", SPRINT_UUID, "--yes"])
    assert result.exit_code == 0, result.output
    assert mock_client.delete.called
    assert mock_client.delete.call_args.args[0] == f"/sprints/{SPRINT_UUID}"


# ---------------------------------------------------------------------------
# membership
# ---------------------------------------------------------------------------


def test_add_thread_resolves_both_refs(mock_client) -> None:
    """Sprint slug AND thread slug both resolved via /by-slug lookups."""
    sprint_obj = _seed_sprint()
    thread_obj = _seed_thread()
    # Order: sprint /projects, sprint /by-slug, thread /projects (cached but
    # still a fetch since no module-level cache), thread /by-slug, then POST.
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
        sprint_obj,
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
        thread_obj,
    ]
    mock_client.post.return_value = thread_obj
    result = runner.invoke(app, ["sprint", "add-thread", "v064", "ki-004"])
    assert result.exit_code == 0, result.output
    args, _ = mock_client.post.call_args
    assert args[0] == f"/sprints/{SPRINT_UUID}/threads"
    assert args[1] == {"thread_id": THREAD_UUID}


def test_add_thread_uuid_passthrough_skips_lookup(mock_client) -> None:
    """When both args are UUIDs, no /projects fetch happens."""
    mock_client.post.return_value = _seed_thread()
    result = runner.invoke(
        app, ["sprint", "add-thread", SPRINT_UUID, THREAD_UUID],
    )
    assert result.exit_code == 0, result.output
    assert mock_client.get.call_count == 0
    args, _ = mock_client.post.call_args
    assert args[0] == f"/sprints/{SPRINT_UUID}/threads"


def test_remove_thread_uuid_passthrough(mock_client) -> None:
    mock_client.delete.return_value = None
    result = runner.invoke(
        app, ["sprint", "remove-thread", SPRINT_UUID, THREAD_UUID],
    )
    assert result.exit_code == 0, result.output
    assert mock_client.delete.call_args.args[0] == f"/sprints/{SPRINT_UUID}/threads/{THREAD_UUID}"


def test_threads_renders_table(mock_client) -> None:
    mock_client.get.side_effect = [
        [
            _seed_thread(slug="ki-004", priority="high"),
            _seed_thread(slug="ki-005", id="55555555-5555-5555-5555-555555555555", title="Other", priority="medium"),
        ],
    ]
    result = runner.invoke(app, ["sprint", "threads", SPRINT_UUID])
    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "ki-004" in out
    assert "ki-005" in out
    assert "2 thread" in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_show_json_output(mock_client) -> None:
    mock_client.get.side_effect = [_seed_sprint(slug="v064")]
    result = runner.invoke(app, ["-o", "json", "sprint", "show", SPRINT_UUID])
    assert result.exit_code == 0
    # Find the JSON blob (test runner can prefix with extra whitespace)
    out = result.output.strip()
    parsed = json.loads(out[out.find("{"):out.rfind("}") + 1])
    assert parsed["slug"] == "v064"


# ---------------------------------------------------------------------------
# Sprint-under-milestone (Powerloom #165 pair, migration 0068)
# ---------------------------------------------------------------------------


MILESTONE_UUID = "33333333-3333-3333-3333-333333333333"


def test_create_with_milestone_passes_uuid_in_body(mock_client) -> None:
    """`weave sprint create --milestone <uuid>` puts milestone_id in POST body."""
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
    ]
    mock_client.post.return_value = _seed_sprint()
    result = runner.invoke(
        app,
        [
            "sprint", "create",
            "--name", "Test sprint",
            "--milestone", MILESTONE_UUID,
        ],
    )
    assert result.exit_code == 0, result.output
    body = mock_client.post.call_args.args[1]
    assert body["milestone_id"] == MILESTONE_UUID


def test_create_milestone_must_be_uuid(mock_client) -> None:
    """Non-UUID --milestone exits 2 with a slug-resolution-not-shipped hint."""
    result = runner.invoke(
        app,
        [
            "sprint", "create",
            "--name", "Test sprint",
            "--milestone", "msp",  # slug, not UUID
        ],
    )
    assert result.exit_code == 2
    assert "uuid" in result.output.lower()


def test_list_with_milestone_filter_passes_param(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "powerloom"}],
        [],
    ]
    result = runner.invoke(
        app, ["sprint", "list", "--milestone", MILESTONE_UUID],
    )
    assert result.exit_code == 0, result.output
    last_call = mock_client.get.call_args_list[-1]
    assert last_call.kwargs.get("milestone_id") == MILESTONE_UUID


def test_update_with_milestone_attaches(mock_client) -> None:
    mock_client.patch.return_value = _seed_sprint()
    result = runner.invoke(
        app,
        ["sprint", "update", SPRINT_UUID, "--milestone", MILESTONE_UUID],
    )
    assert result.exit_code == 0, result.output
    body = mock_client.patch.call_args.args[1]
    assert body == {"milestone_id": MILESTONE_UUID}
