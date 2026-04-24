"""Tests for the Home SQLite backend. Run from plugin/ with:
    python -m pytest tests/ -q

These tests don't require the MCP SDK — they exercise the DB layer
directly. They validate that the SQLite schema + CRUD logic match
what the MCP tool handlers will call into.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add mcp-server/ to the path so `import powerloom_home.db` resolves.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "mcp-server"))

import pytest

from powerloom_home.db import HomeDB


@pytest.fixture
def db(tmp_path):
    d = HomeDB(tmp_path / "test.sqlite")
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Init + org
# ---------------------------------------------------------------------------


def test_init_seeds_home_org(db):
    org_id = db.get_home_org_id()
    assert org_id  # non-empty string


def test_init_is_idempotent(db, tmp_path):
    # Re-opening the same DB file shouldn't re-seed.
    path = tmp_path / "test.sqlite"
    first_org = db.get_home_org_id()
    db.close()
    db2 = HomeDB(path)
    assert db2.get_home_org_id() == first_org
    db2.close()


# ---------------------------------------------------------------------------
# OU
# ---------------------------------------------------------------------------


def test_create_root_ou(db):
    ou = db.create_ou(name="home", display_name="Home")
    assert ou["path"] == "/home"
    assert ou["display_name"] == "Home"
    assert ou["parent_id"] is None


def test_create_nested_ou(db):
    db.create_ou(name="home", display_name="Home")
    child = db.create_ou(name="projects", parent_path="/home", display_name="Projects")
    assert child["path"] == "/home/projects"
    assert child["parent_id"] is not None


def test_create_ou_rejects_unknown_parent(db):
    with pytest.raises(ValueError, match="parent_path"):
        db.create_ou(name="orphan", parent_path="/nonexistent", display_name="Orphan")


def test_create_ou_idempotent(db):
    a = db.create_ou(name="home", display_name="Home")
    b = db.create_ou(name="home", display_name="Home")
    assert a["id"] == b["id"]
    assert len(db.list_ous()) == 1


def test_list_ous_orders_by_path(db):
    db.create_ou(name="home", display_name="Home")
    db.create_ou(name="projects", parent_path="/home", display_name="Projects")
    db.create_ou(name="work", display_name="Work")
    paths = [o["path"] for o in db.list_ous()]
    assert paths == ["/home", "/home/projects", "/work"]


def test_resolve_ou_path(db):
    db.create_ou(name="home", display_name="Home")
    assert db.resolve_ou_path("/home") is not None
    assert db.resolve_ou_path("/missing") is None


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


def test_create_skill_basic(db):
    db.create_ou(name="home", display_name="Home")
    skill = db.create_skill(
        ou_path="/home",
        name="code-reviewer",
        display_name="Code Reviewer",
        description="Reviews code.",
    )
    assert skill["name"] == "code-reviewer"
    assert skill["skill_type"] == "archive"
    assert skill["system"] is False
    assert skill["auto_attach_to"] is None
    assert skill["tool_schema"] is None


def test_create_skill_with_auto_attach(db):
    db.create_ou(name="home", display_name="Home")
    skill = db.create_skill(
        ou_path="/home",
        name="sys-skill",
        display_name="Sys Skill",
        system=True,
        auto_attach_to={"task_kinds": ["qa"]},
    )
    assert skill["system"] is True
    assert skill["auto_attach_to"] == {"task_kinds": ["qa"]}


def test_create_skill_unknown_ou_fails(db):
    with pytest.raises(ValueError, match="not found"):
        db.create_skill(
            ou_path="/nowhere",
            name="x",
            display_name="X",
        )


def test_list_skills_empty(db):
    db.create_ou(name="home", display_name="Home")
    assert db.list_skills() == []
    assert db.list_skills(ou_path="/home") == []


def test_list_skills_filtered_by_ou(db):
    db.create_ou(name="home", display_name="Home")
    db.create_ou(name="work", display_name="Work")
    db.create_skill(ou_path="/home", name="a", display_name="A")
    db.create_skill(ou_path="/work", name="b", display_name="B")
    assert len(db.list_skills(ou_path="/home")) == 1
    assert len(db.list_skills(ou_path="/work")) == 1
    assert len(db.list_skills()) == 2


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def test_create_agent_basic(db):
    db.create_ou(name="home", display_name="Home")
    agent = db.create_agent(
        ou_path="/home",
        name="assistant",
        display_name="Assistant",
        model="claude-sonnet-4-5",
        system_prompt="You help.",
        owner_principal_ref="user:shane@example.com",
    )
    assert agent["name"] == "assistant"
    assert agent["model"] == "claude-sonnet-4-5"
    assert agent["coordinator_role"] is False
    assert agent["task_kinds"] == []
    assert agent["memory_permissions"] == []
    assert agent["skills"] == []


def test_create_agent_with_coordinator_fields(db):
    db.create_ou(name="home", display_name="Home")
    agent = db.create_agent(
        ou_path="/home",
        name="coord",
        display_name="Coordinator",
        model="claude-opus-4",
        system_prompt="You coordinate.",
        owner_principal_ref="user:shane@example.com",
        coordinator_role=True,
        task_kinds=["coordination", "routing"],
        memory_permissions=["home.projects"],
        reranker_model="claude-haiku-3-5",
    )
    assert agent["coordinator_role"] is True
    assert agent["task_kinds"] == ["coordination", "routing"]
    assert agent["memory_permissions"] == ["home.projects"]
    assert agent["reranker_model"] == "claude-haiku-3-5"


def test_create_agent_attaches_skills(db):
    db.create_ou(name="home", display_name="Home")
    db.create_skill(ou_path="/home", name="skill-a", display_name="A")
    db.create_skill(ou_path="/home", name="skill-b", display_name="B")
    agent = db.create_agent(
        ou_path="/home",
        name="multi-skill",
        display_name="Multi-Skill",
        model="claude-sonnet-4-5",
        system_prompt="You do things.",
        owner_principal_ref="user:shane@example.com",
        skills=["skill-a", "skill-b"],
    )
    assert sorted(agent["skills"]) == ["skill-a", "skill-b"]


def test_create_agent_silently_skips_unknown_skills(db):
    """Intentional — matches the applier's forgiving behavior."""
    db.create_ou(name="home", display_name="Home")
    db.create_skill(ou_path="/home", name="real-skill", display_name="Real")
    agent = db.create_agent(
        ou_path="/home",
        name="a",
        display_name="A",
        model="claude-sonnet-4-5",
        system_prompt="x",
        owner_principal_ref="user:t@t.com",
        skills=["real-skill", "imaginary-skill"],
    )
    assert agent["skills"] == ["real-skill"]


def test_list_agents_filtered_by_ou(db):
    db.create_ou(name="home", display_name="Home")
    db.create_ou(name="work", display_name="Work")
    db.create_agent(
        ou_path="/home", name="a1", display_name="A1",
        model="m", system_prompt="s", owner_principal_ref="user:t@t.com",
    )
    db.create_agent(
        ou_path="/work", name="a2", display_name="A2",
        model="m", system_prompt="s", owner_principal_ref="user:t@t.com",
    )
    assert len(db.list_agents(ou_path="/home")) == 1
    assert len(db.list_agents(ou_path="/work")) == 1
    assert len(db.list_agents()) == 2


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_audit_captures_mutations(db):
    db.create_ou(name="home", display_name="Home")
    db.create_skill(ou_path="/home", name="s", display_name="S")
    audit = db.recent_audit()
    kinds = [r["resource_kind"] for r in audit]
    assert "OU" in kinds
    assert "Skill" in kinds


def test_audit_recent_order(db):
    db.create_ou(name="home", display_name="Home")
    audit = db.recent_audit(limit=10)
    # Most recent first
    assert audit[0]["action_verb"] == "create"
    assert audit[0]["resource_kind"] == "OU"


def test_audit_limit(db):
    db.create_ou(name="a", display_name="A")
    db.create_ou(name="b", display_name="B")
    db.create_ou(name="c", display_name="C")
    assert len(db.recent_audit(limit=2)) == 2
    assert len(db.recent_audit(limit=100)) == 3
