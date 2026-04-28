"""Tests for `weave project ls / show` and `weave get projects`.

Until this command shipped, there was no discovery path: every other
tracker subcommand required you to already know the project slug, and
`weave get projects` errored with `Unknown kind 'projects'`.

Tests are offline (PowerloomClient is patched) — engine behavior is
verified in the Powerloom repo.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.client import PowerloomApiError


runner = CliRunner()


@pytest.fixture
def _seed_projects() -> list[dict]:
    return [
        {
            "id": "00000000-0000-0000-0000-0000000000aa",
            "slug": "powerloom",
            "name": "Powerloom",
            "status": "active",
            "ou_id": None,
            "description": "main",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "00000000-0000-0000-0000-0000000000bb",
            "slug": "loomcli",
            "name": "Loom CLI",
            "status": "active",
            "ou_id": None,
        },
    ]


@pytest.fixture
def mock_project_client(_seed_projects):
    with patch("loomcli.commands.project_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        client.get.side_effect = lambda path, **kw: (
            list(_seed_projects)
            if path == "/projects"
            else (
                next(p for p in _seed_projects if p["id"] == path.rsplit("/", 1)[-1])
                if path.startswith("/projects/")
                else None
            )
        )
        yield client


def test_project_subgroup_registered() -> None:
    result = runner.invoke(app, ["project", "--help"])
    assert result.exit_code == 0
    for cmd in ("ls", "show"):
        assert cmd in result.stdout


def test_project_ls_table(mock_project_client) -> None:
    result = runner.invoke(app, ["project", "ls"])
    assert result.exit_code == 0
    assert "powerloom" in result.stdout
    assert "loomcli" in result.stdout


def test_project_ls_json(mock_project_client) -> None:
    result = runner.invoke(app, ["-o", "json", "project", "ls"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {p["slug"] for p in payload} == {"powerloom", "loomcli"}


def test_project_ls_handles_items_envelope(monkeypatch) -> None:
    """Some envelopes return {items: [...]} — both shapes must work."""
    seed = [{"id": "abc", "slug": "x", "name": "X", "status": "active"}]
    with patch("loomcli.commands.project_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        client.get.return_value = {"items": seed}
        result = runner.invoke(app, ["-o", "json", "project", "ls"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == seed


def test_project_show_by_slug(mock_project_client) -> None:
    result = runner.invoke(app, ["project", "show", "powerloom"])
    assert result.exit_code == 0
    assert "Powerloom" in result.stdout
    assert "00000000-0000-0000-0000-0000000000aa" in result.stdout


def test_project_show_by_uuid(mock_project_client) -> None:
    result = runner.invoke(app, ["project", "show", "00000000-0000-0000-0000-0000000000aa"])
    assert result.exit_code == 0
    assert "Powerloom" in result.stdout


def test_project_show_unknown_slug_lists_available(mock_project_client) -> None:
    result = runner.invoke(app, ["project", "show", "nonesuch"])
    assert result.exit_code == 1
    # Either stdout or stderr is fine; Typer's CliRunner combines them by default
    output = result.stdout + (result.stderr or "")
    assert "powerloom" in output
    assert "loomcli" in output


def test_project_ls_api_error_exits_one() -> None:
    with patch("loomcli.commands.project_cmd.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        client.get.side_effect = PowerloomApiError(500, "boom")
        result = runner.invoke(app, ["project", "ls"])
    assert result.exit_code == 1


def test_get_projects_kind_works() -> None:
    """`weave get projects` previously errored with 'Unknown kind'. The new
    _LISTABLE entry routes it through the existing get_command path."""
    seed = [{"id": "abc", "slug": "x", "name": "X", "status": "active"}]
    with patch("loomcli.commands.get.PowerloomClient") as mock_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        mock_cls.return_value = client
        client.get.return_value = seed
        result = runner.invoke(app, ["get", "projects"])
    assert result.exit_code == 0
    assert "x" in result.stdout
