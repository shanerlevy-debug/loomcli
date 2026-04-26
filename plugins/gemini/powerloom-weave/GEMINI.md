# Powerloom Weave Extension

Use `weave`, the CLI from the `loomcli` package, to interact with Powerloom.

## Provider-Agnostic Agent Sessions

Do not call Anthropic, OpenAI, Gemini, or any provider SDK directly for a Powerloom agent session. Use:

```bash
weave ask <agent-uuid-or-/ou/path/name> "prompt"
weave chat <agent-uuid-or-/ou/path/name>
```

The Powerloom backend uses the target Agent's `runtime_type`, `model`, and the user/org runtime credential configured in Powerloom.

## Common Commands

```bash
weave login
weave whoami
weave ask /dev-org/alfred "What should I work on next?"
weave chat /dev-org/alfred
weave agent status /dev-org/alfred
weave agent config /dev-org/alfred
weave agent set-model /dev-org/alfred --model gpt-5.5
weave session tail <session-id>
weave profile set --default-agent /dev-org/alfred --default-runtime openai --default-model gpt-5.5
weave commands --json
weave plan manifest.yaml
weave apply manifest.yaml
weave get agents --ou /dev-org
weave describe agent /dev-org/alfred
```

If a mutation is approval-gated, use:

```bash
weave --justification "reason for this change" apply manifest.yaml
weave approval wait <approval-id>
```

Never print, store, or commit raw PATs.

## Agent/Session Observability

Use `weave agent status`, `weave agent sessions`, `weave session tail`, `weave agent-session status`, `weave agent-session watch`, and `weave thread my-work --watch` when the user asks what an agent or delegated coding session is doing. These are read-only runtime/coordination inspection commands and do not change manifests, provider, or model.

Use `weave agent config` and `weave agent set-model` for model inspection/update. Runtime/provider changes remain manifest-owned until Powerloom exposes a safe runtime patch endpoint; do not add provider/model flags to `weave ask` or `weave chat`.

## Tracker thread workflow (CLAUDE.md / GEMINI.md / AGENTS.md §4.10)

Per the project's working agreement, every agent session — including this Gemini CLI session — registers its work as a tracker thread in the relevant Powerloom project. Use the `/weave:thread:*` commands or invoke `weave thread` directly:

```bash
# At session start, file a thread for the work
weave thread create --project powerloom --title "<imperative phrase>" --priority high --description "<context, repro, definition-of-done>"

# Claim it (sets status=in_progress)
weave thread pluck <thread_id>

# As decisions/blockers come up, post replies
weave thread reply <thread_id> "decision: chose option A because..."

# At PR merge, mark done
weave thread done <thread_id>
```

Read the canonical example threads under the `powerloom` project (ids `8d2c7502`, `c41a8294`, `3671bfaf`, `9210c2c2`, `e011a581`, `2be84503`, `a0a715dc`) with `weave thread show <id>` to see the canonical description structure: context-up-front, repro/current-state, definition-of-done, out-of-scope.

If the `weave thread …` subcommand family isn't yet in your installed loomcli version (it's tracked as thread `2be84503`), fall back to direct API calls via `curl` against `POST /projects/{project_id}/threads` — the underlying engine endpoints exist; only the CLI sugar is in flight.
weave agent-session register --scope "<slug>" --summary "<one-line>" --branch "<branch>" --capabilities "<comma,tags>" --actor-kind gemini_cli
```

3. Use supported actor kinds such as `gemini_cli`, `codex_cli`, `antigravity`, `claude_code`, `cma`, or `human`. If an older control plane rejects the value, omit `--actor-kind` and mention the compatibility fallback.
4. If the user is not signed in or the API is unavailable, return the handoff summary and the exact registration command for later.
