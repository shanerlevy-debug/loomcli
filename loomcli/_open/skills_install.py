"""Skill install for ``weave open`` — pull spec.skills into the worktree.

Sprint skills-mcp-bootstrap-20260430, thread d1b883af.

For each entry in ``spec.skills`` we:
  1. Look up the skill by name (slug) in the org's skill list.
  2. Download the current archive via ``GET /skills/{id}/archive``.
  3. Unpack into ``<worktree>/.claude/skills/<slug>/``.
  4. Write a ``.weave-version`` sidecar so re-runs short-circuit if
     the same `<slug>-<version>` is already on disk (idempotency).

Resolution order in Claude Code is project > user > builtin, so a
worktree-local skill wins over the user's global ``~/.claude/skills/``
copy. The launch spec's pinned version (sprint thread c78ead6d /
mint thread defaults to ``"latest"``) lets the CLI install the
correct version per launch without negotiating with global state.

Failure-mode policy (per DoD): a skill download failure is non-fatal.
The launch continues; the user can always run ``weave skill install``
manually after the fact. Each per-skill failure is recorded in the
``failed`` list and surfaced to the operator as a yellow warn line.
"""
from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import RuntimeConfig
from loomcli.schema.launch_spec import LaunchSpec


# Sidecar filename written into each installed skill dir to track the
# pinned `<slug>-<version>` so we can detect re-install idempotency.
SIDECAR_FILENAME = ".weave-version"


# Companion sidecar (sprint thread 647858ec) that records the engine's
# ``current_version_id`` at install time. The resume-update check
# compares this against the engine's *current* current_version_id —
# if they diverge, an upgrade is available. Separate file from
# SIDECAR_FILENAME so the older plain-text idempotency check stays
# backward-compatible with worktrees installed before this thread.
VERSION_ID_SIDECAR_FILENAME = ".weave-skill-version-id"


# Subdir within the worktree where Claude Code resolves project-local
# skills. CC's resolution chain is project > user > builtin, so this
# location wins over the user's global ``~/.claude/skills/``.
WORKTREE_SKILL_SUBDIR = ".claude/skills"


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class SkillInstallResult:
    """Per-skill outcome of ``install_spec_skills``.

    All three lists hold skill slugs; ``failed`` carries an error
    message alongside.
    """

    installed: list[str] = field(default_factory=list)
    """Skills written fresh on this run."""

    skipped: list[str] = field(default_factory=list)
    """Already-correct version on disk; idempotent no-op."""

    failed: list[tuple[str, str]] = field(default_factory=list)
    """``(slug, error_message)`` per skill that couldn't be installed."""

    @property
    def any_failed(self) -> bool:
        return bool(self.failed)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def install_spec_skills(
    cfg: RuntimeConfig,
    spec: LaunchSpec,
    worktree: Path,
    *,
    client: Optional[PowerloomClient] = None,
) -> SkillInstallResult:
    """Install the spec's pinned skills into ``<worktree>/.claude/skills/``.

    Caller may pass an existing ``PowerloomClient`` (e.g. one already
    used for redeem); when omitted we instantiate from ``cfg``.

    Always returns a ``SkillInstallResult`` — even when the engine
    skill list lookup fails or the spec has no skills. Per the DoD,
    failures don't raise; the launch continues.
    """
    result = SkillInstallResult()
    if not spec.skills:
        return result

    target_root = worktree / WORKTREE_SKILL_SUBDIR
    target_root.mkdir(parents=True, exist_ok=True)

    own_client = client is None
    api = client or PowerloomClient(cfg)
    try:
        try:
            all_skills = api.get("/skills")
        except PowerloomApiError as exc:
            # Couldn't list skills; record a per-skill failure for each
            # one in the spec so the operator sees the issue per-row.
            for skill in spec.skills:
                result.failed.append(
                    (skill.slug, f"engine /skills lookup failed: {exc}"),
                )
            return result

        by_name: dict[str, dict] = {}
        if isinstance(all_skills, list):
            for entry in all_skills:
                if isinstance(entry, dict) and entry.get("name"):
                    by_name[entry["name"]] = entry

        for skill in spec.skills:
            _install_one(api, skill, target_root, by_name, result)
    finally:
        if own_client:
            api.close()

    return result


# ---------------------------------------------------------------------------
# Per-skill install
# ---------------------------------------------------------------------------


def _install_one(
    api: PowerloomClient,
    skill: Any,
    target_root: Path,
    by_name: dict[str, dict],
    result: SkillInstallResult,
) -> None:
    slug = skill.slug
    version = skill.version
    target = target_root / slug
    sidecar = target / SIDECAR_FILENAME
    pin_marker = f"{slug}-{version}"

    # Idempotency — only skip when the dir EXISTS and the sidecar
    # matches. Empty directory or missing sidecar means we go reinstall.
    if (
        target.exists()
        and sidecar.exists()
        and sidecar.read_text(encoding="utf-8").strip() == pin_marker
    ):
        result.skipped.append(slug)
        return

    meta = by_name.get(slug)
    if meta is None:
        result.failed.append((slug, "skill not found in this org's catalog"))
        return
    skill_id = meta.get("id")
    if not skill_id:
        result.failed.append((slug, "skill row missing id (engine drift?)"))
        return

    try:
        archive_bytes = _download_archive(api, str(skill_id))
    except Exception as exc:  # noqa: BLE001 — surface any failure as per-skill, not fatal
        result.failed.append((slug, f"download failed: {exc}"))
        return

    try:
        _unpack_archive(archive_bytes, target)
    except Exception as exc:  # noqa: BLE001
        result.failed.append((slug, f"unpack failed: {exc}"))
        return

    try:
        sidecar.write_text(pin_marker, encoding="utf-8")
    except OSError as exc:
        # Unpack succeeded but we couldn't mark the version. Treat
        # as soft-fail so the next launch tries again instead of
        # silently considering this skill installed.
        result.failed.append((slug, f"sidecar write failed: {exc}"))
        return

    # Sprint thread 647858ec — also stash the engine's
    # current_version_id so the resume-update check can compare
    # against the engine's latest. Best-effort: missing meta means
    # the resume check skips this skill rather than flagging.
    current_version_id = meta.get("current_version_id")
    if current_version_id:
        version_id_sidecar = target / VERSION_ID_SIDECAR_FILENAME
        try:
            version_id_sidecar.write_text(
                str(current_version_id), encoding="utf-8"
            )
        except OSError:
            # Non-fatal — install succeeded; resume check will skip
            # the comparison for this skill rather than flag.
            pass

    result.installed.append(slug)


def _download_archive(api: PowerloomClient, skill_id: str) -> bytes:
    """Stream the skill archive into memory.

    Bypasses ``PowerloomClient.get`` (which JSON-decodes) and uses the
    underlying httpx client directly so we get the raw bytes.
    """
    res = api._http.get(  # type: ignore[attr-defined] — intentional internal access
        f"/skills/{skill_id}/archive",
    )
    res.raise_for_status()
    return res.content


def _unpack_archive(archive_bytes: bytes, target: Path) -> None:
    """Extract a ``.tar.gz`` archive into ``target``.

    Uses tarfile's ``data`` filter (Python 3.12+) to refuse symlinks /
    absolute paths / directory traversal — protects the worktree from
    a hostile archive even though our own engine produces them.
    """
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        # Python 3.12 strict default; older versions emit DeprecationWarning
        # without it. Either way, use the data filter explicitly.
        tar.extractall(path=str(target), filter="data")
