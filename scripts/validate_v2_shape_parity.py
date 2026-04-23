"""Shape-parity check — verify v1.2.0 manifest examples validate against
v2.0.0 stdlib derivations (after the required apiVersion bump).

This is the load-bearing claim from v056-implementation-plan.md §1.5:
"A v1.2.0 Agent manifest validates against v2.0.0 Agent stdlib
derivation unchanged."

Run from loomcli repo root:
    python scripts/validate_v2_shape_parity.py
"""
import json
import os
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver


def load_schema(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def make_validator(schema_path: str) -> Draft202012Validator:
    """Build a validator that resolves $refs across the v2/ tree."""
    schema = load_schema(schema_path)
    base_uri = Path(schema_path).resolve().as_uri()

    # Collect all v2 schemas into a store for the resolver.
    store = {}
    for root, _, fns in os.walk("schema/v2"):
        for fn in fns:
            if fn.endswith(".schema.json"):
                p = os.path.join(root, fn)
                doc = load_schema(p)
                uri = Path(p).resolve().as_uri()
                store[uri] = doc
                if "$id" in doc:
                    store[doc["$id"]] = doc

    resolver = RefResolver(base_uri=base_uri, referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver)


CASES = [
    # (v1 manifest path, v2 stdlib schema path, description)
    ("examples/minimal/memory-policy.yaml", "schema/v2/stdlib/memory-policy.schema.json",
     "v1.2.0 MemoryPolicy example → v2 MemoryPolicy stdlib"),
    ("examples/minimal/workflow-type.yaml", "schema/v2/stdlib/workflow-type.schema.json",
     "v1.2.0 WorkflowType example → v2 WorkflowType stdlib"),
    ("examples/minimal/scope.yaml", "schema/v2/primitives/scope.schema.json",
     "v1.2.0 Scope example → v2 Scope primitive"),
]


# Synthetic minimal v1-shape fixtures for kinds without a committed example.
# Each dict is a valid v1.2.0 manifest for the named kind.
SYNTHETIC_CASES = [
    # (v1 manifest dict, v2 stdlib schema path, description)
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Agent",
            "metadata": {"name": "test-agent", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Test Agent",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a test agent.",
                "owner_principal_ref": "user:test@acme.com",
            },
        },
        "schema/v2/stdlib/agent.schema.json",
        "v1.2.0 minimal Agent → v2 Agent stdlib",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Agent",
            "metadata": {"name": "coord-agent", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Coordinator Agent",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You coordinate.",
                "owner_principal_ref": "user:test@acme.com",
                "coordinator_role": True,
                "task_kinds": ["coordination", "routing"],
                "memory_permissions": ["home.research", "home.ops"],
                "reranker_model": "claude-haiku-3-5",
            },
        },
        "schema/v2/stdlib/agent.schema.json",
        "v1.2.0 Agent with 1.2.0 extensions → v2 Agent stdlib",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Skill",
            "metadata": {"name": "test-skill", "ou_path": "/acme/eng"},
            "spec": {"display_name": "Test Skill"},
        },
        "schema/v2/stdlib/skill.schema.json",
        "v1.2.0 minimal Skill → v2 Skill stdlib",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Skill",
            "metadata": {"name": "sys-skill", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "System Skill",
                "system": True,
                "auto_attach_to": {
                    "agent_kinds": ["user"],
                    "task_kinds": ["qa"],
                    "coordinator_role_required": False,
                },
            },
        },
        "schema/v2/stdlib/skill.schema.json",
        "v1.2.0 System Skill with auto_attach_to → v2 Skill stdlib",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "MCPDeployment",
            "metadata": {"name": "team-files", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Team Files",
                "template_kind": "files",
                "isolation_mode": "shared",
                "config": {"bucket_prefix": "team-files"},
            },
        },
        "schema/v2/stdlib/mcp-deployment.schema.json",
        "v1.2.0 MCPDeployment → v2 MCPDeployment stdlib",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "Workflow",
            "metadata": {"name": "support-triage", "ou_path": "/acme/eng"},
            "spec": {
                "display_name": "Support Triage",
                "status": "draft",
                "nodes": [
                    {"id": "t1", "kind": "trigger", "trigger_kind": "manual"},
                    {"id": "a1", "kind": "agent", "agent_ref": "triage-agent"},
                ],
                "edges": [{"from": "t1", "to": "a1"}],
            },
        },
        "schema/v2/stdlib/workflow.schema.json",
        "v1.2.0 Workflow → v2 Workflow stdlib",
    ),
    (
        {
            "apiVersion": "powerloom.app/v1",
            "kind": "OU",
            "metadata": {"name": "engineering", "parent_ou_path": "/acme"},
            "spec": {"display_name": "Engineering"},
        },
        "schema/v2/stdlib/ou.schema.json",
        "v1.2.0 OU → v2 OU stdlib",
    ),
]


def load_yaml(path: str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def bump_api_version(doc: dict) -> dict:
    """Simulate `weave migrate v1.2->2.0` — bump apiVersion, leave rest."""
    doc = dict(doc)
    doc["apiVersion"] = "powerloom.app/v2"
    return doc


def _validate(doc: dict, schema_path: str, desc: str, failures: list) -> None:
    validator = make_validator(schema_path)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        failures.append(f"FAIL {desc}:")
        for err in errors:
            loc = ".".join(str(p) for p in err.path) or "<root>"
            failures.append(f"    at {loc}: {err.message}")
    else:
        print(f"OK   {desc}")


def main() -> int:
    failures: list[str] = []

    for v1_path, v2_schema_path, desc in CASES:
        if not os.path.exists(v1_path):
            failures.append(f"SKIP {desc}: {v1_path} missing")
            continue
        v1_doc = load_yaml(v1_path)
        v2_doc = bump_api_version(v1_doc)
        _validate(v2_doc, v2_schema_path, desc, failures)

    for v1_dict, v2_schema_path, desc in SYNTHETIC_CASES:
        v2_doc = bump_api_version(v1_dict)
        _validate(v2_doc, v2_schema_path, desc, failures)

    if failures:
        print()
        print("Shape-parity failures:")
        for f in failures:
            print(f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
