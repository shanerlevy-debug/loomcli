"""SQLite backend for Powerloom Home.

Schema is a deliberately-minimal subset of the enterprise Postgres
schema — just enough to back the tools the home MCP exposes. When a
home user upgrades to enterprise, a separate migration utility will
read home's SQLite + POST equivalents to the hosted API.

Design choices:
  - `organization_id`: always the same uuid for a home user (home is
    single-tenant by construction). Seeded at first open.
  - `ou_path` is a TEXT column — we don't bother with the closure
    table on home since OU trees here rarely exceed 2-3 levels deep.
  - `resource_id` is a uuid4 string; no attempt at distributed ID
    generation.
  - audit_log captures every mutation so `weave get audit` has
    content. Bounded to last 10k rows by a trigger — no retention
    policy needed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_SCHEMA_VERSION = 1

_INIT_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ous (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_id TEXT,
    path TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES ous(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    ou_id TEXT NOT NULL REFERENCES ous(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    skill_type TEXT NOT NULL DEFAULT 'archive'
        CHECK (skill_type IN ('archive', 'tool_definition')),
    tool_schema_json TEXT,
    current_version_id TEXT,
    system_ INTEGER NOT NULL DEFAULT 0 CHECK (system_ IN (0, 1)),
    auto_attach_to_json TEXT,
    created_at INTEGER NOT NULL,
    archived_at INTEGER,
    UNIQUE (ou_id, name)
);

CREATE TABLE IF NOT EXISTS skill_versions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    archive_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    ou_id TEXT NOT NULL REFERENCES ous(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    model TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    runtime_type TEXT NOT NULL DEFAULT 'cma',
    agent_kind TEXT NOT NULL DEFAULT 'user'
        CHECK (agent_kind IN ('user', 'service')),
    owner_principal_ref TEXT NOT NULL,
    coordinator_role INTEGER NOT NULL DEFAULT 0 CHECK (coordinator_role IN (0, 1)),
    task_kinds_json TEXT,
    memory_permissions_json TEXT,
    reranker_model TEXT,
    created_at INTEGER NOT NULL,
    archived_at INTEGER,
    UNIQUE (ou_id, name)
);

CREATE TABLE IF NOT EXISTS agent_skill_attachments (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, skill_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action_verb TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_org_recent
    ON audit_log (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skills_ou ON skills (ou_id, archived_at);
CREATE INDEX IF NOT EXISTS idx_agents_ou ON agents (ou_id, archived_at);
"""


def _default_db_path() -> Path:
    """Respect $POWERLOOM_HOME_DB_PATH (injected by plugin.json). Fall
    back to a user-home path for direct-invocation testing."""
    override = os.environ.get("POWERLOOM_HOME_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".powerloom-home" / "powerloom-home.sqlite"


class HomeDB:
    """Thread-safe SQLite wrapper. Instantiated once per MCP process."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + a lock is cheaper than one
        # connection per thread for this workload (low concurrency).
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(_INIT_SQL)
            existing = self._conn.execute(
                "SELECT value FROM _meta WHERE key = ?",
                ("schema_version",),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO _meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(_SCHEMA_VERSION)),
                )
                # Seed the home organization. Everything home-side uses
                # this single org_id.
                org_id = str(uuid.uuid4())
                self._conn.execute(
                    "INSERT INTO organizations (id, slug, display_name, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (org_id, "home", "Home", int(time.time())),
                )
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- org -----------------------------------------------------------

    def get_home_org_id(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM organizations WHERE slug = 'home'"
            ).fetchone()
            return row["id"]

    # ---- OU ------------------------------------------------------------

    def create_ou(
        self, *, name: str, parent_path: Optional[str] = None, display_name: str
    ) -> dict[str, Any]:
        with self._lock:
            org_id = self.get_home_org_id()
            parent_id: Optional[str] = None
            if parent_path:
                parent = self._conn.execute(
                    "SELECT id FROM ous WHERE path = ?", (parent_path,)
                ).fetchone()
                if parent is None:
                    raise ValueError(f"parent_path {parent_path!r} not found")
                parent_id = parent["id"]
                path = f"{parent_path}/{name}"
            else:
                path = f"/{name}"

            existing = self._conn.execute(
                "SELECT id FROM ous WHERE path = ?", (path,)
            ).fetchone()
            if existing is not None:
                # Idempotent: return existing
                return self.get_ou(existing["id"])

            ou_id = str(uuid.uuid4())
            now = int(time.time())
            self._conn.execute(
                "INSERT INTO ous (id, organization_id, name, parent_id, path, "
                "display_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ou_id, org_id, name, parent_id, path, display_name, now),
            )
            self._audit("create", "OU", ou_id, None, {
                "name": name, "path": path, "display_name": display_name,
            })
            self._conn.commit()
            return self.get_ou(ou_id)

    def get_ou(self, ou_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ous WHERE id = ?", (ou_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_ous(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ous ORDER BY path"
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_ou_path(self, path: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM ous WHERE path = ?", (path,)
            ).fetchone()
            return row["id"] if row else None

    # ---- Skill ---------------------------------------------------------

    def create_skill(
        self,
        *,
        ou_path: str,
        name: str,
        display_name: str,
        description: Optional[str] = None,
        skill_type: str = "archive",
        tool_schema: Optional[dict[str, Any]] = None,
        system: bool = False,
        auto_attach_to: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self._lock:
            ou_id = self.resolve_ou_path(ou_path)
            if ou_id is None:
                raise ValueError(f"OU path {ou_path!r} not found")
            org_id = self.get_home_org_id()
            skill_id = str(uuid.uuid4())
            now = int(time.time())
            self._conn.execute(
                "INSERT INTO skills (id, organization_id, ou_id, name, display_name, "
                "description, skill_type, tool_schema_json, system_, auto_attach_to_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill_id, org_id, ou_id, name, display_name, description,
                    skill_type,
                    json.dumps(tool_schema) if tool_schema else None,
                    1 if system else 0,
                    json.dumps(auto_attach_to) if auto_attach_to else None,
                    now,
                ),
            )
            self._audit("create", "Skill", skill_id, None, {
                "ou_path": ou_path, "name": name,
            })
            self._conn.commit()
            return self._get_skill_unlocked(skill_id)

    def _get_skill_unlocked(self, skill_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM skills WHERE id = ?", (skill_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["system"] = bool(d.pop("system_"))
        if d.get("tool_schema_json"):
            d["tool_schema"] = json.loads(d.pop("tool_schema_json"))
        else:
            d.pop("tool_schema_json", None)
            d["tool_schema"] = None
        if d.get("auto_attach_to_json"):
            d["auto_attach_to"] = json.loads(d.pop("auto_attach_to_json"))
        else:
            d.pop("auto_attach_to_json", None)
            d["auto_attach_to"] = None
        return d

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get_skill_unlocked(skill_id)

    def list_skills(self, ou_path: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            if ou_path:
                ou_id = self.resolve_ou_path(ou_path)
                if ou_id is None:
                    return []
                rows = self._conn.execute(
                    "SELECT * FROM skills WHERE ou_id = ? AND archived_at IS NULL "
                    "ORDER BY name",
                    (ou_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM skills WHERE archived_at IS NULL ORDER BY name"
                ).fetchall()
            return [self._get_skill_unlocked(r["id"]) for r in rows]

    # ---- Agent ---------------------------------------------------------

    def create_agent(
        self,
        *,
        ou_path: str,
        name: str,
        display_name: str,
        model: str,
        system_prompt: str,
        owner_principal_ref: str,
        description: Optional[str] = None,
        agent_kind: str = "user",
        coordinator_role: bool = False,
        task_kinds: Optional[list[str]] = None,
        memory_permissions: Optional[list[str]] = None,
        reranker_model: Optional[str] = None,
        skills: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        with self._lock:
            ou_id = self.resolve_ou_path(ou_path)
            if ou_id is None:
                raise ValueError(f"OU path {ou_path!r} not found")
            org_id = self.get_home_org_id()
            agent_id = str(uuid.uuid4())
            now = int(time.time())
            self._conn.execute(
                "INSERT INTO agents (id, organization_id, ou_id, name, display_name, "
                "description, model, system_prompt, agent_kind, owner_principal_ref, "
                "coordinator_role, task_kinds_json, memory_permissions_json, "
                "reranker_model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id, org_id, ou_id, name, display_name, description,
                    model, system_prompt, agent_kind, owner_principal_ref,
                    1 if coordinator_role else 0,
                    json.dumps(task_kinds or []),
                    json.dumps(memory_permissions or []),
                    reranker_model,
                    now,
                ),
            )
            # Attach skills by name (resolve each within the agent's OU).
            for skill_name in skills or []:
                skill_row = self._conn.execute(
                    "SELECT id FROM skills WHERE ou_id = ? AND name = ?",
                    (ou_id, skill_name),
                ).fetchone()
                if skill_row:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO agent_skill_attachments "
                        "(agent_id, skill_id) VALUES (?, ?)",
                        (agent_id, skill_row["id"]),
                    )
            self._audit("create", "Agent", agent_id, None, {
                "ou_path": ou_path, "name": name, "model": model,
            })
            self._conn.commit()
            return self._get_agent_unlocked(agent_id)

    def _get_agent_unlocked(self, agent_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["coordinator_role"] = bool(d["coordinator_role"])
        d["task_kinds"] = json.loads(d.pop("task_kinds_json") or "[]")
        d["memory_permissions"] = json.loads(d.pop("memory_permissions_json") or "[]")
        # attached skill names
        skill_rows = self._conn.execute(
            "SELECT s.name FROM agent_skill_attachments asa "
            "JOIN skills s ON s.id = asa.skill_id "
            "WHERE asa.agent_id = ? ORDER BY s.name",
            (agent_id,),
        ).fetchall()
        d["skills"] = [r["name"] for r in skill_rows]
        return d

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get_agent_unlocked(agent_id)

    def list_agents(self, ou_path: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            if ou_path:
                ou_id = self.resolve_ou_path(ou_path)
                if ou_id is None:
                    return []
                rows = self._conn.execute(
                    "SELECT id FROM agents WHERE ou_id = ? AND archived_at IS NULL "
                    "ORDER BY name",
                    (ou_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id FROM agents WHERE archived_at IS NULL ORDER BY name"
                ).fetchall()
            return [self._get_agent_unlocked(r["id"]) for r in rows]

    # ---- Audit ---------------------------------------------------------

    def _audit(
        self,
        verb: str,
        kind: str,
        resource_id: Optional[str],
        before: Optional[dict[str, Any]],
        after: Optional[dict[str, Any]],
    ) -> None:
        """Unlocked — caller must hold self._lock."""
        self._conn.execute(
            "INSERT INTO audit_log (organization_id, actor, action_verb, "
            "resource_kind, resource_id, before_json, after_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._conn.execute(
                    "SELECT id FROM organizations WHERE slug = 'home'"
                ).fetchone()["id"],
                "home-user",
                verb,
                kind,
                resource_id,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                int(time.time()),
            ),
        )

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
