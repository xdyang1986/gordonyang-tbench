# codimango/geo-finder

## Latest online validation status

**Commit `2244c7af` · run 2026-08-07 14:35–16:12 UTC · jobs 4327628 / 4327629 / 4327630 / 4327631**

Platform verdict: **`validation = passing`** (status `needs_reviewers_assigned`, review `Draft`).

| Gate | Result |
| --- | --- |
| Structural checks | passed — 10/10 |
| Oracle validation | passed — 3/3 @ reward 1.00 |
| Metacode/Opus pass-fail balance | passed — avocado not trivial and ≥1 agent solved |
| Agentic trial-output review | passed — not applicable |
| Contamination check | passed — risk MEDIUM |
| Provenance check | passed — no third-party authorship detected |

### Per-model results (full-task pass = both steps at reward 1.0)

| Model | Job | Full pass | Mean reward | Notes |
| --- | --- | --- | --- | --- |
| oracle | 4327630 | 3/3 | 1.00 | no flake |
| avocado (`meta/avocado-5.14-code`) | 4327629 | 1/10 | 0.50 | 8 × step-2 fail, 1 × 0.00 (step-1 `go build` failure at 160 s) |
| opus (`claude-opus-4-8`) | 4327631 | 0/10 | 0.50 | all 10 reach step 2 and fail there |
| gpt (`gpt-5.5`, codex) | 4327628 | 0/10 | 0.50 | all 10 reach step 2 and fail there |

Step 1 passes in 29/30 agent trials; **every** non-oracle failure is in step 2, so the
DQE cascade split is working as intended.

### Failing tests in the latest run (30 agent trials)

| Test | Trials failing | opus | gpt | avocado |
| --- | --- | --- | --- | --- |
| `test_index_large_polygon` | 19 | 4 | 9 | 6 |
| `test_antimeridian_crossing` | 12 | 10 | 0 | 2 |
| `test_cache_rounding` | 10 | 1 | 9 | 0 |
| `test_concurrency_correctness` | 3 | 0 | 3 | 0 |
| `test_no_null_slices_broad` | 3 | 0 | 3 | 0 |
| `test_concurrency_with_crud` | 1 | 0 | 0 | 1 |

### Reading of the result

The gate passes, but the pass profile is inverted and driven by three tests, two of
which are not fairly derivable from `instruction.md`:

1. **`test_index_large_polygon` (19/30, all three models) — spec⇄test contradiction.**
   The test asserts `stats["index_cells"] > 100` for the world polygon
   `-90,-180;-90,180;90,180;90,-180` plus 100 small zones; every failure reports exactly
   `assert 100 > 100`, i.e. the world polygon contributed zero cells. That is the direct
   consequence of following the instruction's own rule ("when `maxLng-minLng > 180`, the
   polygon crosses the antimeridian … add to cells on both sides of ±180, not the huge
   interior gap") — the world polygon has span 360, so a literal reading classifies it as
   antimeridian-crossing and indexes it into ~0 cells. The reference solution carves out
   `span >= 360` as "covers all longitudes", but that carve-out is oracle-only knowledge.
   `test_antimeridian_crossing` additionally asserts `index_cells < 100` for a tiny
   crossing rect, so the two tests pull in opposite directions.

2. **`test_antimeridian_crossing` (10/10 for opus, 0/10 for gpt) — model-specific split.**
   Failures are on the CLI path (`point 0.5,179.5 should be inside crossing rect`,
   returns `[]`): the polygon-crossing rule must also change point-in-polygon, not just
   the bbox/grid, and the instruction states it only under the bbox and index headings.

3. **`test_cache_rounding` (9/10 for gpt) — under-specified to the point of unfair.**
   The test requires cache keys rounded to exactly 6 decimals (`0.5000001` and
   `0.5000002` must share an entry, `cache_size == 1`), but the instruction only says
   nearby points "should **ideally** share cache entries" — no rounding precision is given.

Net: difficulty here comes largely from unstated requirements rather than from reasoning
depth. Before landing, either state the `span >= 360` world-polygon rule and the 6-decimal
cache key in `instruction.md`, or drop those two assertions and keep the antimeridian
semantics (spelled out for the lookup path, not only the index) as the discriminator.

## Authoring policy

Before authoring, read the [Model usage policy v2](https://codimango.internalmeta.com/tracks/t-bench#model-usage-policy) and the [Workplace v2 post](https://fb.workplace.com/groups/aaitaskquality/posts/1074223732270508). The policy page is the source of truth.

- The task seed must be human; any model may be a thought partner.
- Third-party models (including Codex, Claude, and Gemini) must not author instructions or multi-step prompts, model-visible content, tests, grading rubrics, test/grading configuration, test fixtures, or imported/traced grading inputs.
- Reviewed first-party drafting is allowed; a human must review, own, and remain responsible for the result.
- Never forward third-party-generated findings, summaries, candidate assertions, code, briefs, recommendations, or other context into a first-party session authoring protected content.
- Other hidden, non-test, non-grading files may use any model with human review.
