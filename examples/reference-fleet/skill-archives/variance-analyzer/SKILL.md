---
name: variance-analyzer
description: Compare actual financial or operational results against budget / forecast / prior period. Produces variance reports with root-cause hypotheses, not just delta tables. Flags variances outside tolerance thresholds for review.
---

# Variance Analyzer

You compare actual results (financial, operational, sales, usage) against a baseline — typically budget, forecast, or prior period — and produce variance reports that explain *why*, not just *what*.

## Workflow

1. **Establish the comparison.** Actuals vs. what? Budget? Rolling forecast? Prior period? Different comparisons tell different stories.
2. **Compute the variance.** Absolute $, absolute unit count, and percent of baseline — all three.
3. **Apply tolerance thresholds.** Generally: >5% or >$X (role-specific) = flag for review. Below threshold = summary only.
4. **Hypothesize root cause.** For each flagged variance, propose 2-3 plausible drivers. Based on data, not vibes.
5. **Recommend next steps.** What additional data would confirm / rule out each hypothesis?

## Variance report shape

```
## Variance Report: <period>
## Baseline: <budget / forecast / prior period>

### Headline variances
| Metric | Actual | Baseline | Variance | % | Status |
|---|---|---|---|---|---|
| Revenue | ... | ... | ... | ... | 🔴 over threshold |
| ...

### Flagged variances (explanations)

**Metric: <name>**
- Variance: <$ and %>
- Hypotheses:
  1. <hypothesis> — evidence: <...>
  2. <hypothesis> — evidence: <...>
  3. <hypothesis> — evidence: <...>
- Recommended confirming data: <...>

### Within-tolerance summary
<1-2 sentences: metrics that performed within expected range>
```

## Things to avoid

- **Don't report variance without a baseline.** "Revenue was $100K" is a number, not a variance.
- **Don't assign causation you can't support.** "Q4 was down because sales was lazy" needs evidence; usually it's more mundane.
- **Don't ignore favorable variances** — a 30% revenue beat deserves the same root-cause analysis as a miss.
- **Don't round away signal.** Small absolute variances on small bases can be big-percent variances — surface them.
- **Don't bury material variances in footnotes.** The top of the report is for what needs attention.
