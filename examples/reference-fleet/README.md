# Powerloom Reference Fleet

A complete, working Powerloom deployment you can ship end-to-end to a real control plane with one command. **20 agents + 22 skills + 2 OUs, with real system prompts and archive content.** Designed to be useful as-is (your team uses these agents) and useful as a reference (you fork and adapt for your own fleet).

## What's included

### OUs (2)
- `studio` — the Bespoke-specific agents (brand-director, developer, head-developer, journalist, memory-architect)
- `fleet-demo` — 15 generic role-based agents useful across industries

### Studio agents (5)
Shane's original set — the Bespoke team:
| Agent | Role |
|---|---|
| `brand-director` | Voice + copy approval authority |
| `developer` | End-to-end feature implementation |
| `head-developer` | Architecture decisions + PR authority + shipping gate |
| `journalist` | Essays, field notes, dispatches for brand media |
| `memory-architect` | Custodian of the memory/schema system |

### Fleet-demo agents (15)
Generic roles that apply to most small-to-medium businesses:
| Agent | Role |
|---|---|
| `qa-engineer` | E2E testing, regression coverage |
| `product-manager` | Scope + roadmap + release notes |
| `security-reviewer` | Authz audit, threat modeling |
| `technical-writer` | Docs currency + changelogs |
| `devops-engineer` | Reconciler, migrations, deploys |
| `research-assistant` | Gather + synthesize + cite |
| `data-analyst` | SQL + reports |
| `customer-support` | Ticket triage + first response |
| `sales-development-rep` | Cold outreach + qualification |
| `recruiter` | Resume review + interview prep |
| `legal-reviewer` | Contract review (flags for human counsel) |
| `financial-analyst` | Variance + forecasts |
| `executive-assistant` | Calendar, email, travel |
| `project-manager` | Status reports + dependency tracking |
| `ux-researcher` | Interview synthesis + usability |

### Skills (22)
Each skill has a real `SKILL.md` with frontmatter + role-specific system prompt. They're grouped:

**Studio skills (7)** — used by Shane's 5 agents (some shared):
- `bespoke-brand-style` — the BRAND.md-wrapper skill
- `copy-reviewer` — universal copywriting review
- `code-reviewer` — diff review for correctness + security
- `test-runner` — execute + interpret test suites
- `architecture-analyzer` — design review (coupling / reversibility / operability / cost)
- `article-drafter` — essay / field note / dispatch structural discipline
- `convention-curator` — intent vs. observation convention maintenance

**Fleet-demo skills (15)** — one per generic role above.

## Deploy

Requires:
- `pip install loomcli>=0.5.2` (must include `weave skill upload` commands)
- A Powerloom control plane you have credentials for (prod or local docker-compose)
- An existing root OU (default: `/bespoke-technology` — override with `OU_ROOT` env var)

Two bootstrap scripts are provided, pick the one matching your shell:

### macOS / Linux (bash)

```bash
cd examples/reference-fleet
weave login
./bootstrap.sh

# Optional flags
OU_ROOT=/my-org ./bootstrap.sh          # different root OU
SCHEMA_VERSION=v1.2.0 ./bootstrap.sh    # force older schema
DRY_RUN=1 ./bootstrap.sh                # preview without applying
```

Bash version requires `zip` and `bash` on PATH.

### Windows (PowerShell)

```powershell
cd D:\PowerLoom\loomcli\examples\reference-fleet
weave login
.\bootstrap.ps1

# Optional flags
$env:OU_ROOT = "/my-org"; .\bootstrap.ps1    # different root OU
.\bootstrap.ps1 -SchemaVersion v1.2.0        # force older schema
.\bootstrap.ps1 -DryRun                      # preview without applying
```

PowerShell version uses native `Compress-Archive` — no `zip` dependency. Requires PowerShell 5+ (ships with Windows 10+).

**Execution policy:** if PowerShell refuses to run the script ("script execution is disabled on this system"), either:
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser  # one-time, persistent
# OR
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1  # one-off
```

### What both scripts do

1. Validate `weave` auth (`weave auth whoami` must succeed)
2. Apply the 2 OU manifests (idempotent — skips existing)
3. Apply the 22 skill shells (`current_version_id: null` initially)
4. Build zip archives from `skill-archives/<name>/` + `weave skill upload-and-activate`s each
5. Apply the 20 agent manifests
6. Print summary + verification commands

Expected runtime: ~30-60 seconds against a responsive API.

## Verify

After bootstrap:
```bash
weave get ou
weave get skill
weave get agent
```

You should see:
- 3 OUs (your root + studio + fleet-demo)
- 22 skills
- 20 agents

## Directory layout

```
reference-fleet/
├── README.md                    # this file
├── bootstrap.sh                 # orchestrator (macOS / Linux)
├── bootstrap.ps1                # orchestrator (Windows PowerShell)
├── skill-archives/              # 22 directories, each with SKILL.md
│   ├── bespoke-brand-style/
│   │   └── SKILL.md
│   ├── code-reviewer/
│   │   └── SKILL.md
│   └── ... (20 more)
├── v1.2.0/                      # schema 1.2.0 manifests (pre-Chomskian primitives)
│   ├── ous/
│   │   ├── studio.yaml
│   │   └── fleet-demo.yaml
│   ├── skills/                  # 22 skill manifests
│   └── agents/                  # 20 agent manifests
└── v2.0.0/                      # schema 2.0.0 manifests (Chomskian 6 + stdlib derivations)
    ├── ous/
    ├── skills/
    └── agents/
```

The v2.0.0 manifests are the primary version (what `bootstrap.sh` uses by default). v1.2.0 is the pre-Chomskian equivalent — identical shape except for `apiVersion`, used by shape-parity tests + as a fallback for engines still on the older schema.

## Customizing

### Add a new agent

1. Add a SKILL.md for any new skill the agent will use: `skill-archives/<skill-name>/SKILL.md` (frontmatter required: `name` + `description`)
2. Add the skill + agent entries to `scripts/generate_reference_fleet.py`'s `SKILLS` and `AGENTS` lists
3. Re-run `python scripts/generate_reference_fleet.py` — generates the manifest files
4. Re-run `python scripts/validate_reference_fleet.py` — verifies shape
5. Re-run `./bootstrap.sh` — deploys the new agent

### Change the OU root

The default is `/bespoke-technology`. Override per-invocation:
```bash
OU_ROOT=/my-org ./bootstrap.sh
```

Note: the OU manifest files reference `/bespoke-technology` as a parent path. If you want a different root, either:
- Create the new root first (`weave apply -f my-org-ou.yaml`)
- Or edit the `parent_ou_path` in `v2.0.0/ous/*.yaml` to point at your root

### Change the owner principal

Every agent manifest has `owner_principal_ref: user:shane.levy@bespoke-technology.com`. If you're running this on a different account:
- Edit `OWNER = ...` at the top of `scripts/generate_reference_fleet.py`
- Re-run the generator

## Limitations

- **The reference fleet is a demonstration, not a production deployment.** The agents have real system prompts but haven't been stress-tested for your specific organization's needs.
- **The skills are archive-type only.** No `tool_definition` skills in this reference set — those are a separate pattern.
- **No RBAC groups or role-bindings shipped.** The bootstrap assumes the deploying user has create permissions on the target OUs.
- **No MCP deployments.** Agents don't reference MCP servers in this set — add them via separate manifests if needed.

## Testing locally

Before shipping to production, smoke-test against docker-compose:

**macOS / Linux:**
```bash
cd /path/to/powerloom
docker compose up -d
weave login --dev-as test@dev.local --api-url http://localhost:8000
cd /path/to/loomcli/examples/reference-fleet
./bootstrap.sh
weave get agent --api-url http://localhost:8000
```

**Windows:**
```powershell
cd D:\path\to\powerloom
docker compose up -d
$env:POWERLOOM_API_BASE_URL = "http://localhost:8000"
weave login --dev-as test@dev.local
cd D:\path\to\loomcli\examples\reference-fleet
.\bootstrap.ps1
weave get agent
```

If the local deploy works, prod will too.

## Why this exists

Two purposes:

1. **A working fleet to use.** The 20 agents are real — the Bespoke team uses Studio; other orgs can adapt fleet-demo.
2. **A shape-parity test corpus.** Every manifest exists in both schema v1.2.0 and v2.0.0 form. The schema-v2 migration story is proven by these identical-except-for-apiVersion manifests validating cleanly against both schema versions (see `tests/schema/test_v2_schemas.py`).

When loomcli 0.6.0 ships (schema 2.0.0 with the Chomskian root primitives + `compose` operator), the migrate tool uses this fleet as its round-trip test fixture: apply in v1.2.0, migrate, re-apply in v2.0.0, verify no semantic drift.
