"""Tests for ``loomcli._open.skills_install``.

Sprint skills-mcp-bootstrap-20260430, thread d1b883af.

Mocks the engine fetch and the archive bytes so tests are offline +
deterministic. The actual tarball is built in-process and unpacked
into a tmp_path-rooted worktree.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loomcli._open import skills_install
from loomcli._open.skills_install import (
    SIDECAR_FILENAME,
    SkillInstallResult,
    install_spec_skills,
)
from loomcli.client import PowerloomApiError
from loomcli.schema.launch_spec import LaunchSpec


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _spec_with_skills(skills_payload: list[dict]) -> LaunchSpec:
    base = {
        "schema_version": 1,
        "launch_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-05-01T00:00:00Z",
        "expires_at": "2026-05-01T00:15:00Z",
        "actor": {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "email": "x@y.com",
            "runtime": "claude_code",
        },
        "project": {
            "id": "33333333-3333-3333-3333-333333333333",
            "slug": "powerloom",
            "repo_url": "https://github.com/x/y.git",
            "default_branch": "main",
        },
        "scope": {
            "slug": "cc-test-20260501",
            "branch_base": "main",
            "branch_name": "session/cc-test-20260501",
        },
        "runtime": "claude_code",
        "skills": skills_payload,
        "capabilities": [],
        "clone_auth": {"mode": "server_minted"},
        "mcp_config": {"servers": []},
        "rules_sync": [],
    }
    return LaunchSpec.model_validate(base)


def _build_skill_archive(files: dict[str, str]) -> bytes:
    """Return tar.gz bytes containing the given filename → content map."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _engine_skill_row(name: str, *, sid: str | None = None) -> dict:
    return {
        "id": sid or f"skill-{name}-id",
        "organization_id": "org-1",
        "ou_id": "ou-1",
        "name": name,
        "display_title": name.upper(),
        "description": None,
        "skill_type": "archive",
        "current_version_id": "ver-1",
        "system": False,
        "created_at": "2026-04-01T00:00:00Z",
        "archived_at": None,
    }


@pytest.fixture
def cfg() -> object:
    cfg = MagicMock()
    cfg.api_base_url = "https://api.example.com"
    cfg.access_token = "pat_test"
    cfg.active_profile = "default"
    return cfg


# ---------------------------------------------------------------------------
# install_spec_skills — empty / fresh / idempotent / not-found / failures
# ---------------------------------------------------------------------------


def test_empty_skills_returns_empty_result(cfg, tmp_path) -> None:
    spec = _spec_with_skills([])
    fake_client = MagicMock()
    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert result.installed == []
    assert result.skipped == []
    assert result.failed == []
    fake_client.get.assert_not_called()


def test_fresh_install_writes_archive_and_sidecar(cfg, tmp_path) -> None:
    spec = _spec_with_skills([{"slug": "init", "version": "latest"}])
    archive_bytes = _build_skill_archive(
        {"SKILL.md": "# init\n", "manifest.json": "{}"},
    )

    fake_client = MagicMock()
    fake_client.get.return_value = [_engine_skill_row("init")]
    fake_response = MagicMock()
    fake_response.content = archive_bytes
    fake_response.raise_for_status = MagicMock()
    fake_client._http.get.return_value = fake_response

    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)

    assert result.installed == ["init"]
    assert result.skipped == []
    assert result.failed == []
    target = tmp_path / ".claude" / "skills" / "init"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# init\n"
    assert (target / "manifest.json").read_text(encoding="utf-8") == "{}"
    sidecar = target / SIDECAR_FILENAME
    assert sidecar.read_text(encoding="utf-8") == "init-latest"
    fake_client._http.get.assert_called_once_with(
        "/skills/skill-init-id/archive",
    )


def test_idempotent_when_sidecar_matches(cfg, tmp_path) -> None:
    """Second run with same pin marker → no engine call, skipped."""
    spec = _spec_with_skills([{"slug": "init", "version": "latest"}])
    target = tmp_path / ".claude" / "skills" / "init"
    target.mkdir(parents=True)
    (target / SIDECAR_FILENAME).write_text("init-latest", encoding="utf-8")

    fake_client = MagicMock()
    # /skills lookup runs because the install loop needs the by_name map.
    fake_client.get.return_value = [_engine_skill_row("init")]

    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert result.installed == []
    assert result.skipped == ["init"]
    assert result.failed == []
    # Engine archive endpoint NOT hit.
    fake_client._http.get.assert_not_called()


def test_version_mismatch_reinstalls(cfg, tmp_path) -> None:
    """Existing dir + sidecar 'init-v1' but spec wants 'init-v2' → reinstall."""
    spec = _spec_with_skills([{"slug": "init", "version": "v2"}])
    target = tmp_path / ".claude" / "skills" / "init"
    target.mkdir(parents=True)
    (target / SIDECAR_FILENAME).write_text("init-v1", encoding="utf-8")

    fake_client = MagicMock()
    fake_client.get.return_value = [_engine_skill_row("init")]
    fake_response = MagicMock()
    fake_response.content = _build_skill_archive({"SKILL.md": "v2\n"})
    fake_response.raise_for_status = MagicMock()
    fake_client._http.get.return_value = fake_response

    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert result.installed == ["init"]
    assert (target / SIDECAR_FILENAME).read_text(encoding="utf-8") == "init-v2"


def test_skill_not_in_org_catalog_records_failed(cfg, tmp_path) -> None:
    spec = _spec_with_skills([{"slug": "missing", "version": "latest"}])
    fake_client = MagicMock()
    fake_client.get.return_value = []  # /skills empty
    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert result.installed == []
    assert result.skipped == []
    assert len(result.failed) == 1
    slug, err = result.failed[0]
    assert slug == "missing"
    assert "not found" in err.lower()


def test_engine_lookup_failure_records_per_skill(cfg, tmp_path) -> None:
    """When /skills GET fails, each skill in the spec records the failure."""
    spec = _spec_with_skills([
        {"slug": "init", "version": "latest"},
        {"slug": "review", "version": "latest"},
    ])
    fake_client = MagicMock()
    fake_client.get.side_effect = PowerloomApiError(503, "engine down")
    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert len(result.failed) == 2
    slugs = [f[0] for f in result.failed]
    assert "init" in slugs
    assert "review" in slugs
    fake_client._http.get.assert_not_called()


def test_archive_download_failure_records_failed(cfg, tmp_path) -> None:
    """502 from /skills/{id}/archive (S3 hiccup) → per-skill failure."""
    spec = _spec_with_skills([{"slug": "init", "version": "latest"}])
    fake_client = MagicMock()
    fake_client.get.return_value = [_engine_skill_row("init")]
    fake_client._http.get.side_effect = RuntimeError("S3 hiccup")
    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert result.installed == []
    assert len(result.failed) == 1
    slug, err = result.failed[0]
    assert slug == "init"
    assert "S3" in err or "download" in err


def test_corrupt_archive_records_failed(cfg, tmp_path) -> None:
    spec = _spec_with_skills([{"slug": "init", "version": "latest"}])
    fake_client = MagicMock()
    fake_client.get.return_value = [_engine_skill_row("init")]
    fake_response = MagicMock()
    fake_response.content = b"not a tar.gz"
    fake_response.raise_for_status = MagicMock()
    fake_client._http.get.return_value = fake_response
    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert len(result.failed) == 1
    slug, err = result.failed[0]
    assert slug == "init"
    assert "unpack" in err.lower()


def test_multiple_skills_independently_succeed_or_fail(cfg, tmp_path) -> None:
    """One skill installs, another fails (download error) — both reported."""
    spec = _spec_with_skills([
        {"slug": "init", "version": "latest"},
        {"slug": "broken", "version": "latest"},
    ])
    fake_client = MagicMock()
    fake_client.get.return_value = [
        _engine_skill_row("init", sid="skill-init-id"),
        _engine_skill_row("broken", sid="skill-broken-id"),
    ]

    init_response = MagicMock()
    init_response.content = _build_skill_archive({"SKILL.md": "init\n"})
    init_response.raise_for_status = MagicMock()

    def _fake_get(path: str):
        if path == "/skills/skill-broken-id/archive":
            raise RuntimeError("S3 fetch failure")
        return init_response

    fake_client._http.get.side_effect = _fake_get
    result = install_spec_skills(cfg, spec, tmp_path, client=fake_client)
    assert result.installed == ["init"]
    assert len(result.failed) == 1
    assert result.failed[0][0] == "broken"


# ---------------------------------------------------------------------------
# any_failed property
# ---------------------------------------------------------------------------


def test_any_failed_property() -> None:
    assert SkillInstallResult().any_failed is False
    assert (
        SkillInstallResult(installed=["a"]).any_failed is False
    )
    assert (
        SkillInstallResult(failed=[("a", "x")]).any_failed is True
    )
