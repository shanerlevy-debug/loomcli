"""Client-side launch-spec cache for resume-on-interrupt.

Sprint polish-doctor-resume-20260430, thread b594a3d6.

Pairs with the engine's 5-minute redeem cache (Sprint 1, thread
``08642d9a``). After ``GET /launches/<token>`` succeeds, ``weave open``
writes the spec to ``~/.powerloom/launches/<token_hash>.json`` so a
Ctrl-C between redeem-and-clone can re-run the same command and pick
up where it died — without burning a fresh launch token from the
web UI.

The cache key is the **SHA-256 hash** of the raw token, never the raw
token itself. Reasons:

  * The cache file lives in a user-readable dir; storing the raw
    token would make it equivalent to a credentials file.
  * The engine's 5-min redeem cache is the only thing that can serve
    a re-redeem. Once we have the spec on disk we don't need the raw
    token again — we go straight to clone + register.

Cache is cleared:

  * On successful runtime exec (handled by ``weave open`` after the
    final hand-off succeeds; we don't need the spec anymore).
  * Explicitly by ``weave gc`` (sprint thread 7a81d721).
  * On expiry (TTL = the launch's ``expires_at``; entries past their
    deadline are skipped on read and pruned on write).

Failure-mode policy: any IO / parse failure on read is silent — the
caller falls through to the engine round-trip, same as before this
cache existed. Write failures log a warning but never block.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Subdir under ``~/.powerloom/`` where cache entries live. One JSON
# file per launch; keeps the directory listing readable + lets ``weave
# gc`` ``rm -rf`` the whole dir when needed.
LAUNCH_CACHE_SUBDIR = "launches"


def _cache_root() -> Path:
    """Default ``~/.powerloom/launches/``. Override via POWERLOOM_HOME."""
    import os

    override = os.environ.get("POWERLOOM_HOME")
    if override:
        return Path(override) / LAUNCH_CACHE_SUBDIR
    return Path.home() / ".powerloom" / LAUNCH_CACHE_SUBDIR


def _hash_token(raw_token: str) -> str:
    """SHA-256 hex of the raw token. Same digest the engine uses."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _entry_path(raw_token: str, *, root: Optional[Path] = None) -> Path:
    cache_root = root if root is not None else _cache_root()
    return cache_root / f"{_hash_token(raw_token)}.json"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_cached_spec(
    raw_token: str, *, root: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """Return the cached LaunchSpec dict, or ``None`` when missing/expired.

    Parsed dict shape matches what ``GET /launches/<token>`` returns —
    the caller validates with ``LaunchSpec.model_validate``.
    """
    target = _entry_path(raw_token, root=root)
    if not target.exists():
        return None
    try:
        spec = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(spec, dict):
        return None
    expires_at_str = spec.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(
                str(expires_at_str).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                # Past TTL — treat as cache miss + delete so future reads
                # don't re-walk the same dead entry.
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
        except (ValueError, TypeError):
            # Unparseable — treat as miss; don't delete (might be a
            # forward-compat shape this loomcli version doesn't grok).
            return None
    return spec


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_cached_spec(
    raw_token: str,
    spec: dict[str, Any],
    *,
    root: Optional[Path] = None,
) -> Optional[Path]:
    """Persist the spec dict. Returns the path written or ``None`` on failure.

    Best-effort: any OSError is swallowed. The caller proceeds with the
    in-memory spec regardless.
    """
    target = _entry_path(raw_token, root=root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(spec, default=str), encoding="utf-8")
    except OSError:
        return None
    # Best-effort 0600 on POSIX. The cache holds a full spec including
    # session_attach_token + clone_auth.token; keep it user-only.
    try:
        import os as _os

        _os.chmod(target, 0o600)
    except OSError:
        pass
    return target


# ---------------------------------------------------------------------------
# Clear (per-token + bulk)
# ---------------------------------------------------------------------------


def clear_cached_spec(
    raw_token: str, *, root: Optional[Path] = None
) -> bool:
    """Remove the cache entry for ``raw_token``. Returns True if a file was removed."""
    target = _entry_path(raw_token, root=root)
    if not target.exists():
        return False
    try:
        target.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def list_cache_entries(
    *, root: Optional[Path] = None
) -> list[Path]:
    """Return all cache file paths. Used by ``weave gc`` to prune the dir."""
    cache_root = root if root is not None else _cache_root()
    if not cache_root.is_dir():
        return []
    return sorted(p for p in cache_root.iterdir() if p.is_file())


def prune_expired(*, root: Optional[Path] = None) -> int:
    """Walk the cache, drop any entries whose ``expires_at`` has passed.

    Returns the number of entries removed. Used by ``weave gc`` and by
    a periodic background sweep if/when one lands.
    """
    cache_root = root if root is not None else _cache_root()
    if not cache_root.is_dir():
        return 0
    removed = 0
    now = datetime.now(timezone.utc)
    for entry in cache_root.iterdir():
        if not entry.is_file():
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Garbage entry → delete proactively.
            try:
                entry.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
            continue
        expires_at_str = data.get("expires_at") if isinstance(data, dict) else None
        if not expires_at_str:
            continue
        try:
            expires_at = datetime.fromisoformat(
                str(expires_at_str).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if expires_at <= now:
            try:
                entry.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed
