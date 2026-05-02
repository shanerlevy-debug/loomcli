"""Tests for ``loomcli._open.skill_updates``.

Sprint skills-mcp-bootstrap-20260430, thread 647858ec.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loomcli._open.skill_updates import (
    SkillUpdate,
    SkillUpdateCheckResult,
    check_skill_updates,
    format_update_summary,
)
from loomcli._open.skills_install import (
    VERSION_ID_SIDECAR_FILENAME,
    WORKTREE_SKILL_SUBDIR,
)
from loomcli.client import PowerloomApiError


def _seed_installed_skill(
    worktree: Path, slug: str, *, version_id: str
) -> Path:
    """Create a worktree-local skill dir + version-id sidecar."""
    target = worktree / WORKTREE_SKILL_SUBDIR / slug
    target.mkdir(parents=True)
    (target / VERSION_ID_SIDECAR_FILENAME).write_text(version_id, encoding="utf-8")
    return target


def _engine_skill_row(name: str, *, current_version_id: str) -> dict:
    return {
        "id": f"skill-{name}-id",
        "name": name,
        "current_version_id": current_version_id,
        "archived_at": None,
    }


@pytest.fixture
def cfg() -> object:
    cfg = MagicMock()
    cfg.api_base_url = "https://api.example.com"
    cfg.access_token = "pat_test"
    return cfg


# ---------------------------------------------------------------------------
# check_skill_updates — empty / matched / diverged / mixed paths
# ---------------------------------------------------------------------------


def test_no_skills_dir_returns_empty(cfg, tmp_path) -> None:
    fake_client = MagicMock()
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert result.updates == []
    assert result.skipped == []
    assert result.error is None
    fake_client.get.assert_not_called()


def test_skills_dir_exists_but_empty_returns_empty(cfg, tmp_path) -> None:
    (tmp_path / WORKTREE_SKILL_SUBDIR).mkdir(parents=True)
    fake_client = MagicMock()
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert result.updates == []
    fake_client.get.assert_not_called()


def test_no_version_sidecar_skipped_silently(cfg, tmp_path) -> None:
    """Skill dir without the version-id sidecar (legacy install) → skip."""
    legacy = tmp_path / WORKTREE_SKILL_SUBDIR / "legacy"
    legacy.mkdir(parents=True)
    fake_client = MagicMock()
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert result.updates == []
    # Engine /skills isn't even called when we have nothing comparable.
    fake_client.get.assert_not_called()


def test_matched_versions_no_updates(cfg, tmp_path) -> None:
    _seed_installed_skill(tmp_path, "init", version_id="ver-aaaa")
    fake_client = MagicMock()
    fake_client.get.return_value = [
        _engine_skill_row("init", current_version_id="ver-aaaa"),
    ]
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert result.updates == []
    assert result.skipped == []


def test_diverged_version_recorded_as_update(cfg, tmp_path) -> None:
    _seed_installed_skill(tmp_path, "review", version_id="ver-old")
    fake_client = MagicMock()
    fake_client.get.return_value = [
        _engine_skill_row("review", current_version_id="ver-new"),
    ]
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert len(result.updates) == 1
    upd = result.updates[0]
    assert upd.slug == "review"
    assert upd.installed_version_id == "ver-old"
    assert upd.current_version_id == "ver-new"


def test_skill_no_longer_in_catalog_skipped(cfg, tmp_path) -> None:
    """Skill installed locally but absent from /skills now → skip silently."""
    _seed_installed_skill(tmp_path, "deprecated", version_id="ver-xxx")
    fake_client = MagicMock()
    fake_client.get.return_value = []
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert result.updates == []
    assert result.skipped == ["deprecated"]


def test_mixed_up_to_date_and_outdated(cfg, tmp_path) -> None:
    _seed_installed_skill(tmp_path, "init", version_id="ver-init-current")
    _seed_installed_skill(tmp_path, "review", version_id="ver-review-old")
    _seed_installed_skill(tmp_path, "lint", version_id="ver-lint-current")
    fake_client = MagicMock()
    fake_client.get.return_value = [
        _engine_skill_row("init", current_version_id="ver-init-current"),
        _engine_skill_row("review", current_version_id="ver-review-new"),
        _engine_skill_row("lint", current_version_id="ver-lint-current"),
    ]
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    slugs = [u.slug for u in result.updates]
    assert slugs == ["review"]


def test_engine_lookup_failure_records_error(cfg, tmp_path) -> None:
    _seed_installed_skill(tmp_path, "init", version_id="ver-aaaa")
    fake_client = MagicMock()
    fake_client.get.side_effect = PowerloomApiError(503, "engine down")
    result = check_skill_updates(cfg, tmp_path, client=fake_client)
    assert result.updates == []
    assert result.error is not None
    assert "/skills lookup failed" in result.error


# ---------------------------------------------------------------------------
# format_update_summary
# ---------------------------------------------------------------------------


def test_format_summary_empty_returns_none() -> None:
    assert format_update_summary(SkillUpdateCheckResult()) is None


def test_format_summary_single_update() -> None:
    res = SkillUpdateCheckResult(
        updates=[SkillUpdate("review", "v1", "v2")],
    )
    summary = format_update_summary(res)
    assert summary == "1 skill has updates: review"


def test_format_summary_plural_updates() -> None:
    res = SkillUpdateCheckResult(
        updates=[
            SkillUpdate("review", "v1", "v2"),
            SkillUpdate("init", "v1", "v3"),
            SkillUpdate("lint", "v2", "v4"),
        ],
    )
    summary = format_update_summary(res)
    assert summary == "3 skills have updates: review, init, lint"


# ---------------------------------------------------------------------------
# Integration with skills_install — version-id sidecar gets written
# ---------------------------------------------------------------------------


def test_skills_install_writes_version_id_sidecar(cfg, tmp_path) -> None:
    """Confirms thread d1b883af + 647858ec wiring: install lays down both
    sidecars so the resume-update check can use the version-id one."""
    from loomcli._open.skills_install import (
        SIDECAR_FILENAME,
        install_spec_skills,
    )
    from loomcli.schema.launch_spec import LaunchSpec

    spec = LaunchSpec.model_validate({
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
        "skills": [{"slug": "init", "version": "latest"}],
        "capabilities": [],
        "clone_auth": {"mode": "server_minted"},
        "mcp_config": {"servers": []},
        "rules_sync": [],
    })

    # Build a minimal valid tar.gz so unpack succeeds.
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="SKILL.md")
        info.size = len(b"# init\n")
        tar.addfile(info, io.BytesIO(b"# init\n"))

    fake_client = MagicMock()
    fake_client.get.return_value = [
        {
            "id": "skill-init-id",
            "name": "init",
            "current_version_id": "ver-init-current",
            "current_version": None,
            "archived_at": None,
        },
    ]
    fake_response = MagicMock()
    fake_response.content = buf.getvalue()
    fake_response.raise_for_status = MagicMock()
    fake_client._http.get.return_value = fake_response

    install_spec_skills(cfg, spec, tmp_path, client=fake_client)

    target = tmp_path / WORKTREE_SKILL_SUBDIR / "init"
    assert (target / SIDECAR_FILENAME).read_text(encoding="utf-8") == "init-latest"
    assert (
        (target / VERSION_ID_SIDECAR_FILENAME).read_text(encoding="utf-8")
        == "ver-init-current"
    )
