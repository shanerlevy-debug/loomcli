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
weave plugin doctor

# Claude Code
claude --plugin-dir /path/to/loomcli/plugin

# Codex
# Codex installs marketplace roots. Point it at plugins/codex, not the package folder.
codex plugin marketplace add /path/to/loomcli/plugins/codex

# Gemini CLI
gemini extensions install /path/to/loomcli/plugins/gemini/powerloom-weave --consent --skip-settings
```

`weave plugin instructions <client>` prints the expected command for the current checkout. `weave plugin install <client>` is a dry run by default; pass `--execute` after reviewing the printed command.

On Windows, replace placeholder paths with real paths and do not type angle brackets such as `<loomcli>` in PowerShell; `<` is parsed as redirection. If Gemini CLI fails with `spawn EPERM`, run the install commands with `GEMINI_CLI_NO_RELAUNCH=true`.
