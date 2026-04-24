"""Per-kind Pydantic models + kind registry.

Each entry in KINDS describes a resource type the CLI knows how to
parse + plan + apply. The `metadata` and `spec` models are Pydantic
classes; the kind string (e.g. "Agent") is what appears in the
manifest's `kind:` field.

We hand-maintain these rather than importing powerloom_api.schemas
because (a) the CLI ships as a standalone binary and shouldn't pull
in FastAPI/SQLAlchemy, (b) the manifest surface is a deliberately
narrower projection of the server's schemas — manifests omit server-
computed fields like cma_*_id, created_at, etc.

When the server schema changes, update the matching CLI spec here.
The parser tests guard against obvious drift (unknown field names),
but type changes slip through — worth an OpenAPI-generated client
in Phase 6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


API_VERSION = "powerloom/v1"


# ---------------------------------------------------------------------------
# Metadata shapes — what's in `metadata:` per kind
# ---------------------------------------------------------------------------
class _BaseMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OUMetadata(_BaseMeta):
    """`ou` resources identify themselves by name + parent_ou_path.
    The root-most OU uses `parent_ou_path: null` (or omits it)."""

    name: str
    parent_ou_path: str | None = None


class OUPathScopedMetadata(_BaseMeta):
    """Shared metadata for resources owned by an OU (by path)."""

    name: str
    ou_path: str


class OUIdScopedMetadata(_BaseMeta):
    """Metadata for v1.2.0+ kinds (WorkflowType, MemoryPolicy, Scope)
    that identify their OU by UUID rather than path. Shape per the
    schema/v1/kinds/{workflow-type,memory-policy,scope}.schema.json
    definitions: metadata.ou_id is a UUID string."""

    name: str
    ou_id: str
    display_name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class GroupMembershipMetadata(_BaseMeta):
    """Group-membership rows are identified by the (group_path, member)
    pair. The member_ref is either `user:email` or `group:group_path`."""

    group_path: str  # /dev-org/engineering/senior-engineers
    member_ref: str  # "user:jane@dev.local" or "group:/dev-org/engineering/engineers"


class RoleBindingMetadata(_BaseMeta):
    """Role bindings are identified by (principal_ref, role, scope_ou_path, decision_type).
    principal_ref follows the same scheme as GroupMembershipMetadata."""

    principal_ref: str
    role: str
    scope_ou_path: str
    decision_type: Literal["allow", "deny"] = "allow"


class AgentSkillAttachmentMetadata(_BaseMeta):
    """Attachments are identified by agent_path + skill_path."""

    agent_path: str  # /dev-org/engineering/code-reviewer
    skill_path: str


class AgentMcpAttachmentMetadata(_BaseMeta):
    agent_path: str
    mcp_registration_path: str


class CredentialMetadata(_BaseMeta):
    agent_path: str
    mcp_registration_path: str


class SkillGrantMetadata(_BaseMeta):
    skill_path: str
    principal_ref: str


# ---------------------------------------------------------------------------
# Spec shapes — what's in `spec:` per kind
# ---------------------------------------------------------------------------
class _BaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OUSpec(_BaseSpec):
    display_name: str


class GroupSpec(_BaseSpec):
    display_name: str
    description: str | None = None


class GroupMembershipSpec(_BaseSpec):
    """No spec fields — membership is fully defined by metadata.
    Pydantic still needs a model so we can validate an empty-or-absent
    `spec:` block uniformly."""


class RoleBindingSpec(_BaseSpec):
    """All RoleBinding fields live in metadata for addressing purposes."""


class AutoAttachSelector(BaseModel):
    """v1.2.0 — predicate describing which agents a system skill
    auto-attaches to. All specified conditions are ANDed."""

    model_config = ConfigDict(extra="forbid")

    agent_kinds: list[Literal["user", "service"]] | None = None
    task_kinds: list[
        Literal["routing", "qa", "analogy", "execution", "coordination"]
    ] | None = None
    runtime_types: list[str] | None = None
    coordinator_role_required: bool = False


class SkillSpec(_BaseSpec):
    display_name: str
    description: str | None = None
    skill_type: Literal["archive", "tool_definition"] = "archive"
    tool_schema: dict[str, Any] | None = None
    # Manifests reference skill versions by id — local archive uploads
    # happen via REST; put the resulting uuid here.
    current_version_id: str | None = None
    # v1.2.0 — system skills that auto-attach to matching agents.
    system: bool = False
    auto_attach_to: AutoAttachSelector | None = None


class McpServerRegistrationSpec(_BaseSpec):
    """Manually registered MCP servers (BYO / pre-existing URL).
    Deployed MCP servers (kind: MCPDeployment) auto-create the
    matching registration — don't declare both for the same URL."""

    display_name: str
    url: str
    description: str | None = None


class McpDeploymentSpec(_BaseSpec):
    display_name: str
    template_kind: Literal[
        "files", "postgres", "slack", "echo", "powerloom_meta",
        "github", "google_drive", "notion", "jira", "confluence",
        "microsoft365", "salesforce", "zendesk", "hubspot", "linear",
    ]
    isolation_mode: Literal["shared", "dedicated"] = "shared"
    config: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class AgentSpec(_BaseSpec):
    display_name: str
    description: str | None = None
    model: str
    system_prompt: str
    runtime_type: Literal["cma"] = "cma"
    agent_kind: Literal["user", "service"] = "user"
    owner_principal_ref: str  # "user:email" or "user:email" for service kind
    # Convenience: inline attachments. Equivalent to declaring a
    # separate AgentSkill / AgentMCPServer resource, but more ergonomic
    # for the common case. Applier expands these at plan time.
    skills: list[str] = Field(default_factory=list)
    """Skill names (resolved within the agent's OU)."""
    mcp_servers: list[str] = Field(default_factory=list)
    """MCP registration names (resolved within the agent's OU)."""
    # v1.2.0 extension fields — memory / coordinator / task-kind routing.
    coordinator_role: bool = False
    """Marks an LLM-coordinator; auto-attaches grading skills at reconcile."""
    task_kinds: list[
        Literal["routing", "qa", "analogy", "execution", "coordination"]
    ] = Field(default_factory=list)
    """Which task kinds this agent handles. Empty = accepts all."""
    memory_permissions: list[str] = Field(default_factory=list)
    """Dotted scope refs the agent has memory read access to (v1.2.0)."""
    reranker_model: str | None = None
    """Optional override for the LLM-judge reranker model."""


class AgentSkillSpec(_BaseSpec):
    skill_version_id: str | None = None


class AgentMcpSpec(_BaseSpec):
    """No spec fields."""


class CredentialSpec(_BaseSpec):
    """No spec — bearer is minted server-side, stored in Secrets Manager.
    Manifest apply creates the credential if missing; no update path."""


# ---------------------------------------------------------------------------
# v1.2.0 new kinds: WorkflowType, MemoryPolicy, Scope
# ---------------------------------------------------------------------------


class WorkflowTypeSpec(_BaseSpec):
    """v1.2.0 — reusable workflow-type template."""

    coordinator_agent_id: str | None = None
    default_timeout_seconds: int = 3600
    memory_policy_id: str | None = None
    task_kinds: list[
        Literal["routing", "qa", "analogy", "execution", "coordination"]
    ] = Field(default_factory=list)
    runtime_targets: list[
        Literal[
            "cma", "openai", "anthropic_messages", "ollama",
            "bedrock", "vertex", "azure_openai", "mistral",
            "cohere", "langchain", "crewai", "autogen",
        ]
    ] = Field(default_factory=list)


class MemoryPolicySpec(_BaseSpec):
    """v1.2.0 — per-OU memory governance configuration."""

    review_cadence_hours: int = 24
    timeout_action: Literal["approve", "forget", "escalate"] = "forget"
    review_deadline_hours: int = 72
    tentative_weight: float = 0.25
    org_scope_requires_approval: bool = True
    max_memories_per_session: int = 50
    consolidation_gate: Literal["kairos", "none"] = "kairos"


class SelectiveInheritance(BaseModel):
    """v1.2.0 — scope's selective_inheritance block."""

    model_config = ConfigDict(extra="forbid")
    from_types: list[str] | None = None
    memory_kinds: list[
        Literal["grammar", "lexicon", "procedural", "concepts"]
    ] | None = None


class RetentionOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decay_days: int | None = None
    forget_after_days: int | None = None


class ScopeSpec(_BaseSpec):
    """v1.2.0 — explicit scope declaration with inheritance config."""

    parent_scope_ref: str | None = None
    inheritance_mode: Literal["full", "isolated", "selective"] = "full"
    selective_inheritance: SelectiveInheritance | None = None
    retention_override: RetentionOverride | None = None


class SkillGrantSpec(_BaseSpec):
    """No spec — grant is fully defined by metadata (skill + principal)."""


# ---------------------------------------------------------------------------
# Kind registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KindSpec:
    """Per-kind wiring: metadata model, spec model, and the display
    category the planner groups by (purely cosmetic for plan output)."""

    kind: str
    metadata_model: type[BaseModel]
    spec_model: type[BaseModel]
    category: str


KINDS: dict[str, KindSpec] = {
    "OU": KindSpec("OU", OUMetadata, OUSpec, "identity"),
    "Group": KindSpec("Group", OUPathScopedMetadata, GroupSpec, "identity"),
    "GroupMembership": KindSpec(
        "GroupMembership", GroupMembershipMetadata, GroupMembershipSpec, "identity"
    ),
    "RoleBinding": KindSpec(
        "RoleBinding", RoleBindingMetadata, RoleBindingSpec, "rbac"
    ),
    "Skill": KindSpec("Skill", OUPathScopedMetadata, SkillSpec, "content"),
    "MCPServerRegistration": KindSpec(
        "MCPServerRegistration",
        OUPathScopedMetadata,
        McpServerRegistrationSpec,
        "content",
    ),
    "MCPDeployment": KindSpec(
        "MCPDeployment", OUPathScopedMetadata, McpDeploymentSpec, "content"
    ),
    "Agent": KindSpec("Agent", OUPathScopedMetadata, AgentSpec, "agents"),
    "AgentSkill": KindSpec(
        "AgentSkill", AgentSkillAttachmentMetadata, AgentSkillSpec, "agents"
    ),
    "AgentMCPServer": KindSpec(
        "AgentMCPServer", AgentMcpAttachmentMetadata, AgentMcpSpec, "agents"
    ),
    "Credential": KindSpec(
        "Credential", CredentialMetadata, CredentialSpec, "secrets"
    ),
    "SkillAccessGrant": KindSpec(
        "SkillAccessGrant", SkillGrantMetadata, SkillGrantSpec, "rbac"
    ),
    # v1.2.0 new kinds — metadata uses ou_id (UUID) rather than ou_path.
    "WorkflowType": KindSpec(
        "WorkflowType", OUIdScopedMetadata, WorkflowTypeSpec, "content"
    ),
    "MemoryPolicy": KindSpec(
        "MemoryPolicy", OUIdScopedMetadata, MemoryPolicySpec, "governance"
    ),
    "Scope": KindSpec(
        "Scope", OUIdScopedMetadata, ScopeSpec, "identity"
    ),
}


def get_kind_spec(kind: str) -> KindSpec | None:
    return KINDS.get(kind)


# ---------------------------------------------------------------------------
# Runtime-typed Resource — what the parser yields and everything downstream
# consumes. Keeps the kind string + untyped metadata/spec dicts next to
# their parsed-typed counterparts for flexibility.
# ---------------------------------------------------------------------------
class Resource(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: str
    metadata: BaseModel
    spec: BaseModel
    # Source file + 1-based document index within that file — used
    # for error messages ("acme.yaml doc 3: …").
    source_file: str
    doc_index: int

    @property
    def address(self) -> str:
        """Canonical human-readable address for plan output and logs.
        Format varies by kind — the addressing module computes this."""
        from loomcli.manifest.addressing import resource_address
        return resource_address(self)
