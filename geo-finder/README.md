# codimango/geo-finder

## Latest online validation status

**Commit `b36ea0b9` (v1.25) · run 2026-08-12 · jobs 4580997 / 4580998 / 4580999 / 4581000**

Platform verdict: `validation = passing`, `tbdReviewStatus = pass`, status `draft`.
Contamination MEDIUM · novelty risk MEDIUM · embedding dedup 0.5244 · quality dimensions
**depth 3 / realism 3 / originality 3** — all three at maximum.

All four jobs complete. The "Metacode or Opus pass/fail balance" row has still not
appeared in `validationDetails` — aggregation lag, not a failure.

| Stage | Job | Result | Reward split |
| --- | --- | --- | --- |
| oracle | 4580998 | **3/3** | 3 × 1.00 |
| codex (`gpt-5.5`) | 4580997 | **10/10** | 10 × 1.00 |
| metacode (avocado) | 4581000 | **4/10** | 4 × 1.00, 5 × 0.50, 1 × 0.00 |
| agent (`claude-opus-4-8`) | 4580999 | **5/10** | 5 × 1.00, 4 × 0.50, 1 × 0.00 |

### TBR: 18/18, full marks

```
total_score = 18            all six axes = 3          quality_concern_titles = null
is_memorizable = False      is_trivial = False        is_realistic = True
tests_fail_before_solution = True    tests_pass_after_solution = True
```

> "M1 a strict-validation CLI with atomic persistence and correct point-in-polygon lookup
> (75 tests), M2 evolving it into a concurrent HTTP service with spatial index, LRU cache,
> batch/CRUD, and stats (38 tests). Tests fail before and pass after each cumulative
> solution, with no cross-milestone leakage and no regression. Verification is fully
> execution-based and deterministic (fixed fuzz seeds, reference cross-checks, relative
> performance thresholds), the environment is clean with no answer leakage… No trivial
> shortcuts or quality concerns found. Full marks: 18/18."

### The world-bounds unfairness is resolved

At `2898b43b` two classification tests carried 17 of 20 failures — `test_world_bounds`
(11×), which was unwinnable for any implementation that normalised longitudes before
running the zero-area check, and `test_lookup_antimeridian_cli` (6×). **Neither appears in
the failure set at this commit.**

### Failure spread (11 failing trials)

| Test | Step | Count | opus | avocado |
| --- | --- | --- | --- | --- |
| `test_concurrent_post_stress` | 2 | 5 | 0 | 5 |
| `test_grid_cells_exact_small` | 2 | 3 | 3 | 0 |
| `test_id_starting_with_hyphen_underscore` | 1 | 2 | 1 | 1 |
| `test_world_plus_crossing_batch` | 2 | 2 | 2 | 0 |

Every failure is a one- or two-test miss (74/75 on step 1, 36–37/38 on step 2). The two
dominant discriminators are now the fair concurrency and exact-reclaim families:
`test_concurrent_post_stress` (cache invalidation under concurrent POST) and
`test_grid_cells_exact_small` (index cells reclaimed exactly). No single assertion is
load-bearing, so an under-specified test could not by itself be driving the calibration.

### Reading

Fair difficulty with a real three-way spread and the best quality dimensions in the repo.
Two things to note:

1. **codex 10/10** — the top of the range is saturated. avocado 4/10 and opus 5/10 still
   give genuine separation, but further difficulty should come from the concurrency /
   exact-reclaim family where every real failure already lands.
2. **`test_id_starting_with_hyphen_underscore` is borderline.** It requires
   `add -abc --polygon …` to succeed; failing implementations return
   `error: unknown flag -abc`. The ID regex `^[A-Za-z0-9_-]{1,64}$` (step 1 spec, L29) does
   permit a leading hyphen, so it is derivable — but nothing states that a positional
   argument beginning with `-` must not be flag-parsed, which is the default in Go's `flag`
   package and most parsers. At 2 of 11 failures it is not carrying the calibration. One
   clause on L29 would close it: *an ID may begin with `-` or `_`; positional arguments must
   not be parsed as flags*.

Contamination moved LOW → MEDIUM at this commit. Still passing, but worth a glance.

## Authoring policy

Before authoring, read the [Model usage policy v2](https://codimango.internalmeta.com/tracks/t-bench#model-usage-policy) and the [Workplace v2 post](https://fb.workplace.com/groups/aaitaskquality/posts/1074223732270508). The policy page is the source of truth.

- The task seed must be human; any model may be a thought partner.
- Third-party models (including Codex, Claude, and Gemini) must not author instructions or multi-step prompts, model-visible content, tests, grading rubrics, test/grading configuration, test fixtures, or imported/traced grading inputs.
- Reviewed first-party drafting is allowed; a human must review, own, and remain responsible for the result.
- Never forward third-party-generated findings, summaries, candidate assertions, code, briefs, recommendations, or other context into a first-party session authoring protected content.
- Other hidden, non-test, non-grading files may use any model with human review.
