---
description: Explain + exercise the local home-mode MCP server. Demonstrates creating an OU, skill, and agent fully locally with no remote Powerloom account.
---

# /powerloom-home:home-mode

The user wants to use Powerloom Home — the local MCP-backed mode.

**Confirm the MCP server is live:** call the `powerloom_whoami` tool. It should return:
```json
{
  "mode": "home",
  "org_id": "<uuid>",
  "db_path": "<path to sqlite file>",
  "version": "0.1.0-dev"
}
```

**If the tool call fails or times out:**
- The `powerloom-home` MCP server isn't running. Tell the user to reinstall the plugin via `claude plugin install` or restart Claude Code with the plugin enabled.
- If `$CLAUDE_PLUGIN_DATA` is missing, the directory wasn't created — check `plugin.json` mcpServers env configuration.

**Demo flow (ask user first):**

If the user wants a demo of home mode, walk them through creating a minimal fleet:

1. Create a home OU: `powerloom_create_ou(name="home", display_name="Home")`
2. Create a project child OU: `powerloom_create_ou(name="projects", parent_path="/home", display_name="Projects")`
3. Create one agent: `powerloom_create_agent(ou_path="/home/projects", name="assistant", display_name="Assistant", model="claude-sonnet-4-5", system_prompt="You help with tasks.", owner_principal_ref="user:local@home")`
4. Verify: `powerloom_list_agents(ou_path="/home/projects")`
5. Show recent audit: `powerloom_recent_audit(limit=10)`

After the demo, tell the user that everything they just created lives in `$CLAUDE_PLUGIN_DATA/powerloom-home.sqlite`. Persists across Claude Code restarts. To migrate to enterprise (api.powerloom.org) later, use the pending `weave home-to-enterprise migrate` tool (not yet shipped; tracked for v0.2 of the plugin).

**Differences from hosted (api.powerloom.org):**
- No multi-tenant — single home org
- No approval gates, no RBAC, no audit-chain hashing
- No background reconciler yet (stub in v0.1; real one lands v0.2)
- SQLite persistence only; upgrade to docker-compose Postgres coming in v2.1

**When to use home vs. hosted:**
- **Home:** solo use, zero-infra, local-only, no cloud dependency
- **Hosted:** team use, full RBAC + approvals, multi-device, production SLA
- **Future Supporter tier:** $5/mo, Powerloom hosts your home backend on a subdomain — combines ease of home with reachability of hosted
