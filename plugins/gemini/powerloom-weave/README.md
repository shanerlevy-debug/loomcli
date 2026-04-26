# Powerloom Weave for Gemini CLI

Gemini CLI extension for operating the Powerloom `weave` CLI.

## What It Provides

- `GEMINI.md` context for Powerloom/loomcli usage.
- Custom commands:
  - `/weave:onboard` — fresh-agent onboarding walk (install, sign in, load this extension, file your first tracker thread)
  - `/weave:ask`
  - `/weave:chat`
  - `/weave:status`
  - `/weave:plan`
  - `/weave:agent-status`
  - `/weave:session-tail`
  - `/weave:thread:create`
  - `/weave:thread:pluck`
  - `/weave:thread:reply`
  - `/weave:thread:done`
  - `/weave:thread:list`
  - `/weave:my-work`
  - `/weave:pluck-thread`

## Development Install

Recommended local install:

```bash
gemini extensions validate /path/to/loomcli/plugins/gemini/powerloom-weave
gemini extensions install /path/to/loomcli/plugins/gemini/powerloom-weave --consent --skip-settings
```

On this checkout:

```powershell
$env:GEMINI_CLI_NO_RELAUNCH = "true"
gemini extensions validate D:\powerloom\loomcli\plugins\gemini\powerloom-weave
gemini extensions install D:\powerloom\loomcli\plugins\gemini\powerloom-weave --consent --skip-settings
gemini extensions enable powerloom-weave
```

Use `gemini extensions link <path> --consent` only when you specifically want live edits reflected immediately. On Windows, `install` is safer because it copies the manifest and command files into `~/.gemini/extensions/powerloom-weave`.

Restart Gemini CLI or reload commands after installing.

The extension expects `weave` to be installed:

```bash
pip install -e /path/to/loomcli
weave --version
```

## Onboarding a fresh Gemini session

If you just installed this extension and want the full walk-through, run `/weave:onboard`. It covers loomcli install, authentication (against `api.powerloom.org` or a self-hosted control plane), the §4.10 tracker thread workflow, and pointers to the rest of the slash commands.
