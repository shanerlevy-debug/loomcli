"""Mirror of the engine's ``LaunchSpec`` for ``weave open`` consumption.

Hand-synced from
``api/powerloom_api/schemas/launch_spec.py`` in
github.com/shanerlevy-debug/Powerloom (canonical source). The engine
also publishes a JSON Schema at
``api/powerloom_api/schemas/launch_spec.v1.json``; bumping this mirror
when fields change is the operator's job until we wire automatic
generation from that artifact.

``extra="ignore"`` (not ``"forbid"`` like the engine side) — the CLI
should be forward-compatible with new fields the engine adds. New
fields are ignored until the mirror catches up; missing required
fields are still validated.

Sprint: ``cli-weave-open-20260430`` under milestone "Weave Open Launch
UX 20260430". Initial thread: ``c78ead6d``
(``weave open <token>: skeleton + redeem call``).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- enums (inline Literals matching the engine side) ---------------------

LaunchRuntime = Literal[
    "claude_code",
    "codex_cli",
    "gemini_cli",
    "antigravity",
]

CloneAuthMode = Literal[
    "server_minted",
    "local_credentials",
    "hybrid",
]


# ---- nested submodels -----------------------------------------------------


class LaunchActor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: uuid.UUID
    email: str
    runtime: LaunchRuntime


class LaunchProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    slug: str
    repo_url: str
    default_branch: str = "main"


class LaunchScope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str
    friendly_name: Optional[str] = None
    branch_base: str = "main"
    branch_name: str


class LaunchSkill(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str
    version: str


class CloneAuth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: CloneAuthMode
    token: Optional[str] = None
    expires_at: Optional[datetime] = None
    hint: Optional[str] = None


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    attach_token: Optional[str] = None


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    servers: list[McpServerConfig] = Field(default_factory=list)


class RulesSyncDirective(BaseModel):
    """Convention-scope sync to run after worktree creation.

    Iterating over ``LaunchSpec.rules_sync`` and invoking
    ``weave conventions sync --scope <scope> --runtime <r> --workdir <wt>``
    once per ``(directive, runtime)`` pair is the contract. Lands in
    thread ``53fddf29`` (apply rules_sync directives in weave open).
    """

    model_config = ConfigDict(extra="ignore")

    scope: str
    runtimes: list[LaunchRuntime]


# ---- top-level spec --------------------------------------------------------


class LaunchSpec(BaseModel):
    """The full payload returned by ``GET /launches/{token}``."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1

    launch_id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    redeemed_at: Optional[datetime] = None

    actor: LaunchActor
    project: LaunchProject
    scope: LaunchScope
    runtime: LaunchRuntime
    skills: list[LaunchSkill] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)

    clone_auth: CloneAuth
    mcp_config: McpConfig = Field(default_factory=McpConfig)
    rules_sync: list[RulesSyncDirective] = Field(default_factory=list)

    session_attach_token: Optional[str] = None
    thread_id: Optional[uuid.UUID] = None
