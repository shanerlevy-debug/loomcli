"""v056 Milestone 1 — Q4 efficiency benchmark.

Does the Chomskian 6-primitive root (v2.0.0) help or hurt an LLM agent
authoring Powerloom manifests vs. the flat 1.2.0 primitives?

Methodology (full spec in docs/memory-evolution/efficiency-benchmark-spec.md):
  - 50 tasks across 5 task types
  - Each task run 3 times per condition (A = v1.2.0, B = v2.0.0)
  - Task type T5 is v2.0.0-only (compose operator) — runs only 3x
  - Metrics: input tokens, output tokens, wall-clock latency, correctness
  - Correctness: LLM grader applies the task's 5-criterion rubric

Output:
  - benchmarks/results/schema_efficiency_<date>_<condition>.csv — raw per-run rows
  - benchmarks/results/schema_efficiency_<date>_summary.json — aggregated
  - Stdout: human-readable summary + pass/fail verdict against the 4 gates

Prerequisites:
  - ANTHROPIC_API_KEY in env or .env
  - Python 3.12+
  - pip install anthropic pyyaml

Run:
  python benchmarks/schema_efficiency.py           # full run
  python benchmarks/schema_efficiency.py --smoke   # 3 tasks × 1 run, debug
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Load .env if present (for ANTHROPIC_API_KEY). Simple parser — no
# python-dotenv dependency.
def _load_dotenv() -> None:
    candidates = [
        Path(".env"),
        Path("../powerloom/.env"),
        Path("../../powerloom/.env"),
        Path(__file__).resolve().parent.parent.parent / "powerloom" / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # Overwrite if the current value is empty. Some shells
                # export ANTHROPIC_API_KEY="" which passes an os.environ
                # membership check but is useless.
                if k and (not os.environ.get(k)):
                    os.environ[k] = v
            return


_load_dotenv()

try:
    from anthropic import Anthropic
except ImportError:
    print("error: pip install anthropic", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_V1_ROOT = REPO_ROOT / "schema" / "v1"
SCHEMA_V2_ROOT = REPO_ROOT / "schema" / "v2"
RESULTS_DIR = REPO_ROOT / "benchmarks" / "results"
CORPUS_PATH = REPO_ROOT / "benchmarks" / "task_corpus.yaml"

# Model + sampling.
MODEL = "claude-sonnet-4-5"  # latest sonnet generation as of 2026-04-24
MAX_TOKENS = 4096
TEMPERATURE = 0.2  # low to reduce stochastic variance across runs

# Pass/fail thresholds from the spec (relative deltas of B vs. A).
GATE_INPUT_TOKENS = 1.50    # B <= 150% of A
GATE_OUTPUT_TOKENS = 1.20   # B <= 120% of A
GATE_LATENCY = 1.15         # B <= 115% of A
GATE_CORRECTNESS = -0.05    # B's pass rate >= A's - 5pp


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Task:
    id: str
    task_type: str  # T1..T5
    description: str
    rubric: list[str]
    reference_outputs: list[str]
    v2_only: bool = False


@dataclass
class RunResult:
    task_id: str
    task_type: str
    condition: str                 # "A" or "B"
    run_index: int                 # 0..2
    input_tokens: int              # uncached billable input
    output_tokens: int
    latency_seconds: float
    cached_input_tokens: int       # cache hits (billed at ~10%)
    cache_creation_tokens: int = 0 # first-call cache writes (billed at ~25% premium)
    raw_output: str = ""
    rubric_scores: list[bool] = field(default_factory=list)
    passed: bool = False
    grader_cost_usd: float = 0.0
    error: str | None = None

    @property
    def total_processed_input_tokens(self) -> int:
        """What the model actually saw — sum of uncached + cache-read
        + cache-creation. This is the meaningful metric for 'did the
        bigger schema cost the agent more context?' regardless of
        cache behavior."""
        return self.input_tokens + self.cached_input_tokens + self.cache_creation_tokens


# ---------------------------------------------------------------------------
# Schema bundle loaders
# ---------------------------------------------------------------------------


def _load_schema_bundle(root: Path) -> str:
    """Concatenate the schema bundle into a single string for the
    agent's context. Excludes `__pycache__` etc."""
    files: list[Path] = []
    for pattern in ("*.schema.json", "*.md", "VERSION"):
        files.extend(sorted(root.rglob(pattern)))
    sections: list[str] = []
    for p in files:
        rel = p.relative_to(root)
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        sections.append(f"\n--- {rel} ---\n{content}")
    return "".join(sections)


def _system_prompt(condition: str, schema_bundle: str) -> str:
    """Build the system prompt given the condition + schema bundle."""
    version_tag = "v1.2.0 (powerloom.app/v1)" if condition == "A" else "v2.0.0 (powerloom.app/v2)"
    return (
        f"You are a Powerloom manifest author. You produce YAML manifests "
        f"that conform to the Powerloom JSON Schema bundle provided below "
        f"(schema {version_tag}).\n\n"
        f"Rules:\n"
        f"  - Output EXACTLY one YAML document per task. No markdown fencing, "
        f"no prose before or after. Just the YAML.\n"
        f"  - Start with 'apiVersion:' line. Use powerloom.app/v1 for 1.2.0 "
        f"tasks, powerloom.app/v2 for 2.0.0 tasks.\n"
        f"  - All required fields present per the schema.\n"
        f"  - No fields the schema disallows.\n"
        f"  - If asked to create a 'system' skill with auto-attach, use the "
        f"spec.system + spec.auto_attach_to shape.\n"
        f"  - If asked for a coordinator agent, set spec.coordinator_role: true.\n\n"
        f"=== SCHEMA BUNDLE ===\n"
        f"{schema_bundle}\n"
        f"=== END SCHEMA BUNDLE ===\n"
    )


# ---------------------------------------------------------------------------
# Task corpus loader
# ---------------------------------------------------------------------------


def load_corpus() -> list[Task]:
    import yaml

    raw = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    tasks: list[Task] = []
    for t in raw["tasks"]:
        tasks.append(
            Task(
                id=t["id"],
                task_type=t["task_type"],
                description=t["description"],
                rubric=t["rubric"],
                reference_outputs=t.get("reference_outputs", []),
                v2_only=t.get("v2_only", False),
            )
        )
    return tasks


# ---------------------------------------------------------------------------
# Run one task under one condition
# ---------------------------------------------------------------------------


def run_task(
    client: Anthropic,
    task: Task,
    condition: str,
    run_index: int,
    schema_bundle: str,
) -> RunResult:
    system_prompt = _system_prompt(condition, schema_bundle)
    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=[
                # Prompt caching on the schema bundle — the system prompt
                # is large + identical across runs, so this is a huge win
                # on repeat-task cost.
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": task.description}],
        )
    except Exception as e:
        return RunResult(
            task_id=task.id,
            task_type=task.task_type,
            condition=condition,
            run_index=run_index,
            input_tokens=0,
            output_tokens=0,
            latency_seconds=time.perf_counter() - started,
            cached_input_tokens=0,
            raw_output="",
            error=f"{type(e).__name__}: {e}",
        )
    elapsed = time.perf_counter() - started

    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return RunResult(
        task_id=task.id,
        task_type=task.task_type,
        condition=condition,
        run_index=run_index,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        latency_seconds=elapsed,
        cached_input_tokens=cache_read,
        cache_creation_tokens=cache_create,
        raw_output=_strip_code_fences(_extract_text(response)),
    )


def _extract_text(response) -> str:
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _strip_code_fences(text: str) -> str:
    """Strip ```yaml / ``` fences the model occasionally wraps around
    YAML output despite the system prompt. Operates semantically — if
    the output is a single fenced block, we unwrap; otherwise we leave
    it alone (multi-block outputs probably genuinely have multiple
    fences for multiple docs, which is its own problem)."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    # Find the first newline after the opening fence; find the last ```
    first_nl = t.find("\n")
    if first_nl == -1:
        return t
    body = t[first_nl + 1:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()
    return body


# ---------------------------------------------------------------------------
# Grader — LLM-applies the 5-criterion rubric per output
# ---------------------------------------------------------------------------

GRADER_MODEL = "claude-sonnet-4-5"


def grade(
    client: Anthropic,
    task: Task,
    output: str,
) -> tuple[list[bool], float]:
    if not output.strip():
        return [False] * len(task.rubric), 0.0
    criteria_list = "\n".join(
        f"  {i + 1}. {criterion}" for i, criterion in enumerate(task.rubric)
    )
    prompt = (
        "You are grading a Powerloom manifest produced by an LLM agent. "
        "Apply the rubric below. For each criterion, answer ONLY 'yes' or "
        "'no' (one word per line, in order).\n\n"
        f"Task description:\n{task.description}\n\n"
        f"Rubric:\n{criteria_list}\n\n"
        f"Output to grade:\n```yaml\n{output}\n```\n\n"
        "Your answers (one yes/no per line, in order):"
    )
    response = client.messages.create(
        model=GRADER_MODEL,
        max_tokens=256,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_text(response)
    # Parse
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    scores: list[bool] = []
    for i, _ in enumerate(task.rubric):
        if i < len(lines):
            first_word = lines[i].split()[0] if lines[i].split() else ""
            scores.append(first_word.startswith("y"))
        else:
            scores.append(False)
    # Grader cost (sonnet-4-5 ~ $3/1M in, $15/1M out — not precisely the
    # current prices but close enough for tracking).
    usage = response.usage
    grader_cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    return scores, grader_cost


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_benchmark(
    smoke: bool = False,
    n_runs: int = 3,
) -> tuple[list[RunResult], float]:
    client = Anthropic()
    corpus = load_corpus()
    if smoke:
        corpus = corpus[:3]
        n_runs = 1
    bundle_a = _load_schema_bundle(SCHEMA_V1_ROOT)
    bundle_b = _load_schema_bundle(SCHEMA_V2_ROOT)

    print(f"Corpus: {len(corpus)} tasks (smoke={smoke}, n_runs={n_runs})")
    print(f"Schema v1 bundle: {len(bundle_a):,} chars")
    print(f"Schema v2 bundle: {len(bundle_b):,} chars")
    print(f"Model: {MODEL}")
    print()

    all_results: list[RunResult] = []
    total_cost_usd = 0.0

    for task_idx, task in enumerate(corpus):
        for condition, bundle in (("A", bundle_a), ("B", bundle_b)):
            # T5 tasks only run under condition B
            if task.v2_only and condition == "A":
                continue
            for run_idx in range(n_runs):
                print(
                    f"[{task_idx + 1}/{len(corpus)} {task.task_type}/{task.id}] "
                    f"condition={condition} run={run_idx} ...",
                    end="",
                    flush=True,
                )
                result = run_task(
                    client, task, condition, run_idx, bundle
                )
                if result.error is None:
                    # Grade + mark pass
                    scores, grader_cost = grade(client, task, result.raw_output)
                    result.rubric_scores = scores
                    result.passed = all(scores)
                    result.grader_cost_usd = grader_cost
                    total_cost_usd += grader_cost

                    # Task cost (Anthropic sonnet-4-5 public pricing,
                    # approximate for budget tracking — may be off by a
                    # small constant factor).
                    #   input:         $3/M
                    #   cache-write:   $3.75/M (25% premium)
                    #   cache-read:    $0.30/M (90% discount)
                    #   output:        $15/M
                    task_cost = (
                        result.input_tokens * 3
                        + result.cache_creation_tokens * 3.75
                        + result.cached_input_tokens * 0.30
                        + result.output_tokens * 15
                    ) / 1_000_000
                    total_cost_usd += task_cost
                    mark = "OK " if result.passed else "X  "
                    total_in = result.total_processed_input_tokens
                    print(
                        f" {mark} in={total_in:>6} (uc={result.input_tokens:>4}, "
                        f"cw={result.cache_creation_tokens:>5}, cr={result.cached_input_tokens:>5}) "
                        f"out={result.output_tokens:>4} lat={result.latency_seconds:>5.2f}s"
                    )
                else:
                    print(f" ERR: {result.error[:80]}")
                all_results.append(result)

    return all_results, total_cost_usd


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze(results: list[RunResult]) -> dict[str, Any]:
    by_cond: dict[str, list[RunResult]] = {"A": [], "B": []}
    for r in results:
        if r.error is None:
            by_cond[r.condition].append(r)

    def agg(rs: list[RunResult]) -> dict[str, float]:
        if not rs:
            return {
                "n": 0,
                "mean_input_tokens": 0,
                "mean_billable_input_tokens": 0,
                "mean_output_tokens": 0,
                "mean_latency_s": 0,
                "pass_rate": 0,
            }
        return {
            "n": len(rs),
            # Total input the model actually processed (uncached +
            # cache-read + cache-creation). This is the meaningful
            # "how much schema context" metric regardless of caching.
            "mean_input_tokens": statistics.mean(
                r.total_processed_input_tokens for r in rs
            ),
            # What you actually get billed for (uncached) — smaller
            # signal but reflects real $ impact.
            "mean_billable_input_tokens": statistics.mean(
                r.input_tokens for r in rs
            ),
            "mean_output_tokens": statistics.mean(r.output_tokens for r in rs),
            "mean_latency_s": statistics.mean(r.latency_seconds for r in rs),
            "pass_rate": sum(1 for r in rs if r.passed) / len(rs),
        }

    a_agg = agg(by_cond["A"])
    b_agg = agg(by_cond["B"])

    # Exclude v2-only tasks from the A vs B deltas — they're B-only by design.
    a_tasks = {r.task_id for r in by_cond["A"]}
    b_shared = [r for r in by_cond["B"] if r.task_id in a_tasks]
    b_shared_agg = agg(b_shared)

    gates: dict[str, dict[str, Any]] = {}
    if a_agg["n"] > 0 and b_shared_agg["n"] > 0:
        ratio_in = b_shared_agg["mean_input_tokens"] / max(
            a_agg["mean_input_tokens"], 1
        )
        ratio_out = b_shared_agg["mean_output_tokens"] / max(
            a_agg["mean_output_tokens"], 1
        )
        ratio_lat = b_shared_agg["mean_latency_s"] / max(
            a_agg["mean_latency_s"], 0.001
        )
        corr_delta = b_shared_agg["pass_rate"] - a_agg["pass_rate"]
        gates = {
            "input_tokens_ratio": {
                "value": ratio_in,
                "threshold": GATE_INPUT_TOKENS,
                "passed": ratio_in <= GATE_INPUT_TOKENS,
            },
            "output_tokens_ratio": {
                "value": ratio_out,
                "threshold": GATE_OUTPUT_TOKENS,
                "passed": ratio_out <= GATE_OUTPUT_TOKENS,
            },
            "latency_ratio": {
                "value": ratio_lat,
                "threshold": GATE_LATENCY,
                "passed": ratio_lat <= GATE_LATENCY,
            },
            "correctness_delta": {
                "value": corr_delta,
                "threshold": GATE_CORRECTNESS,
                "passed": corr_delta >= GATE_CORRECTNESS,
            },
        }
    verdict = "pass" if gates and all(g["passed"] for g in gates.values()) else "fail"

    # Per-task-type breakdown
    task_types: set[str] = {r.task_type for r in results}
    by_type: dict[str, dict[str, dict[str, float]]] = {}
    for t in sorted(task_types):
        by_type[t] = {
            "A": agg([r for r in by_cond["A"] if r.task_type == t]),
            "B": agg([r for r in by_cond["B"] if r.task_type == t]),
        }

    return {
        "condition_A_v1_2_0": a_agg,
        "condition_B_v2_0_0_shared": b_shared_agg,
        "condition_B_v2_0_0_all": b_agg,
        "gates": gates,
        "verdict": verdict,
        "by_task_type": by_type,
    }


# ---------------------------------------------------------------------------
# Write outputs
# ---------------------------------------------------------------------------


def write_csv(results: list[RunResult], stamp: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"schema_efficiency_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "task_id", "task_type", "condition", "run_index",
                "input_tokens", "cache_creation_tokens", "cached_input_tokens",
                "total_processed_input_tokens", "output_tokens",
                "latency_seconds", "passed", "rubric_scores",
                "grader_cost_usd", "error", "raw_output",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id": r.task_id,
                "task_type": r.task_type,
                "condition": r.condition,
                "run_index": r.run_index,
                "input_tokens": r.input_tokens,
                "cache_creation_tokens": r.cache_creation_tokens,
                "cached_input_tokens": r.cached_input_tokens,
                "total_processed_input_tokens": r.total_processed_input_tokens,
                "output_tokens": r.output_tokens,
                "latency_seconds": round(r.latency_seconds, 3),
                "passed": r.passed,
                "rubric_scores": ",".join("1" if s else "0" for s in r.rubric_scores),
                "grader_cost_usd": round(r.grader_cost_usd, 6),
                "error": r.error or "",
                # Full raw output for any row that failed rubric — lets us
                # human-spot-check later. Truncate to 4k to keep CSV sane.
                "raw_output": (r.raw_output or "")[:4000] if not r.passed else "",
            })
    return path


def write_summary(summary: dict[str, Any], total_cost: float, stamp: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"schema_efficiency_{stamp}_summary.json"
    payload = {
        **summary,
        "total_anthropic_cost_usd": round(total_cost, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def print_human_summary(summary: dict[str, Any], total_cost: float) -> None:
    print()
    print("=" * 70)
    print("EFFICIENCY BENCHMARK RESULTS")
    print("=" * 70)
    print()
    a = summary["condition_A_v1_2_0"]
    bshared = summary["condition_B_v2_0_0_shared"]
    ball = summary["condition_B_v2_0_0_all"]
    print(f"Condition A (v1.2.0): n={a['n']:>3}   mean_in={a['mean_input_tokens']:>8,.0f}   "
          f"mean_out={a['mean_output_tokens']:>5,.0f}   lat={a['mean_latency_s']:>5.2f}s   "
          f"pass={a['pass_rate']*100:>5.1f}%")
    print(f"Condition B (v2.0.0): n={bshared['n']:>3}   mean_in={bshared['mean_input_tokens']:>8,.0f}   "
          f"mean_out={bshared['mean_output_tokens']:>5,.0f}   lat={bshared['mean_latency_s']:>5.2f}s   "
          f"pass={bshared['pass_rate']*100:>5.1f}%  (shared tasks only)")
    print(f"                     n={ball['n']:>3}   (all tasks incl. v2-only)")
    print()
    print("Gates (B vs. A, relative):")
    for name, g in summary["gates"].items():
        mark = "PASS" if g["passed"] else "FAIL"
        print(f"  [{mark}] {name}: {g['value']:.4f}   (threshold {g['threshold']})")
    print()
    print(f"VERDICT: {summary['verdict'].upper()}")
    print()
    print(f"Total Anthropic spend: ${total_cost:.4f}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--smoke", action="store_true",
                   help="Run 3 tasks × 1 run only. ~$0.50. Use to verify the "
                        "harness works before a full run.")
    p.add_argument("--n-runs", type=int, default=3,
                   help="Runs per task per condition (default 3).")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY not set. Put it in .env or export it.",
              file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    t0 = time.time()
    results, cost = run_benchmark(smoke=args.smoke, n_runs=args.n_runs)
    elapsed_min = (time.time() - t0) / 60
    print(f"\nBenchmark complete in {elapsed_min:.1f} minutes.")

    summary = analyze(results)
    csv_path = write_csv(results, stamp)
    json_path = write_summary(summary, cost, stamp)
    print_human_summary(summary, cost)
    print(f"Per-run CSV:    {csv_path}")
    print(f"Summary JSON:   {json_path}")
    return 0 if summary["verdict"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
