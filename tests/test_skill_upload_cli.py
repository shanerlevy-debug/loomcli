"""Tests for `weave skill upload / activate / upload-and-activate / versions`.

Shipped in v0.5.2 to close the gap between manifest-driven skill
creation (`weave apply`) and archive uploads (which previously
required curl or direct REST).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from loomcli.cli import app

runner = CliRunner()


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("POWERLOOM_HOME", str(tmp_path))
    monkeypatch.setenv("POWERLOOM_API_BASE_URL", "https://api.test.example.com")
    yield tmp_path


@pytest.fixture
def signed_in(isolated_home):
    (isolated_home / "credentials").write_text("valid-token", encoding="utf-8")
    return isolated_home


@pytest.fixture
def mock_resolver_and_client():
    """Shared fixture — patches PowerloomClient + AddressResolver as used
    inside skill_cmd. Returns the mock client so tests can configure
    per-method return values."""
    with patch("loomcli.commands.skill_cmd.PowerloomClient") as mock_client_cls, patch(
        "loomcli.commands.skill_cmd.AddressResolver"
    ) as mock_resolver_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        mock_resolver = MagicMock()
        # Default: OU resolves and skill is found.
        mock_resolver.ou_path_to_id.return_value = "ou-uuid-123"
        mock_resolver.find_in_ou.return_value = {
            "id": "skill-uuid-456",
            "name": "bespoke-brand-style",
        }
        mock_resolver_cls.return_value = mock_resolver

        yield mock_client, mock_resolver


# ---------------------------------------------------------------------------
# Help + discovery
# ---------------------------------------------------------------------------


def test_weave_skill_subgroup_registered():
    result = runner.invoke(app, ["skill", "--help"])
    assert result.exit_code == 0
    for cmd in ("upload", "activate", "upload-and-activate", "versions"):
        assert cmd in result.stdout


def test_weave_skill_upload_help_shows_address_and_archive():
    result = runner.invoke(app, ["skill", "upload", "--help"])
    assert result.exit_code == 0
    # Typer renders positional args in ARGUMENTS.
    assert "ARCHIVE" in result.stdout.upper() or "ADDRESS" in result.stdout.upper()


# ---------------------------------------------------------------------------
# Address validation
# ---------------------------------------------------------------------------


def test_upload_rejects_relative_address(signed_in, tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"test-bytes")
    result = runner.invoke(
        app, ["skill", "upload", "ou/name", str(archive)]
    )
    # Typer's BadParameter → exit code 2. Message goes to stderr.
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "") + (result.output or "")
    assert "absolute path" in combined.lower()


def test_upload_rejects_too_short_address(signed_in, tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"test-bytes")
    result = runner.invoke(app, ["skill", "upload", "/just-one", str(archive)])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "") + (result.output or "")
    assert "missing" in combined.lower()


# ---------------------------------------------------------------------------
# Authentication gate
# ---------------------------------------------------------------------------


def test_upload_requires_signin(isolated_home, tmp_path):
    # isolated_home only — NOT signed_in.
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"x")
    result = runner.invoke(
        app,
        ["skill", "upload", "/org/ou/my-skill", str(archive)],
    )
    assert result.exit_code == 1
    assert "sign in" in result.stdout.lower() or "login" in result.stdout.lower()


# ---------------------------------------------------------------------------
# upload — happy path
# ---------------------------------------------------------------------------


def test_upload_hits_versions_endpoint_with_multipart(
    signed_in, tmp_path, mock_resolver_and_client
):
    mock_client, _ = mock_resolver_and_client
    mock_client.post_multipart.return_value = {
        "id": "version-uuid-789",
        "name": "bespoke-brand-style",
        "description": "brand style manager",
        "sha256": "deadbeef" * 8,
        "size_bytes": 1024,
    }
    archive = tmp_path / "bespoke-brand-style.zip"
    archive.write_bytes(b"fake-zip-contents")

    result = runner.invoke(
        app,
        [
            "skill",
            "upload",
            "/bespoke-technology/studio/bespoke-brand-style",
            str(archive),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "version-uuid-789" in result.stdout
    # Verify the right endpoint + multipart payload
    mock_client.post_multipart.assert_called_once()
    kwargs = mock_client.post_multipart.call_args.kwargs
    args = mock_client.post_multipart.call_args.args
    assert args[0] == "/skills/skill-uuid-456/versions"
    assert kwargs["file_name"] == "bespoke-brand-style.zip"
    assert kwargs["file_bytes"] == b"fake-zip-contents"
    assert kwargs["content_type"] == "application/zip"
    # Upload alone does NOT activate.
    mock_client.patch.assert_not_called()


def test_upload_tar_gz_content_type(
    signed_in, tmp_path, mock_resolver_and_client
):
    mock_client, _ = mock_resolver_and_client
    mock_client.post_multipart.return_value = {"id": "v1"}
    archive = tmp_path / "skill.tar.gz"
    archive.write_bytes(b"tgz-bytes")

    result = runner.invoke(
        app, ["skill", "upload", "/a/b/c", str(archive)]
    )
    assert result.exit_code == 0, result.stdout
    kwargs = mock_client.post_multipart.call_args.kwargs
    assert kwargs["content_type"] == "application/gzip"


def test_upload_missing_archive_fails_cleanly(signed_in):
    result = runner.invoke(
        app,
        [
            "skill",
            "upload",
            "/a/b/c",
            "/nonexistent/file.zip",
        ],
    )
    # Typer's file validation catches this before our code runs.
    assert result.exit_code != 0


def test_upload_skill_not_found(signed_in, tmp_path, mock_resolver_and_client):
    _, mock_resolver = mock_resolver_and_client
    mock_resolver.find_in_ou.return_value = None
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"x")

    result = runner.invoke(
        app, ["skill", "upload", "/a/b/missing-skill", str(archive)]
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()
    assert "weave apply" in result.stdout.lower()


def test_upload_ou_not_found(signed_in, tmp_path, mock_resolver_and_client):
    from loomcli.manifest.addressing import AddressResolutionError

    _, mock_resolver = mock_resolver_and_client
    mock_resolver.ou_path_to_id.side_effect = AddressResolutionError(
        "OU path '/a/b' not found. Known paths: ['/x']"
    )
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"x")

    result = runner.invoke(
        app, ["skill", "upload", "/a/b/c", str(archive)]
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


def test_activate_patches_current_version_id(
    signed_in, mock_resolver_and_client
):
    mock_client, _ = mock_resolver_and_client
    mock_client.patch.return_value = {"id": "skill-uuid-456"}

    result = runner.invoke(
        app,
        [
            "skill",
            "activate",
            "/a/b/my-skill",
            "version-uuid-to-activate",
        ],
    )
    assert result.exit_code == 0, result.stdout
    mock_client.patch.assert_called_once_with(
        "/skills/skill-uuid-456",
        {"current_version_id": "version-uuid-to-activate"},
    )
    assert "activated" in result.stdout.lower()


# ---------------------------------------------------------------------------
# upload-and-activate
# ---------------------------------------------------------------------------


def test_upload_and_activate_does_both(
    signed_in, tmp_path, mock_resolver_and_client
):
    mock_client, _ = mock_resolver_and_client
    mock_client.post_multipart.return_value = {"id": "version-uuid-new"}
    mock_client.patch.return_value = {"id": "skill-uuid-456"}

    archive = tmp_path / "combo.zip"
    archive.write_bytes(b"combo-bytes")

    result = runner.invoke(
        app,
        [
            "skill",
            "upload-and-activate",
            "/bespoke-technology/studio/bespoke-brand-style",
            str(archive),
        ],
    )
    assert result.exit_code == 0, result.stdout
    mock_client.post_multipart.assert_called_once()
    mock_client.patch.assert_called_once_with(
        "/skills/skill-uuid-456",
        {"current_version_id": "version-uuid-new"},
    )
    assert "version-uuid-new" in result.stdout
    assert "uploaded" in result.stdout.lower()
    assert "activated" in result.stdout.lower()


def test_upload_and_activate_upload_fails(
    signed_in, tmp_path, mock_resolver_and_client
):
    from loomcli.client import PowerloomApiError

    mock_client, _ = mock_resolver_and_client
    mock_client.post_multipart.side_effect = PowerloomApiError(
        400, "invalid SKILL.md frontmatter", method="POST", path="/skills/x/versions"
    )

    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"bad-bytes")

    result = runner.invoke(
        app,
        ["skill", "upload-and-activate", "/a/b/c", str(archive)],
    )
    assert result.exit_code == 1
    assert "upload failed" in result.stdout.lower()
    mock_client.patch.assert_not_called()


def test_upload_and_activate_activation_fails_after_upload(
    signed_in, tmp_path, mock_resolver_and_client
):
    """If upload succeeds but activation fails, surface both facts so
    the user can retry activation without re-uploading."""
    from loomcli.client import PowerloomApiError

    mock_client, _ = mock_resolver_and_client
    mock_client.post_multipart.return_value = {"id": "version-uuid-orphan"}
    mock_client.patch.side_effect = PowerloomApiError(
        500, "db error", method="PATCH", path="/skills/x"
    )

    archive = tmp_path / "a.zip"
    archive.write_bytes(b"x")

    result = runner.invoke(
        app,
        ["skill", "upload-and-activate", "/a/b/c", str(archive)],
    )
    assert result.exit_code == 1
    assert "version-uuid-orphan" in result.stdout
    assert (
        "retry" in result.stdout.lower()
        or "weave skill activate" in result.stdout.lower()
    )


# ---------------------------------------------------------------------------
# versions (list)
# ---------------------------------------------------------------------------


def test_versions_list_empty(signed_in, mock_resolver_and_client):
    mock_client, _ = mock_resolver_and_client
    mock_client.get.return_value = []
    result = runner.invoke(app, ["skill", "versions", "/a/b/c"])
    assert result.exit_code == 0, result.stdout
    assert "no versions" in result.stdout.lower()


def test_versions_list_renders(signed_in, mock_resolver_and_client):
    mock_client, _ = mock_resolver_and_client
    mock_client.get.return_value = [
        {
            "id": "v-1",
            "name": "bespoke-brand-style",
            "sha256": "abc123" * 10,
            "size_bytes": 2048,
            "created_at": "2026-04-24T00:00:00Z",
        },
        {
            "id": "v-2",
            "name": "bespoke-brand-style",
            "sha256": "def456" * 10,
            "size_bytes": 2100,
            "created_at": "2026-04-25T00:00:00Z",
        },
    ]
    result = runner.invoke(
        app, ["skill", "versions", "/a/b/c"], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.stdout
    # Verify API path
    mock_client.get.assert_called_once_with("/skills/skill-uuid-456/versions")
    # Some evidence of content rendered
    combined = result.stdout
    assert "v-1" in combined or "v-2" in combined or "bespoke-brand-style" in combined
