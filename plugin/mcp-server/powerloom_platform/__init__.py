"""Powerloom Platform — stdio→HTTP MCP bridge for the hosted control plane.

Companion to ``powerloom_home`` (which ships local SQLite-backed tools).
Where ``powerloom_home`` is offline-first and home-edition only, this
bridge exposes the **hosted** Powerloom platform's MCP tools to Claude
Code sessions in any working directory.

Why a bridge: ``weave register --token=...`` writes a deployment
credential to ``%APPDATA%\\powerloom\\powerloom\\deployment-claude_code.json``
(or ``$XDG_CONFIG_HOME/powerloom/`` on POSIX). The credential carries
the API base URL + the bearer token. CC sessions need a way to consume
that credential without the operator copying ``.mcp.json`` snippets
into every project. The bridge MCP server boots from the plugin
manifest (so it auto-loads on every CC launch), reads the credential
at startup, and proxies all MCP traffic to ``{api_base_url}/mcp``.

Graceful degradation: if no credential exists OR the credential is
malformed OR the upstream platform is unreachable, the bridge boots
cleanly and exposes zero tools. Operators who haven't run
``weave register`` yet see no errors — just nothing in their tool
list. Once they register and restart CC, the platform tools appear.

Filed under tracker ``e1e61ca6`` ("Plugin: bridge hosted Powerloom MCP
tools into CC sessions outside the monorepo (Fix B)") after the v290
ship of PR #290 (CMA push gating refactor).
"""

__version__ = "0.1.0-dev"
