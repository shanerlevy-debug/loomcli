"""Powerloom Home — local MCP server.

Ships as part of the `powerloom-home` Claude Code plugin. Exposes
Powerloom control-plane operations as MCP tools, backed by SQLite
state on the user's machine. Zero infrastructure required for casual
users; advanced users can swap the backend for docker-compose Postgres.

Architecture:
  - Python MCP server (stdio transport) invoked by Claude Code when
    the plugin is enabled
  - SQLite database at $CLAUDE_PLUGIN_DATA/powerloom-home.sqlite
  - Tools mirror the Powerloom REST API's shape so skills + agents +
    manifests authored against home validate + apply cleanly against
    enterprise when the user upgrades
  - Reconciler runs as a background thread inside the MCP process

Tools exposed (v0.1 scaffold — expanding over subsequent releases):
  - list_ous / get_ou / create_ou
  - list_skills / get_skill / create_skill / upload_skill_archive
  - list_agents / get_agent / create_agent
  - apply_manifest (takes YAML, does the full parse+validate+apply)
  - whoami (for symmetry with the hosted API)

Not yet implemented (tracked; arriving in later plugin releases):
  - MCP deployment management (requires local MCP launcher)
  - Workflow type + memory policy (v1.2.0 kinds)
  - Approval gate (home defaults to allow_self_approval=True)
  - Background reconciler (stub only; v0.2)
  - Federation / multi-org (Layer C concern)
"""

__version__ = "0.1.0-dev"
