from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app


runner = CliRunner()

PROJECT_UUID = "00000000-0000-0000-0000-0000000000aa"
MILESTONE_UUID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def mock_client():
    with patch("loomcli.commands.milestone_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        client.get.side_effect = lambda path, **kw: (
            [{"id": PROJECT_UUID, "slug": "loomcli"}]
            if path == "/projects"
            else []
        )
        yield client


def _seed_milestone(**overrides) -> dict:
    base = {
        "id": MILESTONE_UUID,
        "project_id": PROJECT_UUID,
        "title": "Loomcli onboarding polish",
        "description": "Make setup and tracker hierarchy easier for agents.",
        "target_date": None,
        "status": "open",
        "created_at": "2026-04-27T00:00:00Z",
        "updated_at": "2026-04-27T00:00:00Z",
    }
    base.update(overrides)
    return base


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def test_milestone_subgroup_registered() -> None:
    result = runner.invoke(app, ["milestone", "--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    for cmd in ("ls", "create", "show", "update", "close", "reopen"):
        assert cmd in out


def test_create_resolves_project_and_posts_body(mock_client) -> None:
    created = _seed_milestone()
    mock_client.get.side_effect = [[{"id": PROJECT_UUID, "slug": "loomcli"}]]
    mock_client.post.return_value = created

    result = runner.invoke(
        app,
        [
            "milestone", "create",
            "--project", "loomcli",
            "--title", "Loomcli onboarding polish",
            "--description", "Make setup easier",
            "--target-date", "2026-05-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0, result.output
    path, body = mock_client.post.call_args.args
    assert path == f"/projects/{PROJECT_UUID}/milestones"
    assert body == {
        "title": "Loomcli onboarding polish",
        "description": "Make setup easier",
        "target_date": "2026-05-01T00:00:00Z",
    }


def test_list_renders_table(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "loomcli"}],
        [_seed_milestone(), _seed_milestone(id="44444444-4444-4444-4444-444444444444", title="Packaging", status="closed")],
    ]

    result = runner.invoke(app, ["milestone", "ls", "--project", "loomcli"])

    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "Loomcli onboarding polish" in out
    assert "Packaging" in out


def test_show_resolves_case_insensitive_title(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "loomcli"}],
        [_seed_milestone()],
    ]

    result = runner.invoke(
        app,
        ["milestone", "show", "loomcli onboarding polish", "--project", "loomcli", "--json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output[result.output.find("{"):result.output.rfind("}") + 1])
    assert parsed["id"] == MILESTONE_UUID


def test_update_resolves_title_and_patches(mock_client) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "loomcli"}],
        [_seed_milestone()],
    ]
    mock_client.patch.return_value = _seed_milestone(status="closed")

    result = runner.invoke(
        app,
        ["milestone", "update", "Loomcli onboarding polish", "--project", "loomcli", "--status", "closed"],
    )

    assert result.exit_code == 0, result.output
    path, body = mock_client.patch.call_args.args
    assert path == f"/projects/{PROJECT_UUID}/milestones/{MILESTONE_UUID}"
    assert body == {"status": "closed"}


@pytest.mark.parametrize(("verb", "status"), [("close", "closed"), ("reopen", "open")])
def test_lifecycle_shortcuts_patch_status(mock_client, verb, status) -> None:
    mock_client.get.side_effect = [
        [{"id": PROJECT_UUID, "slug": "loomcli"}],
        [_seed_milestone()],
    ]
    mock_client.patch.return_value = _seed_milestone(status=status)

    result = runner.invoke(
        app,
        ["milestone", verb, "Loomcli onboarding polish", "--project", "loomcli"],
    )

    assert result.exit_code == 0, result.output
    assert mock_client.patch.call_args.args[1] == {"status": status}
