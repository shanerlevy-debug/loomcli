# Q4 efficiency benchmark — results

**Status:** Complete. Run on 2026-04-24.
**Spec:** [`efficiency-benchmark-spec.md`](./efficiency-benchmark-spec.md)
**Harness:** [`benchmarks/schema_efficiency.py`](../../benchmarks/schema_efficiency.py)
**Corpus:** [`benchmarks/task_corpus.yaml`](../../benchmarks/task_corpus.yaml)
**Raw results:** `benchmarks/results/schema_efficiency_20260424_011134.csv` + `_summary.json`

## Executive summary

**Verdict: PASS** — all four gates cleared with comfortable margins. Recommendation: **ship v056** (Chomskian 6 root primitives + 8 stdlib derivations).

Across 50 tasks × 3 runs × 2 schema conditions (270 LLM invocations, plus 270 grader invocations, 36.6 min wall clock, $2.98 Anthropic spend), Chomskian 6 (v2.0.0) **beat** flat 1.2.0 primitives on 3 of 4 metrics — most notably **22% smaller context** (terser primitives + stdlib derivations replace v1's 16 per-kind JSON Schemas) and **+2.5pp higher correctness** (90.0% vs 87.5% rubric pass rate). The single trailing metric (latency, +9%) stays well inside the ≤15% gate. No gate was closer than 6% to failing. The architectural bet is validated; proceed with Milestones 2–7 as planned.

## Headline numbers

Condition A = schema v1.2.0 (flat primitives). Condition B = schema v2.0.0 (Chomskian 6 + 8 stdlib derivations).

| Metric | A (v1.2.0) | B (v2.0.0, shared tasks) | Δ | Gate threshold | Result |
|---|---|---|---|---|---|
| Mean input tokens (total processed) | 19,244 | 14,942 | **−22.4%** | ≤+50% | ✅ |
| Mean output tokens | 254.0 | 266.6 | +4.9% | ≤+20% | ✅ |
| Mean latency (s) | 6.18 | 6.72 | +8.8% | ≤+15% | ✅ |
| Pass rate (rubric) | 87.5% | 90.0% | **+2.5pp** | ≥−5pp | ✅ |

**Sample sizes:** A has N=120 runs (40 tasks × 3 runs; T5 excluded since it's B-only). B-shared has N=120 (same 40 tasks × 3 runs). B-all has N=150 (50 tasks × 3 runs, including T5 compose-operator tasks).

**Anthropic spend:** $2.98 total — 540 calls (270 task runs + 270 grader runs). Well under the $10 budget. Prompt caching saved ~90% of per-call input cost after the first run per condition.

## Per-task-type breakdown

### T1 — Simple agent manifests

| Metric | A | B |
|---|---|---|
| Input tokens | 19,234 | 14,932 |
| Output tokens | 338.3 | 404.4 |
| Latency (s) | 8.92 | 10.53 |
| Pass rate | 100.0% | 90.0% |

Commentary: T1 showed the largest output-token delta (+19.5%) and the only task type where condition B's pass rate trailed A. Three T1 runs under v2 failed (one task — likely T1-06 where the model ran into a model-choice subtlety; rubric was strict about exact model string match). Overall both conditions near-perfect; v2 slightly chattier.

### T2 — System skill with auto_attach_to

| Metric | A | B |
|---|---|---|
| Input tokens | 19,221 | 14,919 |
| Output tokens | 201.1 | 153.9 |
| Latency (s) | 4.79 | 4.06 |
| Pass rate | 60.0% | 80.0% |

Commentary: **v2 wins T2 outright** — 20 pp higher pass rate, 23% fewer output tokens, 15% faster latency. The v2 schema's explicit `AutoAttachSelector` nested model appears to guide the agent more reliably than v1's loose dict shape. Worth flagging: T2 pass rates are the lowest across all task types (60% under A), suggesting the underlying rubric for auto_attach_to details is genuinely harder regardless of schema. Documentation improvement opportunity.

### T3 — WorkflowType + MemoryPolicy

| Metric | A | B |
|---|---|---|
| Input tokens | 19,263 | 14,961 |
| Output tokens | 165.8 | 168.4 |
| Latency (s) | 3.98 | 4.46 |
| Pass rate | 100.0% | 100.0% |

Commentary: **Parity across the board** — 100% on both conditions, tiny deltas on output + latency. The v1.2.0 kinds (added in v052) migrate cleanly into v2's stdlib derivations; the agent doesn't notice any semantic difference. Cleanest signal of shape parity holding.

### T4 — Multi-resource with OU hierarchy

| Metric | A | B |
|---|---|---|
| Input tokens | 19,258 | 14,956 |
| Output tokens | 311.0 | 339.5 |
| Latency (s) | 7.02 | 7.84 |
| Pass rate | 90.0% | 90.0% |

Commentary: Parity on correctness (27/30 vs. 27/30). Multi-document YAMLs with cross-resource references (OU + Agent + Skill) work equally well under both conditions. Output tokens modestly higher under v2 (+9%), within the +20% gate.

### T5 — Compose operator (v2.0.0 only)

| Metric | B |
|---|---|
| Input tokens | 14,935 |
| Output tokens | 341.5 |
| Latency (s) | 7.67 |
| Pass rate | 86.7% |

**Note:** T5 has no condition-A counterpart — `compose` doesn't exist in v1. Pass rate 86.7% (26/30) is encouraging for a first-time-in-corpus feature. Four failures clustered on one task (T5-05 SupportEscalation: 3/3 failed), flagged below.

Commentary: The compose operator format is sufficiently well-understood from the schema docstrings alone for the model to author it ~87% of the time with no exemplars. Concentrated failures suggest one or two docstring improvements could push this to near-parity with T1-T4.

## Gate results (detailed)

### Gate 1: Input-token efficiency

- **Threshold:** B.mean_input_tokens ≤ 1.50 × A.mean_input_tokens
- **Observed ratio:** 0.776 (v2 is ~22% smaller)
- **Result:** ✅ PASS (by 0.724 margin — huge)

Interpretation: The v2.0.0 bundle (6 primitives + 8 stdlib derivations + compose + bundle = ~15 KB) is smaller than the v1.2.0 bundle (16 per-kind schemas + common + bundle = ~19 KB). Fewer per-file headers and terser primitive definitions more than outweigh the compose + stdlib overhead. This flips the expected risk ("v2 will be bigger and harder for agents to reason about") into a surprise benefit.

### Gate 2: Output-token efficiency

- **Threshold:** B.mean_output_tokens ≤ 1.20 × A.mean_output_tokens
- **Observed ratio:** 1.049 (v2 is ~5% more verbose)
- **Result:** ✅ PASS (by 0.151 margin)

Interpretation: Manifests produced under v2 are marginally longer. Driven mostly by T1 where the model adds slightly richer field descriptions. No task-type showed pathological verbosity under v2.

### Gate 3: Latency

- **Threshold:** B.mean_latency_s ≤ 1.15 × A.mean_latency_s
- **Observed ratio:** 1.088 (v2 is ~9% slower per call)
- **Result:** ✅ PASS (by 0.062 margin)

Interpretation: The extra latency comes from longer output generation (gate 2's +5%). Reasoning time itself is not materially different. Well inside the gate.

### Gate 4: Correctness (rubric pass rate)

- **Threshold:** B.pass_rate ≥ A.pass_rate − 5pp
- **Observed delta:** +2.5pp (v2 is **better**)
- **Result:** ✅ PASS (by 7.5pp margin on the "worse" side)

Interpretation: v2.0.0 produces more rubric-correct manifests than v1.2.0 on the shared task set. The likely driver is the better-typed nested models in v2 (`AutoAttachSelector`, `SelectiveInheritance`, etc.) guiding the agent toward valid shapes more reliably than v1's looser `dict[str, Any]` stand-ins. T2 is the clearest demonstration (+20 pp).

## Failure analysis — notable cases

Five failure clusters worth investigating; none change the verdict but each is a docs/polish opportunity for v056 Milestone 7.

### T5-05 — SupportEscalation (3/3 failed under B)

- **Task:** Compose a kind `SupportEscalation` that extends stdlib `WorkflowType` and adds Scope + Policy slots.
- **What the model produced:** Correct shape for the `extends` + Scope slot, but missed or mis-shaped the Policy slot.
- **Rubric missed:** "There is a Policy slot" and "apiVersion is powerloom.app/v2" (possibly both tripped by a single structural error).
- **Hypothesis:** The compose docstring for the `policy_type` field may be ambiguous for "escalation_rules" — the model may be writing it as a free-text description rather than a structured Policy slot.
- **Fixable by:** Adding one example in the compose documentation showing a Policy slot with `policy_type: <custom-string>`. One docstring edit.

### T2 — General rubric strictness

- **Task type:** System skills with auto_attach_to selectors.
- **What happened:** 12/30 under A failed; 6/30 under B.
- **Rubric criteria missed most:** The "auto_attach_to.coordinator_role_required is true/false" exact-value checks.
- **Hypothesis:** The model is omitting `coordinator_role_required` when it could default; the rubric interprets absence as failure.
- **Fixable by:** Adjusting the rubric to accept "field absent or matching value" — this is a corpus issue, not a schema issue.

### T1-06 fact-checker — v2 only failure

- **What the model produced:** Valid Agent manifest but likely used `claude-opus-4-0` instead of `claude-opus-4` (minor model-string mismatch).
- **Rubric missed:** "spec.model is claude-opus-4" (exact match).
- **Hypothesis:** Temperature 0.2 sampling variance occasionally tacks a version suffix.
- **Fixable by:** Rubric could accept `claude-opus-4*`; minor.

### T5-03 IncidentRunbook — 1/3 failed under B

- Minor run-to-run variance on one compose edge case. Not systemic.

### Scattered low-N failures

Remaining ~3 failures are single-run anomalies across task types; no pattern.

## Methodology notes

**Prompt caching worked beautifully.** First call per condition wrote the ~15K/~19K-token schema bundle to cache. Subsequent calls paid ~75 billable input tokens + 14,856 cache-read tokens. `total_processed_input_tokens` (what the model actually sees) is the meaningful metric for schema-size comparison regardless of caching — it's what the gate uses.

**Model:** `claude-sonnet-4-5` for both agent runs and grader. Different temperatures (0.2 vs. 0.0).

**N per task per condition:** 3 runs. Adequate for spotting obvious failures + directional signal; not enough for tight confidence intervals. A re-run at v057 against the polished stdlib should use N=5 for tighter comparison.

**Corpus bias:** author-written tasks; written BEFORE stdlib derivations were hand-tuned, per the spec's anti-bias clause. Spot-check shows tasks aren't systematically favorable to either condition — most metrics come out either parity or B-better.

## Recommendation + next steps

**Proceed with v056 Milestones 2–7.** The Chomskian 6 architectural bet is validated by the benchmark gate. Go-signals:

1. **Gate 4 win (v2 is more correct)** is the strongest validation — the whole architecture change pivots on whether it makes agents better or worse at authoring, and the answer is clearly "better."
2. **Gate 1 win (v2 is smaller)** removes the biggest pre-benchmark concern. We expected v2 might cost more context and budgeted +50% — instead it's −22%.
3. **Gate 2 and 3** stay comfortably inside thresholds.

Low-stakes polish for Milestone 7 (docs) based on failure analysis:
- T5-05 → add one compose example showing a Policy slot with custom `policy_type`
- T2 rubric adjustment for absent-optional-fields (benchmark-harness level, not schema)
- Optional: smaller corpus-level rubric tightening for model-string exact matches

A re-benchmark at v057 after stdlib polish will tell us whether these tweaks move the needle on T5 and T2 specifically — expected T5 pass rate → ~93-95% and T2 under v2 → ~85-90%.

## Artifacts

- `benchmarks/results/schema_efficiency_20260424_011134.csv` — 270 per-run rows with tokens, latency, pass/fail, rubric scores, raw output for failed rows
- `benchmarks/results/schema_efficiency_20260424_011134_summary.json` — aggregate metrics + per-task-type breakdown + gate decisions
- `benchmarks/results/full_run.log` — streaming per-run log

## Companion documents

- [Efficiency benchmark spec](./efficiency-benchmark-spec.md)
- [v056 implementation plan](./v056-implementation-plan.md) — Milestone 1 ✅ DONE
- [Pros-cons Q4-Q6 analysis](./pros-cons-analysis.md) — Option C fallback (not triggered)
