"""Quick DB-layer smoke test. Run from plugin/ dir:
    python tests/smoke.py

Exits 0 on success, non-zero on failure.
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))
from powerloom_home.db import HomeDB

tmp = Path(tempfile.mkdtemp()) / "t.sqlite"
db = HomeDB(tmp)

# OUs
o = db.create_ou(name="home", display_name="Home")
assert o["path"] == "/home", f"bad path {o['path']}"
c = db.create_ou(name="projects", parent_path="/home", display_name="Projects")
assert c["path"] == "/home/projects"
b = db.create_ou(name="home", display_name="Home")
assert b["id"] == o["id"], "ou create not idempotent"

# Skill with v1.2.0 extensions
s = db.create_skill(
    ou_path="/home", name="a-skill", display_name="A",
    system=True, auto_attach_to={"task_kinds": ["qa"]},
)
assert s["system"] is True
assert s["auto_attach_to"]["task_kinds"] == ["qa"]

# Agent with coordinator role + skill attachment
ag = db.create_agent(
    ou_path="/home", name="me", display_name="Me",
    model="claude-sonnet-4-5", system_prompt="hi",
    owner_principal_ref="user:t@t.com",
    skills=["a-skill"],
    coordinator_role=True,
    task_kinds=["coordination"],
    memory_permissions=["home.x"],
)
assert ag["coordinator_role"] is True
assert ag["task_kinds"] == ["coordination"]
assert ag["skills"] == ["a-skill"]

# Silent skip unknown skill
ag2 = db.create_agent(
    ou_path="/home", name="me2", display_name="Me2",
    model="m", system_prompt="s", owner_principal_ref="user:t@t.com",
    skills=["a-skill", "nonexistent"],
)
assert ag2["skills"] == ["a-skill"]

# Listings
assert len(db.list_skills(ou_path="/home")) == 1
assert len(db.list_agents(ou_path="/home")) == 2
assert len(db.list_ous()) == 2

# Audit
audit = db.recent_audit()
kinds = {r["resource_kind"] for r in audit}
assert "OU" in kinds and "Skill" in kinds and "Agent" in kinds

# Persistence across reopen
prior_org = db.get_home_org_id()
db.close()
db2 = HomeDB(tmp)
assert db2.get_home_org_id() == prior_org
assert len(db2.list_ous()) == 2
db2.close()

print("ALL DB SMOKE TESTS PASS")
