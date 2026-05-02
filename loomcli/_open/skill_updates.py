"""Skill-update check for ``weave open --resume``.

Sprint skills-mcp-bootstrap-20260430, thread 647858ec.

Per-design decision (set in the milestone plan doc): skills are
**pinned at launch-time**. Resume does NOT auto-upgrade; instead it
checks whether the engine's current_version_id for each installed
skill has moved since install, and prints a one-line summary if any
have. Operators run ``weave skill upgrade --in-worktree`` to apply.

The check reads the per-skill ``.weave-skill-version-id`` sidecar
written by ``skills_install._install_one`` and compares against the
engine's ``current_version_id`` from ``GET /skills``. Skills installed
before sprint thread 647858ec lack the sidecar — those are silently
skipped (treated as "version unknown") rather than flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loomcli._open.skills_install import (
    VERSION_ID_SIDECAR_FILENAME,
    WORKTREE_SKILL_SUBDIR,
)
from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import RuntimeConfig


@dataclass
class SkillUpdate:
    """A single skill that has a newer version available on the engine."""

    slug: str
    installed_version_id: str
    current_version_id: str


@dataclass
class SkillUpdateCheckResult:
    updates: list[SkillUpdate] = field(default_factory=list)
    """Skills whose installed version differs from the engine's current."""

    skipped: list[str] = field(default_factory=list)
    """Slugs we couldn't check (no sidecar / not in catalog)."""

    error: Optional[str] = None
    """Engine error or other lookup failure that aborted the whole check."""

    @property
    def has_updates(self) -> bool:
        return bool(self.updates)


def check_skill_updates(
    cfg: RuntimeConfig,
    worktree: Path,
    *,
    client: Optional[PowerloomClient] = None,
) -> SkillUpdateCheckResult:
    """Compare installed skill versions in ``worktree`` to the engine's current.

    Walks ``<worktree>/.claude/skills/<slug>/`` looking for the
    version-id sidecar, queries ``/skills`` once, and reports any
    diverged versions.

    Returns an empty result when the worktree has no skills installed
    or the engine lookup fails — the caller renders a one-line summary
    only when there's something useful to say.
    """
    skills_root = worktree / WORKTREE_SKILL_SUBDIR
    if not skills_root.is_dir():
        return SkillUpdateCheckResult()

    # Snapshot installed skills (slug → installed_version_id).
    installed: dict[str, str] = {}
    for child in skills_root.iterdir():
        if not child.is_dir():
            continue
        sidecar = child / VERSION_ID_SIDECAR_FILENAME
        if not sidecar.exists():
            continue
        try:
            installed[child.name] = sidecar.read_text(encoding="utf-8").strip()
        except OSError:
            continue

    if not installed:
        return SkillUpdateCheckResult()

    own_client = client is None
    api = client or PowerloomClient(cfg)
    try:
        try:
            all_skills = api.get("/skills")
        except PowerloomApiError as exc:
            return SkillUpdateCheckResult(error=f"engine /skills lookup failed: {exc}")

        current_by_name: dict[str, str] = {}
        if isinstance(all_skills, list):
            for entry in all_skills:
                if (
                    isinstance(entry, dict)
                    and entry.get("name")
                    and entry.get("current_version_id")
                ):
                    current_by_name[entry["name"]] = str(
                        entry["current_version_id"]
                    )
    finally:
        if own_client:
            api.close()

    result = SkillUpdateCheckResult()
    for slug, installed_version_id in installed.items():
        current = current_by_name.get(slug)
        if current is None:
            # Skill no longer in this user's catalog (revoked?). Not
            # an update — skip silently.
            result.skipped.append(slug)
            continue
        if current != installed_version_id:
            result.updates.append(
                SkillUpdate(
                    slug=slug,
                    installed_version_id=installed_version_id,
                    current_version_id=current,
                ),
            )
    return result


def format_update_summary(result: SkillUpdateCheckResult) -> Optional[str]:
    """Render the human-readable one-line summary, or None when no updates.

    Pattern from the DoD: "3 skills have updates: <slug1>, <slug2>, <slug3>".
    Caller appends the "Run `weave skill upgrade --in-worktree` to apply"
    hint underneath.
    """
    if not result.updates:
        return None
    n = len(result.updates)
    plural = "s" if n != 1 else ""
    slugs = ", ".join(u.slug for u in result.updates)
    return f"{n} skill{plural} ha{'ve' if n != 1 else 's'} updates: {slugs}"
