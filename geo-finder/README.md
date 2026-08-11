# codimango/geo-finder

## Latest online validation status

**Commit `2898b43b` (v1.13) · run 2026-08-11 · jobs 4525004 / 4525005 / 4525006 / 4525007**

Platform verdict: `validation = passing`, `tbdReviewStatus = pass`, status `draft`.
Contamination LOW · novelty risk MEDIUM · embedding dedup 0.5235 · quality dimensions
depth 3 / realism 3 / originality 2.

All four jobs complete. The "Metacode or Opus pass/fail balance" row has still not
appeared in `validationDetails` — aggregation lag, not a failure.

| Stage | Job | Result | Reward split |
| --- | --- | --- | --- |
| oracle | 4525005 | **3/3** | 3 × 1.00 |
| codex (`gpt-5.5`) | 4525006 | **8/10** | 8 × 1.00, 2 × 0.00 |
| metacode (avocado) | 4525004 | **1/10** | 1 × 1.00, 3 × 0.50, 6 × 0.00 |
| agent (`claude-opus-4-8`) | 4525007 | **1/10** | 1 × 1.00, 9 × 0.00 |

### The task has swung to too-hard, and step 1 is what kills trials

A reward of `0.00` means step 1 failed, so step 2 never runs (`min_reward = 1`).
Opus scored 0.00 on 9 of 10 trials; avocado on 6 of 10.

All 20 failing trials were analysed. **Every one is a single-test miss** — 29/30 on step 1
or 37/38 on step 2 — and 17 of the 20 die in step 1:

| Test | Step | Trials failing | opus | avocado | codex |
| --- | --- | --- | --- | --- | --- |
| `test_world_bounds` | 1 | **11** | 4 | 6 | 1 |
| `test_lookup_antimeridian_cli` | 1 | **6** | 5 | 0 | 1 |
| `test_concurrent_post_stress` | 2 | 3 | 0 | 3 | 0 |

Both step-1 discriminators are the same thing: **world-spanning vs antimeridian
classification**. `test_concurrent_post_stress` is the only step-2 test that ever fires,
and only for avocado.

### Reading of the result — the difficulty is an unstated rule, not reasoning depth

`test_world_bounds` fails at *add* time, before any lookup:

```
add world --polygon "-90,-180;-90,180;90,180;90,-180"
→ exit 2  "error: polygon has zero area (degenerate or colinear)"
```

Two separately-required rules collide. Longitude wrapping normalises `-180 ≡ 180`, which
collapses the world rectangle to a degenerate shape; the also-required "non-zero area"
validation then rejects it. The instruction never says which rule wins, nor that the world
rectangle must be accepted as a valid polygon.

`test_lookup_antimeridian_cli` is the `>180` vs `≥360` misclassification on the lookup
path — a point that should be inside the crossing rectangle comes back `[]`.

The step-1 instruction states only (L74):

> "Must also handle polygons that cross the antimeridian and world-spanning polygons
> correctly, with same answers as HTTP service will require in step 2."

No threshold, no classification order, no statement that the world rectangle is
non-degenerate. The disambiguation that used to live in the step-2 spec was removed by
`0619c25` (de-over-specification) and was never present in step 1. `f57dd07` then added
these two tests to step 1, where a failure is fatal to the whole trial.

The model ordering is inverted as a result — **codex 8/10 while opus and avocado are both
1/10**. That is a coin flip on an unstated rule, not a capability difference: codex happens
to pick the intended reading, the other two do not, and the split is near-total in each
direction.

### Action before landing

State explicitly, in both steps' `instruction.md`:

1. A polygon whose longitude span is ≥ 360 covers every longitude and is **not**
   antimeridian-crossing; classify world-spanning *before* applying the crossing rule.
2. The world rectangle is a valid, non-degenerate polygon — the zero-area/colinearity
   check must run on unwrapped coordinates, before any longitude normalisation.

Without (2), `test_world_bounds` is unwinnable for any implementation that follows the
wrapping rule — it accounts for 11 of the 20 failures on its own.

Expect pass rates to rise sharply once both are stated: removing the two step-1 tests from
the tally would put opus at 10/10, avocado at 4/10 and codex at 10/10. The only fair
discriminator still firing is `test_concurrent_post_stress` (3 × avocado), so the task will
need real difficulty rebuilt on top of the selective-invalidation / concurrency family
rather than on classification ambiguity.

## Authoring policy

Before authoring, read the [Model usage policy v2](https://codimango.internalmeta.com/tracks/t-bench#model-usage-policy) and the [Workplace v2 post](https://fb.workplace.com/groups/aaitaskquality/posts/1074223732270508). The policy page is the source of truth.

- The task seed must be human; any model may be a thought partner.
- Third-party models (including Codex, Claude, and Gemini) must not author instructions or multi-step prompts, model-visible content, tests, grading rubrics, test/grading configuration, test fixtures, or imported/traced grading inputs.
- Reviewed first-party drafting is allowed; a human must review, own, and remain responsible for the result.
- Never forward third-party-generated findings, summaries, candidate assertions, code, briefs, recommendations, or other context into a first-party session authoring protected content.
- Other hidden, non-test, non-grading files may use any model with human review.
