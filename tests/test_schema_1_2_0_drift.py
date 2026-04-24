"""Tests for v1.2.0 Pydantic model coverage (v0.5.4).

Regression guard for the CLI-side Pydantic models drifting from the
v1.2.0 schema bundle. Before 0.5.4, the CLI schema was v1.2.0 but the
Pydantic models only covered the v1.1.0 shape — valid manifests using
the v1.2.0 additions (system + auto_attach_to on Skill; coordinator_role,
task_kinds, memory_permissions, reranker_model on Agent; new kinds
WorkflowType / MemoryPolicy / Scope) passed schema validation and then
failed with "Extra inputs are not permitted" on Pydantic parse.

Surfaced when Shane ran the reference-fleet bootstrap against prod:
bespoke-brand-style (which uses system + auto_attach_to) crashed.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from loomcli.manifest.schema import (
    KINDS,
    AgentSpec,
    AutoAttachSelector,
    MemoryPolicySpec,
    OUIdScopedMetadata,
    ScopeSpec,
    SkillSpec,
    WorkflowTypeSpec,
)


# ---------------------------------------------------------------------------
# Skill 1.2.0 additions
# ---------------------------------------------------------------------------


def test_skill_spec_accepts_system_flag():
    spec = SkillSpec(
        display_name="Test Skill",
        system=True,
    )
    assert spec.system is True
    assert spec.auto_attach_to is None


def test_skill_spec_default_system_false():
    spec = SkillSpec(display_name="Test")
    assert spec.system is False


def test_skill_spec_accepts_auto_attach_to_full_selector():
    spec = SkillSpec(
        display_name="Test",
        system=True,
        auto_attach_to={
            "agent_kinds": ["user", "service"],
            "task_kinds": ["qa", "execution"],
            "runtime_types": ["cma"],
            "coordinator_role_required": True,
        },
    )
    assert spec.auto_attach_to is not None
    assert spec.auto_attach_to.agent_kinds == ["user", "service"]
    assert spec.auto_attach_to.task_kinds == ["qa", "execution"]
    assert spec.auto_attach_to.coordinator_role_required is True


def test_skill_spec_auto_attach_to_partial_selector():
    """Empty object is valid — matches any agent."""
    spec = SkillSpec(
        display_name="Test",
        system=True,
        auto_attach_to={},
    )
    assert spec.auto_attach_to is not None
    assert spec.auto_attach_to.agent_kinds is None


def test_skill_spec_auto_attach_to_rejects_unknown_task_kind():
    with pytest.raises(ValidationError):
        SkillSpec(
            display_name="Test",
            system=True,
            auto_attach_to={"task_kinds": ["not-a-real-task-kind"]},
        )


def test_skill_spec_auto_attach_to_rejects_extra_fields():
    with pytest.raises(ValidationError) as exc_info:
        SkillSpec(
            display_name="Test",
            auto_attach_to={"what": "invalid"},
        )
    assert "Extra inputs" in str(exc_info.value) or "extra_forbidden" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Agent 1.2.0 additions
# ---------------------------------------------------------------------------


def test_agent_spec_accepts_coordinator_role():
    spec = AgentSpec(
        display_name="Test",
        model="claude-sonnet-4-6",
        system_prompt="You coordinate.",
        owner_principal_ref="user:test@example.com",
        coordinator_role=True,
    )
    assert spec.coordinator_role is True


def test_agent_spec_default_coordinator_role_false():
    spec = AgentSpec(
        display_name="Test",
        model="m",
        system_prompt="p",
        owner_principal_ref="user:t@t.com",
    )
    assert spec.coordinator_role is False
    assert spec.task_kinds == []
    assert spec.memory_permissions == []
    assert spec.reranker_model is None


def test_agent_spec_task_kinds_validates_enum():
    with pytest.raises(ValidationError):
        AgentSpec(
            display_name="Test",
            model="m",
            system_prompt="p",
            owner_principal_ref="user:t@t.com",
            task_kinds=["not-valid"],
        )


def test_agent_spec_memory_permissions_list_of_strings():
    spec = AgentSpec(
        display_name="Test",
        model="m",
        system_prompt="p",
        owner_principal_ref="user:t@t.com",
        memory_permissions=["home.research", "home.ops"],
    )
    assert spec.memory_permissions == ["home.research", "home.ops"]


def test_agent_spec_reranker_model_nullable():
    spec = AgentSpec(
        display_name="Test",
        model="m",
        system_prompt="p",
        owner_principal_ref="user:t@t.com",
        reranker_model="claude-haiku-3-5",
    )
    assert spec.reranker_model == "claude-haiku-3-5"


# ---------------------------------------------------------------------------
# New v1.2.0 kinds — WorkflowType, MemoryPolicy, Scope
# ---------------------------------------------------------------------------


def test_workflow_type_registered_in_kinds():
    assert "WorkflowType" in KINDS
    kind_spec = KINDS["WorkflowType"]
    assert kind_spec.metadata_model is OUIdScopedMetadata
    assert kind_spec.spec_model is WorkflowTypeSpec


def test_memory_policy_registered_in_kinds():
    assert "MemoryPolicy" in KINDS
    kind_spec = KINDS["MemoryPolicy"]
    assert kind_spec.metadata_model is OUIdScopedMetadata
    assert kind_spec.spec_model is MemoryPolicySpec


def test_scope_registered_in_kinds():
    assert "Scope" in KINDS
    kind_spec = KINDS["Scope"]
    assert kind_spec.metadata_model is OUIdScopedMetadata
    assert kind_spec.spec_model is ScopeSpec


def test_ou_id_scoped_metadata_basics():
    meta = OUIdScopedMetadata(
        name="my-thing",
        ou_id="9d1c0e36-2a4a-4aec-905b-c1e2a4f7e5b1",
    )
    assert meta.name == "my-thing"
    assert meta.display_name is None


def test_ou_id_scoped_metadata_with_optional_fields():
    meta = OUIdScopedMetadata(
        name="my-thing",
        ou_id="9d1c0e36-2a4a-4aec-905b-c1e2a4f7e5b1",
        display_name="My Thing",
        description="A thing.",
        labels={"env": "prod", "team": "platform"},
    )
    assert meta.display_name == "My Thing"
    assert meta.labels == {"env": "prod", "team": "platform"}


def test_ou_id_scoped_metadata_rejects_extras():
    with pytest.raises(ValidationError):
        OUIdScopedMetadata(
            name="x",
            ou_id="9d1c0e36-2a4a-4aec-905b-c1e2a4f7e5b1",
            random_field="nope",
        )


def test_workflow_type_spec_defaults():
    spec = WorkflowTypeSpec()
    assert spec.coordinator_agent_id is None
    assert spec.default_timeout_seconds == 3600
    assert spec.task_kinds == []
    assert spec.runtime_targets == []


def test_workflow_type_spec_runtime_targets_enum():
    spec = WorkflowTypeSpec(
        runtime_targets=["cma", "anthropic_messages"],
    )
    assert "anthropic_messages" in spec.runtime_targets
    with pytest.raises(ValidationError):
        WorkflowTypeSpec(runtime_targets=["nonexistent-runtime"])


def test_memory_policy_spec_defaults():
    spec = MemoryPolicySpec()
    assert spec.review_cadence_hours == 24
    assert spec.timeout_action == "forget"
    assert spec.tentative_weight == 0.25
    assert spec.org_scope_requires_approval is True
    assert spec.consolidation_gate == "kairos"


def test_memory_policy_spec_custom_values():
    spec = MemoryPolicySpec(
        review_cadence_hours=12,
        timeout_action="escalate",
        tentative_weight=0.5,
        consolidation_gate="none",
    )
    assert spec.review_cadence_hours == 12
    assert spec.timeout_action == "escalate"
    assert spec.tentative_weight == 0.5
    assert spec.consolidation_gate == "none"


def test_memory_policy_spec_rejects_invalid_timeout_action():
    with pytest.raises(ValidationError):
        MemoryPolicySpec(timeout_action="delete-everything-now")


def test_scope_spec_defaults():
    spec = ScopeSpec()
    assert spec.parent_scope_ref is None
    assert spec.inheritance_mode == "full"


def test_scope_spec_selective_inheritance():
    spec = ScopeSpec(
        inheritance_mode="selective",
        selective_inheritance={
            "from_types": ["ContractClause"],
            "memory_kinds": ["grammar", "lexicon"],
        },
    )
    assert spec.selective_inheritance is not None
    assert spec.selective_inheritance.from_types == ["ContractClause"]
    assert spec.selective_inheritance.memory_kinds == ["grammar", "lexicon"]


def test_scope_spec_retention_override():
    spec = ScopeSpec(
        retention_override={"decay_days": 30, "forget_after_days": 180}
    )
    assert spec.retention_override is not None
    assert spec.retention_override.decay_days == 30
    assert spec.retention_override.forget_after_days == 180


def test_scope_spec_rejects_invalid_memory_kind():
    with pytest.raises(ValidationError):
        ScopeSpec(
            inheritance_mode="selective",
            selective_inheritance={"memory_kinds": ["not-a-kind"]},
        )


# ---------------------------------------------------------------------------
# End-to-end — reference-fleet Skill manifest with system + auto_attach_to
# (the exact shape that crashed Shane's bootstrap)
# ---------------------------------------------------------------------------


def test_reference_fleet_bespoke_brand_style_shape():
    """The manifest that crashed pre-0.5.4. Now validates cleanly."""
    spec = SkillSpec(
        display_name="Bespoke Brand Style Manager",
        description="Reviews copy for adherence to BRAND.md.",
        skill_type="archive",
        current_version_id=None,
        system=True,
        auto_attach_to={"task_kinds": ["qa", "execution"]},
    )
    assert spec.system is True
    assert spec.auto_attach_to.task_kinds == ["qa", "execution"]


def test_reference_fleet_head_developer_agent_shape():
    """Agent with all v1.2.0 fields used together."""
    spec = AgentSpec(
        display_name="Head Developer",
        model="claude-sonnet-4-6",
        system_prompt="You make decisions.",
        owner_principal_ref="user:test@example.com",
        skills=["code-reviewer", "architecture-analyzer", "convention-curator"],
        coordinator_role=True,
        task_kinds=["coordination", "qa"],
    )
    assert spec.coordinator_role is True
    assert "coordination" in spec.task_kinds
