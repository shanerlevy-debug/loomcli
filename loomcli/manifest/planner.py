"""Plan computation — manifest Resource + current API state → Action.

Each action is one of:
    CREATE     resource missing on the server, will be created
    UPDATE     spec differs; PATCH fields listed in `changed_fields`
    NOOP       server already matches the manifest
    UNKNOWN    couldn't determine current state (e.g., upstream OU
               doesn't exist yet — deferred until apply)

Actions are emitted in the same order as the manifest. Apply uses
this order, with an additional dependency-sort pass for create-only
runs (OU before agents, etc.) handled in the applier.

Destroy actions are computed separately — see `plan_destroy_for_resources`
at the bottom — because destroy needs to reverse dependency order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from loomcli.manifest.addressing import (
    AddressResolutionError,
    AddressResolver,
)
from loomcli.manifest.handlers import get_handler
from loomcli.manifest.schema import Resource


ActionVerb = Literal["create", "update", "noop", "destroy", "unknown"]


@dataclass
class FieldDiff:
    field: str
    before: Any
    after: Any


@dataclass
class PlanAction:
    resource: Resource
    verb: ActionVerb
    """What apply will do. `unknown` means apply will re-plan after
    upstream creates land."""

    # Populated for update/destroy. Empty for create/noop.
    current_snapshot: dict[str, Any] | None = None
    """The server row we diffed against. None for CREATE and UNKNOWN."""

    changed_fields: list[FieldDiff] = field(default_factory=list)
    """Per-field diff for UPDATE. Empty for CREATE (everything new)
    and NOOP (nothing changed)."""

    reason: str | None = None
    """Human-readable explanation — shown in plan output. Especially
    useful for UNKNOWN ('waiting on parent OU /dev-org/engineering')."""


@dataclass
class Plan:
    actions: list[PlanAction]

    def summary_counts(self) -> dict[ActionVerb, int]:
        counts: dict[ActionVerb, int] = {}
        for a in self.actions:
            counts[a.verb] = counts.get(a.verb, 0) + 1
        return counts


def plan_resources(resources: list[Resource], resolver: AddressResolver) -> Plan:
    """Compute the plan for applying `resources`. Does NOT mutate
    server state — pure GETs."""
    actions: list[PlanAction] = []
    for r in resources:
        handler = get_handler(r.kind)
        if handler is None:
            actions.append(
                PlanAction(
                    resource=r,
                    verb="unknown",
                    reason=f"no handler registered for kind={r.kind!r}",
                )
            )
            continue
        try:
            current = handler.read(r, resolver)
        except AddressResolutionError as e:
            # Upstream dependency (parent OU, containing agent, etc.)
            # not present yet. Plan will re-check on next invocation.
            actions.append(
                PlanAction(
                    resource=r,
                    verb="unknown",
                    reason=str(e),
                )
            )
            continue

        if current is None:
            actions.append(PlanAction(resource=r, verb="create"))
            continue

        diff = _diff_spec(r.spec, current, handler=handler)
        if not diff:
            actions.append(
                PlanAction(
                    resource=r,
                    verb="noop",
                    current_snapshot=current,
                )
            )
        else:
            actions.append(
                PlanAction(
                    resource=r,
                    verb="update",
                    current_snapshot=current,
                    changed_fields=diff,
                )
            )
    return Plan(actions=actions)


def _diff_spec(
    spec: BaseModel,
    current: dict[str, Any],
    *,
    handler: Any,
) -> list[FieldDiff]:
    """Compare the manifest's spec to the server's current state.

    The handler decides which spec fields map to which server fields
    (they're usually identically named, but not always — e.g., an
    Agent's `owner_principal_ref` resolves to `owner_principal_id`).
    Handlers provide a `map_spec_to_server_fields` that returns the
    server-shaped dict for comparison.
    """
    desired = handler.map_spec_to_server_fields(spec, current=current)
    diffs: list[FieldDiff] = []
    for field_name, desired_val in desired.items():
        current_val = current.get(field_name)
        if _values_equal(desired_val, current_val):
            continue
        diffs.append(
            FieldDiff(field=field_name, before=current_val, after=desired_val)
        )
    return diffs


def _values_equal(a: Any, b: Any) -> bool:
    """Defensive equality check — JSON-ish comparison that handles
    None == None, list order-insensitivity, and nested dicts.

    We deliberately treat None == "" as NOT equal (empty string is
    a real server value distinct from "never set"). But we do treat
    missing keys and None identically on the `current` side because
    server responses omit null fields sometimes.
    """
    if a is None and b is None:
        return True
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        # Allow int/float crossover since JSON round-trips can flip them.
        return False
    if isinstance(a, list):
        # Order-insensitive for lists — manifests often list attachments
        # in a different order than the server returns.
        return sorted(a, key=lambda x: str(x)) == sorted(b, key=lambda x: str(x))
    if isinstance(a, dict):
        return a == b
    return a == b
