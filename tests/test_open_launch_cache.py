"""Tests for ``loomcli._open.launch_cache``.

Sprint polish-doctor-resume-20260430, thread b594a3d6.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loomcli._open import launch_cache
from loomcli._open.launch_cache import (
    _hash_token,
    clear_cached_spec,
    list_cache_entries,
    prune_expired,
    read_cached_spec,
    write_cached_spec,
)


def _spec_dict(*, expires_offset_minutes: int = 15) -> dict:
    return {
        "schema_version": 1,
        "launch_id": "11111111-1111-1111-1111-111111111111",
        "expires_at": (
            datetime.now(timezone.utc)
            + timedelta(minutes=expires_offset_minutes)
        ).isoformat(),
        "scope": {"slug": "cc-test-20260501"},
    }


# ---------------------------------------------------------------------------
# write / read / clear roundtrips
# ---------------------------------------------------------------------------


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_cached_spec("lt_missing", root=tmp_path) is None


def test_write_then_read_roundtrips(tmp_path: Path) -> None:
    spec = _spec_dict()
    target = write_cached_spec("lt_xxx", spec, root=tmp_path)
    assert target is not None
    assert target.exists()
    loaded = read_cached_spec("lt_xxx", root=tmp_path)
    assert loaded is not None
    assert loaded["launch_id"] == spec["launch_id"]


def test_cache_filename_uses_token_hash_not_raw_token(tmp_path: Path) -> None:
    spec = _spec_dict()
    target = write_cached_spec("lt_secret_should_not_leak", spec, root=tmp_path)
    assert target is not None
    # Filename is the SHA-256 hash, not the raw token.
    assert "lt_secret_should_not_leak" not in target.name
    assert target.name.startswith(_hash_token("lt_secret_should_not_leak"))


def test_read_returns_none_for_expired_entry(tmp_path: Path) -> None:
    spec = _spec_dict(expires_offset_minutes=-1)
    write_cached_spec("lt_old", spec, root=tmp_path)
    assert read_cached_spec("lt_old", root=tmp_path) is None


def test_read_deletes_expired_entry_on_miss(tmp_path: Path) -> None:
    """Expired entries should self-prune on read so future reads don't re-walk."""
    spec = _spec_dict(expires_offset_minutes=-1)
    target = write_cached_spec("lt_dead", spec, root=tmp_path)
    assert target.exists()
    read_cached_spec("lt_dead", root=tmp_path)
    assert not target.exists()


def test_read_tolerates_malformed_json(tmp_path: Path) -> None:
    target = tmp_path / f"{_hash_token('lt_garbage')}.json"
    target.write_text("not json {{", encoding="utf-8")
    assert read_cached_spec("lt_garbage", root=tmp_path) is None


def test_read_tolerates_unparseable_expires_at(tmp_path: Path) -> None:
    spec = {"launch_id": "x", "expires_at": "yesterday afternoon"}
    target = write_cached_spec("lt_baddate", spec, root=tmp_path)
    # Treated as miss — and crucially, NOT auto-deleted (might be a
    # forward-compat shape we'll learn to read later).
    assert read_cached_spec("lt_baddate", root=tmp_path) is None
    # File still on disk for forensics.
    assert target.exists()


def test_clear_removes_entry(tmp_path: Path) -> None:
    write_cached_spec("lt_doomed", _spec_dict(), root=tmp_path)
    assert clear_cached_spec("lt_doomed", root=tmp_path) is True
    assert clear_cached_spec("lt_doomed", root=tmp_path) is False  # idempotent


def test_clear_idempotent_on_missing(tmp_path: Path) -> None:
    assert clear_cached_spec("lt_never_existed", root=tmp_path) is False


# ---------------------------------------------------------------------------
# bulk ops — list, prune
# ---------------------------------------------------------------------------


def test_list_cache_entries_returns_files(tmp_path: Path) -> None:
    write_cached_spec("lt_a", _spec_dict(), root=tmp_path)
    write_cached_spec("lt_b", _spec_dict(), root=tmp_path)
    entries = list_cache_entries(root=tmp_path)
    assert len(entries) == 2


def test_list_cache_entries_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_cache_entries(root=tmp_path / "nonexistent") == []


def test_prune_expired_removes_dead_entries(tmp_path: Path) -> None:
    write_cached_spec("lt_dead", _spec_dict(expires_offset_minutes=-1), root=tmp_path)
    write_cached_spec("lt_alive", _spec_dict(), root=tmp_path)
    removed = prune_expired(root=tmp_path)
    assert removed == 1
    # alive survives
    assert read_cached_spec("lt_alive", root=tmp_path) is not None


def test_prune_expired_handles_malformed_files(tmp_path: Path) -> None:
    """Garbage files get cleaned up by prune too."""
    bad = tmp_path / f"{_hash_token('lt_bad')}.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not json", encoding="utf-8")
    removed = prune_expired(root=tmp_path)
    assert removed == 1
    assert not bad.exists()


# ---------------------------------------------------------------------------
# 0600 perms on POSIX (skip Windows)
# ---------------------------------------------------------------------------


def test_write_sets_0600_on_posix(tmp_path: Path) -> None:
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("0600 is a POSIX permission concept")
    target = write_cached_spec("lt_secret", _spec_dict(), root=tmp_path)
    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600
