"""Apply per-runtime skill + MCP config files for non-Claude launches.

Console-deployability sprint PR4, thread ``ea3be766``.

Calls ``GET /launches/{token}/runtime-configs`` (engine-side
translator output) and writes the returned files to disk. Non-Claude
launches (codex_cli / gemini_cli / antigravity) need this because the
existing ``skills_install`` and ``mcp_install`` modules only know
Claude's shape — Codex skills land at ``.codex/skills/<name>/``,
Gemini extensions at ``.gemini/extensions/<name>/``, etc.

For ``claude_code`` launches the existing pipeline (archive-extract +
``.mcp.json`` write) is the right path; this module is a no-op there.
The engine still emits Claude-shaped configs for forward-compat, but
applying them would conflict with the legacy pipeline's outputs.

Failure-mode policy: per-launch failures are non-fatal. We surface
each failed file with a yellow warning line; the agent still boots,
the operator can re-run ``weave open`` after fixing the underlying
issue (e.g. wrong file mode on a parent dir).

The applier mirrors the engine-side ``apply_runtime_config`` (file
hash idempotency, refuses to write outside the target dir, surfaces
per-file ``installed`` / ``updated`` / ``unchanged`` actions).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import RuntimeConfig as LoomcliRuntimeConfig
from loomcli.schema.launch_spec import LaunchSpec


log = logging.getLogger(__name__)


# Launch-runtime tags that need translator-rendered configs.
# Claude doesn't — its existing install path covers it.
_TRANSLATOR_RUNTIMES = frozenset({"codex_cli", "gemini_cli", "antigravity"})


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


FileAction = Literal["installed", "updated", "unchanged", "error"]


@dataclass
class RuntimeFileResult:
    """One file-write outcome from ``install_runtime_configs``."""

    path: Path
    action: FileAction
    detail: Optional[str] = None
    """Human-readable error or note when ``action == 'error'``."""


@dataclass
class RuntimeConfigsInstallResult:
    """Aggregate outcome of ``install_runtime_configs``.

    ``skipped_reason`` is set (and ``files`` is empty) when:
      * the launch's runtime is ``claude_code`` (legacy pipeline owns it)
      * the engine returns an empty configs list (launch had no
        skills + no MCP servers)
      * the engine call failed (non-fatal — logged as a warning)
    """

    files: list[RuntimeFileResult] = field(default_factory=list)
    post_install_steps: list[str] = field(default_factory=list)
    """Operator-facing steps surfaced by translators (e.g. 'set
    POWERLOOM_MCP_TOKEN in your shell rc'). Never executed."""
    warnings: list[str] = field(default_factory=list)
    """Translator warnings (e.g. 'this runtime can't consume tool_schema').
    Surfaced to the operator via the open-cmd UI."""
    skipped_reason: Optional[str] = None
    """One of ``'claude_runtime'``, ``'empty_configs'``, ``'fetch_failed'``,
    or None when configs were applied."""

    @property
    def any_failed(self) -> bool:
        return any(f.action == "error" for f in self.files)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def install_runtime_configs(
    cfg: LoomcliRuntimeConfig,
    spec: LaunchSpec,
    *,
    launch_token: str,
    target_dir: Path,
    client: Optional[PowerloomClient] = None,
) -> RuntimeConfigsInstallResult:
    """Fetch translator-rendered configs from the engine and apply them.

    Parameters
    ----------
    cfg
        loomcli runtime config (used to construct a PowerloomClient
        when ``client`` isn't passed).
    spec
        The redeemed launch spec. We read ``spec.runtime`` to decide
        whether to short-circuit for ``claude_code``.
    launch_token
        The raw launch token (``lt_…``); used to call the runtime-
        configs endpoint. Pass through from the caller — the spec
        itself doesn't carry the raw token (only its hash).
    target_dir
        Where to root the relative paths the translator emits. For
        worktree-local files (Claude / Codex skills bundled with the
        repo) this is the worktree. For host-level paths (Codex
        ``~/.codex/`` files, Gemini ``~/.gemini/`` extensions) the
        translator still emits ``.codex/...`` / ``.gemini/...``
        relative paths and the caller passes ``Path.home()``.
        Today: the caller picks ``Path.home()`` for non-Claude runtimes
        because the translators target host-level config dirs; we may
        switch to a per-config-kind target_dir in a follow-up if
        per-worktree placements emerge.

    Returns
    -------
    :class:`RuntimeConfigsInstallResult`
        Aggregate outcome with per-file action + translator post-install
        steps. The caller surfaces each entry to the operator.
    """
    if spec.runtime not in _TRANSLATOR_RUNTIMES:
        # Claude path is covered by skills_install + mcp_install.
        # Don't fetch the endpoint at all — keeps the read flow off
        # the API for the dominant case.
        return RuntimeConfigsInstallResult(skipped_reason="claude_runtime")

    own_client = client is None
    api = client or PowerloomClient(cfg)
    try:
        try:
            payload = api.get(f"/launches/{launch_token}/runtime-configs")
        except PowerloomApiError as exc:
            log.warning(
                "runtime_configs_install.fetch_failed token=… err=%s", exc,
            )
            return RuntimeConfigsInstallResult(
                skipped_reason="fetch_failed",
                warnings=[f"engine /launches/.../runtime-configs failed: {exc}"],
            )
    finally:
        if own_client:
            api.close()

    if not isinstance(payload, dict) or not payload.get("configs"):
        return RuntimeConfigsInstallResult(skipped_reason="empty_configs")

    result = RuntimeConfigsInstallResult()
    target_dir = target_dir.resolve()

    for cfg_dto in payload.get("configs", []):
        if not isinstance(cfg_dto, dict):
            continue
        for step in cfg_dto.get("post_install_steps", []) or []:
            if isinstance(step, str):
                result.post_install_steps.append(step)
        for w in cfg_dto.get("warnings", []) or []:
            if isinstance(w, str):
                result.warnings.append(w)
        for f_dto in cfg_dto.get("files", []) or []:
            if not isinstance(f_dto, dict):
                continue
            result.files.append(_apply_one_file(f_dto, target_dir))

    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _apply_one_file(
    f_dto: dict[str, Any], target_dir: Path
) -> RuntimeFileResult:
    """Write one engine-shaped file DTO to disk under ``target_dir``.

    Implements the same idempotency as the server-side applier:
    SHA-256 of the new payload vs current contents picks
    ``installed`` / ``updated`` / ``unchanged``. Refuses absolute
    paths or any segment of ``..`` to keep the writer inside
    ``target_dir`` (defense-in-depth — the engine's translator
    output is trusted but rendering bugs shouldn't escape the
    worktree).
    """
    rel_str = f_dto.get("path") or ""
    rel_path = Path(rel_str)
    # Defense in depth: catch absolute paths (Path.is_absolute() handles
    # the host's native shape; the explicit `/`-prefix and drive-letter
    # checks catch foreign shapes from the wire — e.g. POSIX-style
    # paths arriving on Windows or vice versa).
    looks_absolute = (
        rel_path.is_absolute()
        or rel_str.startswith("/")
        or rel_str.startswith("\\")
        or (len(rel_str) >= 2 and rel_str[1] == ":")
    )
    if looks_absolute or any(p == ".." for p in rel_path.parts):
        return RuntimeFileResult(
            path=rel_path,
            action="error",
            detail=(
                "refusing to write outside target_dir "
                "(absolute path or .. segment)"
            ),
        )

    dst = target_dir / rel_path

    # Resolve content. Engine prefers `bytes_content_b64` when set;
    # otherwise `content` is UTF-8 text.
    bytes_b64 = f_dto.get("bytes_content_b64")
    if bytes_b64:
        try:
            payload_bytes = base64.b64decode(bytes_b64, validate=True)
        except (ValueError, TypeError) as e:
            return RuntimeFileResult(
                path=dst,
                action="error",
                detail=f"invalid bytes_content_b64: {e}",
            )
    else:
        text = f_dto.get("content") or ""
        if not isinstance(text, str):
            return RuntimeFileResult(
                path=dst,
                action="error",
                detail="content was not a string",
            )
        payload_bytes = text.encode("utf-8")

    expected_hash = hashlib.sha256(payload_bytes).hexdigest()

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return RuntimeFileResult(
            path=dst,
            action="error",
            detail=f"mkdir failed: {e}",
        )

    mode = int(f_dto.get("mode") or 0o644)
    if dst.exists():
        try:
            existing_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
        except OSError as e:
            return RuntimeFileResult(
                path=dst,
                action="error",
                detail=f"read failed: {e}",
            )
        if existing_hash == expected_hash:
            return RuntimeFileResult(path=dst, action="unchanged")
        try:
            dst.write_bytes(payload_bytes)
            _safe_chmod(dst, mode)
        except OSError as e:
            return RuntimeFileResult(
                path=dst,
                action="error",
                detail=f"write failed: {e}",
            )
        return RuntimeFileResult(path=dst, action="updated")

    try:
        dst.write_bytes(payload_bytes)
        _safe_chmod(dst, mode)
    except OSError as e:
        return RuntimeFileResult(
            path=dst,
            action="error",
            detail=f"write failed: {e}",
        )
    return RuntimeFileResult(path=dst, action="installed")


def _safe_chmod(path: Path, mode: int) -> None:
    """Best-effort chmod. POSIX honours it; Windows ignores most bits.

    On Windows, ``os.chmod`` only effectively flips the read-only
    bit (write ↔ read-only). Setting executable mode is a no-op,
    which is fine for translator outputs (skill manifests are read,
    not exec'd)."""
    if os.name == "nt":
        # Don't bother — most modes are no-ops here.
        return
    try:
        path.chmod(mode)
    except OSError:
        pass
