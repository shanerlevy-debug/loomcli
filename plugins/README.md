# Powerloom Client Plugins

Powerloom ships plugin packages for agent clients that can drive the `weave` CLI.

## Packages

- **Claude Code:** `../plugin`
  - Existing Powerloom Home plugin with slash commands, a local MCP server, hooks, and the `weave-interpreter` skill.
- **OpenAI Codex:** `codex/powerloom-weave`
  - Codex plugin package with a `weave-interpreter` skill focused on `weave ask`, `weave chat`, manifests, auth, and troubleshooting.
- **Gemini CLI:** `gemini/powerloom-weave`
  - Gemini extension package with `GEMINI.md` context and custom command TOML files.

## Provider-Agnostic Rule

All plugins should route agent invocation through `weave ask` or `weave chat`, not direct provider SDK calls. The Powerloom control plane chooses the target runtime/model from the Agent row and uses the user/org runtime credential already configured in Powerloom.

## Local Development

```bash
# Claude Code
claude --plugin-dir /path/to/loomcli/plugin

# Codex
# Add plugins/codex/powerloom-weave to a Codex plugin marketplace or load as a local plugin.

# Gemini CLI
gemini extensions link /path/to/loomcli/plugins/gemini/powerloom-weave
```
