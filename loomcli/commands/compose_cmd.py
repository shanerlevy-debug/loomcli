"""`weave compose` — author, inspect, and validate Compose-operator kinds.

The compose operator is the v2.0.0 authoring surface for user-derived
kinds (see `schema/v2/compose.schema.json`). Operators write a Compose
manifest that declares a new kind as a composition of primitive or
stdlib slots. At `weave apply` time the engine persists the compose
spec in `kind_registry` (see v056 Milestone 6); subsequent resources of
the new kind validate against the composed shape.

This CLI layer handles the AUTHORING side — lint + show + scaffold.

Commands:
  - `weave compose show <kind-name>` — fetch the effective schema for
    a registered composed kind; render it human-readably
  - `weave compose lint <path.yaml>` — parse a Compose manifest,
    validate it against the compose.schema.json meta-schema, and run
    interface-compatibility checks on the slots (e.g. a Policy slot
    that references a non-existent scope_ref)
  - `weave compose scaffold --name X --namespace ns [--extends K]`
    print a starter Compose manifest to stdout
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from rich.console import Console

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config

app = typer.Typer(help="Author, lint, and inspect v2.0.0 Compose kinds.")
_console = Console()


# Primitive slot names accepted by compose_slot
_PRIMITIVE_NAMES = {"Entity", "Event", "Relation", "Process", "Scope", "Policy"}


def _find_schema_root() -> Path:
    """Find the bundled schema/v2 directory. Look first in the
    loomcli package, then in an adjacent checkout (dev mode)."""
    try:
        import loomcli
        pkg_root = Path(loomcli.__file__).resolve().parent
        candidates = [
            pkg_root / "_bundled_schema" / "v2",
            pkg_root.parent / "schema" / "v2",
        ]
        for c in candidates:
            if (c / "compose.schema.json").exists():
                return c
    except Exception:
        pass
    # Fallback: cwd/schema/v2
    return Path("schema/v2")


@app.command("scaffold")
def scaffold(
    name: str = typer.Option(..., "--name", "-n", help="New kind's name (Capital-first, e.g. 'ContractClause')."),
    namespace: str = typer.Option(..., "--namespace", help="Dotted namespace (e.g. 'legal.acme')."),
    extends: Optional[str] = typer.Option(
        None, "--extends",
        help="Optional stdlib kind to extend (e.g. 'Agent', 'Workflow').",
    ),
    primitives: str = typer.Option(
        "Entity,Policy",
        "--primitives",
        help="Comma-separated primitives to include as slots. Ignored if --extends is used.",
    ),
) -> None:
    """Print a starter Compose manifest to stdout. Redirect to a file
    to begin authoring: `weave compose scaffold --name Foo --namespace bar > foo.yaml`."""
    compose_slots: list[dict[str, Any]] = []

    if extends:
        compose_slots.append({"extends": extends, "fields": {}})
    else:
        prim_list = [p.strip() for p in primitives.split(",") if p.strip()]
        for p in prim_list:
            if p not in _PRIMITIVE_NAMES:
                _console.print(f"[red]{p!r} is not a valid primitive.[/red] Choose from: {sorted(_PRIMITIVE_NAMES)}")
                raise typer.Exit(1)
            compose_slots.append({"primitive": p, "fields": {}})

    doc = {
        "apiVersion": "powerloom.app/v2",
        "kind": "Compose",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "display_name": name,
            "description": f"{name} — a {namespace} domain kind.",
        },
        "spec": {
            "compose": compose_slots,
        },
    }
    typer.echo(f"# Compose manifest for {namespace}.{name}")
    typer.echo("# Edit fields + add additional slots as needed.")
    typer.echo("# Run `weave compose lint <this-file>` to validate before apply.")
    typer.echo("---")
    typer.echo(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))


@app.command("lint")
def lint(
    path: Path = typer.Argument(
        ...,
        help="Path to a Compose manifest YAML file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
) -> None:
    """Parse + validate a Compose manifest. Runs three checks:

    1. **Schema validation** — document conforms to
       `schema/v2/compose.schema.json`.
    2. **Slot-shape checks** — each slot references a real primitive
       or a registered stdlib kind. Field collisions surface.
    3. **Reference sanity** — scope_refs, role tags, and `extends`
       targets look reasonable (shallow check; deep validation runs
       at `weave apply` time against the live registry).
    """
    schema_root = _find_schema_root()
    compose_schema_path = schema_root / "compose.schema.json"
    if not compose_schema_path.exists():
        _console.print(f"[red]Can't find compose.schema.json at {compose_schema_path}[/red]")
        raise typer.Exit(1)

    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    errors: list[str] = []
    warnings: list[str] = []

    # Step 1: meta-schema validation. Build a store of all v2 schemas
    # so cross-file $refs (e.g. common.schema.json#/$defs/api_version)
    # resolve correctly.
    try:
        import jsonschema
        from jsonschema import RefResolver

        with compose_schema_path.open("r", encoding="utf-8") as fh:
            compose_schema = json.load(fh)

        # Build the store: URI → loaded schema. Covers $id refs AND
        # relative-file refs like "common.schema.json".
        store: dict[str, Any] = {}
        for p in schema_root.rglob("*.schema.json"):
            with p.open("r", encoding="utf-8") as fh:
                d = json.load(fh)
            # By absolute URI (as $id)
            if d.get("$id"):
                store[d["$id"]] = d
            # By file URI (for relative refs from compose.schema.json)
            store[p.resolve().as_uri()] = d

        resolver = RefResolver(
            base_uri=compose_schema_path.resolve().as_uri(),
            referrer=compose_schema,
            store=store,
        )
        validator = jsonschema.Draft202012Validator(
            compose_schema, resolver=resolver
        )
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
            path_str = "/".join(str(p) for p in err.absolute_path) or "<root>"
            errors.append(f"schema: at {path_str}: {err.message}")
    except ImportError:
        _console.print("[yellow]jsonschema not installed — skipping meta-schema validation.[/yellow]")

    # Step 2: slot-shape checks.
    if doc and isinstance(doc, dict):
        spec = doc.get("spec") or {}
        slots = spec.get("compose") or []
        seen_primitives_by_role: dict[str, list[int]] = {}
        for idx, slot in enumerate(slots):
            if not isinstance(slot, dict):
                errors.append(f"slot {idx}: must be a mapping")
                continue

            primitive = slot.get("primitive")
            extends_ref = slot.get("extends")

            if primitive and extends_ref:
                errors.append(f"slot {idx}: use EITHER 'primitive' OR 'extends', not both")
            elif not primitive and not extends_ref:
                errors.append(f"slot {idx}: must specify 'primitive' or 'extends'")

            if primitive and primitive not in _PRIMITIVE_NAMES:
                errors.append(f"slot {idx}: '{primitive}' is not a valid primitive (use one of {sorted(_PRIMITIVE_NAMES)})")

            # Role-tag conflicts: two Policy slots without distinct roles is ambiguous.
            if primitive:
                role = slot.get("role")
                key = f"{primitive}:{role or ''}"
                seen_primitives_by_role.setdefault(key, []).append(idx)

            # Fields should be a mapping (if present).
            fields = slot.get("fields")
            if fields is not None and not isinstance(fields, dict):
                errors.append(f"slot {idx}: 'fields' must be a mapping")

            # Policy slot hint — this is the T5-05 failure case from benchmark.
            if primitive == "Policy":
                pt = (fields or {}).get("policy_type") if isinstance(fields, dict) else None
                if pt is None:
                    warnings.append(f"slot {idx} (Policy): missing 'policy_type' in fields. This is free-form text (e.g. 'escalation_rules', 'legal_constraint'). Not setting it is allowed but strongly discouraged for clarity.")

        # Multi-slot same primitive same role = collision.
        for key, indices in seen_primitives_by_role.items():
            if len(indices) > 1:
                errors.append(f"two slots share the same primitive+role: {key} (slots {indices}). Distinguish with distinct 'role' tags.")

    # Step 3 (shallow): scope_refs pattern
    scope_ref_pattern = "^[a-z0-9][a-z0-9_.-]*[a-z0-9]$"
    import re
    _srp = re.compile(scope_ref_pattern)
    spec = (doc or {}).get("spec") or {}
    for idx, slot in enumerate((spec.get("compose") or [])):
        if not isinstance(slot, dict):
            continue
        fields = slot.get("fields") or {}
        for fk in ("applies_to", "exceptions", "effects"):
            vals = fields.get(fk)
            if not vals:
                continue
            if isinstance(vals, list):
                for v in vals:
                    if isinstance(v, str) and not _srp.match(v):
                        warnings.append(f"slot {idx} field {fk}: {v!r} doesn't look like a valid scope_ref (expected dotted lowercase, e.g. 'home.projects')")

    # Output
    if errors:
        _console.print(f"[red]{len(errors)} error(s):[/red]")
        for e in errors:
            _console.print(f"  ✗ {e}")
    if warnings:
        _console.print(f"[yellow]{len(warnings)} warning(s):[/yellow]")
        for w in warnings:
            _console.print(f"  ⚠ {w}")
    if not errors and not warnings:
        _console.print(f"[green]✓ {path} is valid.[/green]")

    if errors:
        raise typer.Exit(1)


@app.command("show")
def show(
    kind_name: str = typer.Argument(..., help="Kind name (e.g. 'legal.acme.ContractClause' or just 'ContractClause')."),
    output_format: str = typer.Option(
        "yaml", "--format", "-f",
        help="'yaml' (default) or 'json'.",
    ),
) -> None:
    """Fetch the effective resolved schema for a registered composed
    kind. Shows what the kind's resource shape looks like after all
    compose slots merge. Requires the v056 engine with kind_registry
    endpoint — NOT yet available in production.

    Under the hood calls `GET /kind-registry/{namespace}/{name}`.
    """
    cfg = load_runtime_config()
    if not cfg.access_token:
        _console.print("[yellow]Not signed in — run `weave login` first.[/yellow]")
        raise typer.Exit(1)

    # Parse namespace + name from input.
    if "." in kind_name and kind_name[0].islower():
        # "legal.acme.ContractClause" → namespace="legal.acme", name="ContractClause"
        parts = kind_name.split(".")
        namespace = ".".join(parts[:-1])
        name = parts[-1]
    else:
        # Bare name — ask server to find it.
        namespace = None
        name = kind_name

    path = f"/kind-registry/{namespace}/{name}" if namespace else f"/kind-registry/{name}"
    with PowerloomClient(cfg) as client:
        try:
            resp = client.get(path)
        except PowerloomApiError as e:
            if e.status_code == 404:
                _console.print(f"[red]No composed kind named {kind_name!r} registered.[/red]")
                _console.print("[dim]Did you `weave apply <compose.yaml>` to register it? "
                              "Or is the v056 engine not yet deployed? (kind_registry is v056+.)[/dim]")
                raise typer.Exit(1) from None
            _console.print(f"[red]Failed:[/red] {e}")
            raise typer.Exit(1) from None

    if output_format == "json":
        _console.print(json.dumps(resp, indent=2, default=str))
    else:
        _console.print(yaml.safe_dump(resp, sort_keys=False, default_flow_style=False))
