# codimango/distant-estimate – Multi-Turn Go Path Routing: Distance → Traffic-Aware (GIGA HARD EXTRA)

## Description
This multi-turn T-Bench task builds a Go CLI router in two phases, now both GIGA HARD EXTRA.

- **Turn1 (1_step_one) – GIGA HARD (140 tests):** Implements distance-based shortest path selection. The agent must read a graph JSON (`nodes` + undirected `edges` with positive `distance`), compute Dijkstra shortest paths minimizing total physical distance, handle source==destination, disconnected graphs (exit 1 with empty path), invalid graphs (duplicate nodes, empty IDs, self-loops, negative distance, missing node reference, **nodes containing non-string, edges containing non-object, edge missing from/to/distance, from/to non-string, distance null/bool/string/0/negative/-0, trailing comma, // comments, BOM, top-level not object, nodes empty array, edge leading/trailing space exact-match semantics, distance scientific 1e+3/1E+3/1e+2 plus valid, +5 invalid JSON** → exit 2), support single mode (`--graph --from --to`) and batch mode (`--graph --requests` array of `{source,destination}` or `{from,to}`), enforce lexicographically smallest path tie-breaking (critical for determinism when equal-distance paths exist, including **5-way/10-way ties, deeper diamond-of-diamonds where decision at depth 2, case-sensitive ASCII where 'A' < 'a' and '-' < '.' < '_'**, prefix shorter wins), produce help output containing `graph, from, to, requests, help` with **help precedence over unknown flags** (`--help --unknown` → help exit 0), **flag order independence**, **positional help `help`**, **single-mode empty/whitespace from/to invalid exit2 vs batch empty no-route**, **non-existing node query no-route exit1**, **duplicate edges reverse direction keep min**, **requests array entries must be objects, null/non-object invalid, null source/dest invalid, empty object {} invalid, missing key vs empty string distinction**, **invalid JSON trailing comma in requests**, **large graphs 2000 nodes 10000 edges, batch 1000 float distances, 5000 nodes <4.5s, 200 batch 2000 nodes <6s, same-source amortization, relative bound batch 100 <=25*base+1**. Built via `go build -o router .` from `/app`, stdlib only, no external deps, binary at `/app/router`.

- **Turn2 (2_step_two) – GIGA HARD EXTRA (269 tests):** Extends same binary with traffic-aware routing. New flag `--traffic` points to traffic JSON (`{"traffic":[{"from","to","factor":>0,"delay":>=0}]}` or direct array), validates factor >0, delay>=0 default 0, edge must exist in graph undirected, self-loop invalid, duplicate same unordered pair including reverse B-A **last-wins including delay reset to 0 when second entry missing delay**, missing entries default factor 1.0 delay 0, direct array and object-wrapped forms accepted, **BOM/trailing comma/comment must not crash**, **top-level must be object with traffic array or direct array – string/number/null invalid**, **wrapper traffic null vs empty valid distinction: {"traffic":null} invalid vs {"traffic":[]} and [] valid empty**, **direct array elements must be objects starting with '{' not null**, **null/number/string/array like [1,2,3] invalid**, **from/to must be string non-empty whitespace-only invalid, leading/trailing spaces exact-match no trim – " A" ≠ "A" → edge not found invalid (different from requests where " A" is no-route)**, **factor 0/negative/-0/null/string/bool/object/array invalid, factor scientific with plus 1e+3/1E+3/1e+2/2.5e+2 valid**, **delay negative/null/string/bool/object/array invalid, delay scientific plus 1e+2 valid**, **extra nested fields ignored**. Routing now minimizes effective distance `effective = raw*factor + delay` **strict per edge not (raw+delay)*factor** – multi-edge sum discrimination critical, reports both raw `distance`, `effective_distance`, `traffic_delay = effective - raw` (float tolerance 1e-6) per-edge sum, supports factor <1 (faster lane, negative delay allowed), **raw must be sum along effective-best path not raw-best** (reroute changes raw), batch mode outputs extra fields, source==dest 0 values, no-route outputs -1 for all distance fields, **3-level tie-break effective (1e-9) → raw (1e-9) → lex smallest path ASCII case-sensitive**, deeper diamond-of-diamonds effective equal where decision at depth2, **10-way effective tie B..K must pick B**, **secondary raw tie: effective equal 12 raw 11 vs 4 pick 4 even though B<C lex would pick 11**, **special chars '-' < '.' < '_'**, **case-sensitive 'A'<'a'**, **prefix shorter wins**, **float tolerance tie 1e-9 effective equal within epsilon → raw wins**, lexicographic tie-break remains but on effective distance. Help must now include `traffic` keyword (6 keywords). Turn1 functionality must still work when traffic not supplied (Turn1 tests pass with the Turn2 binary, excluding the Turn1-only "help must not contain traffic" checks). Also inherits all Turn1 extra-hard validations (BOM, trailing comma, leading space exact-match, null handling, help precedence, flag equals/order independence, etc.). Current suite sizes: **Turn1 140 tests, Turn2 269 tests**.

Why naive fails: simple BFS ignores weights; sorting only by distance without lexicographic tie-break fails deterministic tests; forgetting undirected nature; not handling duplicate edges with minimal distance including reverse; missing exit code distinction (0 all routed, 1 some no route, 2 invalid); ignoring batch order; traffic mode requiring effective-distance minimization not raw, requiring recomputed raw sum along traffic-chosen path (raw along effective-best not raw-best); handling both `{source,destination}` and `{from,to}` request keys; handling traffic file dual formats; floating tolerance; **help precedence** (Go flag package errors on unknown before help unless you scan args early); **BOM/trailing comma/comment JSON must not crash**; **nodes non-string, edges non-object must be invalid**; **edge " A" with leading space is not auto-trimmed – exact match matters – its reference missing → invalid graph, but request " A" is no-route, traffic " A" is invalid (edge not found)**; **null literal in batch source/dest – Go's json.Unmarshal null into string gives "" without error, must explicitly detect "null" raw**; **flag order independence**, **equals syntax --graph=path requires flag package or custom parsing**; **single vs batch empty semantics**; **deep lex tie requiring full path compare, not just second node**; **case-sensitive ASCII ordering**; **reverse duplicate min**; **traffic per-edge formula raw*factor+delay vs (raw+delay)*factor – sum over 2 edges discriminates**; **duplicate traffic last-wins with delay reset – second entry without delay must reset delay 0 not keep old delay**; **reverse duplicate traffic B-A overwrites A-B**; **factor scientific plus 1e+3 valid – many hand parsers reject plus**; **traffic wrapper null vs empty distinct – {"traffic":null} invalid vs [] valid**; **direct array invalid elements – null/number/string/array must be invalid**; **from/to whitespace-only invalid, leading/trailing space exact invalid**; **factor 0/negative invalid, delay negative invalid**; **secondary raw tie – effective equal raw differs pick raw smaller**; **float tolerance effective equal within 1e-9 considered tie**; **same-source amortization – cache per origin else 500 same vs 500 distinct fails 25% bound**; **output strictness – exactly 4 keys single with traffic, 6 keys batch, no extra, -1 for all fields on no-route**.

## Latest online validation result

**Commit `a546ac05` (HEAD, v1.31) · run 2026-08-17 · jobs 4968241 / 4968242 / 4968243 / 4968244 · AFTR run 9220238**

> **BLOCKED — validation FAILED.** The oracle fails 0/3: the reference solution is
> OOM-killed on one Step 2 performance test. The Agentic Full-Task Review returned
> **`BAD_GRADING_WEAK`** (secondary **`BAD_GOLDEN`**), TBR is `fail`, and the platform
> set `status = needs_revision`. Two concrete defects must be fixed — see below.

| Field | Value |
| --- | --- |
| `validationStatus` | **failed** — Oracle validation 0/3 (all must pass) |
| `status` / `reviewStatus` | **needs_revision** |
| `tbdReviewStatus` | **fail** |
| Agentic Full-Task Review | **`BAD_GRADING_WEAK`** (primary R06), secondary **`BAD_GOLDEN`** (R12/R13) |
| Difficulty classification | GOOD — Opus 231/595 tests (39%), oracle 188/196 (96%) |
| Novelty risk | MEDIUM |
| Contamination | Not checked — repo not yet covered by the pipeline |
| Provenance | CLEAN |
| Embedding dedup | **0.7993** against a 0.8 threshold — 0.0007 under, effectively at the line |
| Structural checks | 10/10 PASS |

| Stage | Job | Result | Reward split |
| --- | --- | --- | --- |
| oracle | 4968243 | **0/3** | 3 × 0.50 (Step 1 passes, Step 2 fails every run) |
| metacode (avocado `avocado-5.14-code`) | 4968241 | **1/10** | 1 × 1.00, 4 × 0.50, 5 × 0.00 |
| agent (opus `claude-opus-4-8`) | 4968242 | **0/10** | 10 × 0.00 |
| codex (`gpt-5.5`) | 4968244 | **0/10** | 10 × 0.00 |

The pass/fail balance gate reports "passed" (avocado non-trivial at 1/10 and ≥1 agent
solved), but the oracle gate fails, so overall validation is **failed**.

### Blocker 1 — `BAD_GOLDEN` / R12 + R13: the reference solution is OOM-killed

Every oracle trial fails exactly one test. Step 1 is 140/140; Step 2 is 268/269, failing
`test_traffic_batch_2000_with_traffic_perf_v7`:

```
>  assert proc.returncode in (0,1)
E  assert -9 in (0, 1)
```

`-9` is SIGKILL — the router is killed mid-stream (stdout is truncated partway through
the batch, stderr empty), i.e. the OOM killer, not a timeout assertion.

**Root cause**, confirmed in `steps/2_step_two/solution/solve.sh`: the batch path builds
`cache := make(map[string]allRes)` keyed by source, and `allRes` holds
`bestPath map[string][]string` — the full path slice to *every* destination. The test's
workload is a 1000-node line graph with 2000 requests whose sources are `N{i%1000}`, so
there are **1000 distinct sources**. The cache therefore retains 1000 × 1000 destination
entries whose average path length on a line graph is ~333 nodes — on the order of 3×10⁸
string entries, never evicted.

**Fix directions.** Cache predecessor pointers (`prev map[string]string`, O(N) per source)
and reconstruct paths on demand, or bound/evict the cache. Note the full-path map *is*
genuinely needed during a single Dijkstra run for the lexicographic tie-break — but not
after it, so convert to predecessors before inserting into the cache.

**This is a golden bug, not a test bug.** Avocado's 1.00 trial passed the same test, and
the AFTR notes the suite "can accept an independent correct implementation."

### Blocker 2 — `BAD_GRADING_WEAK` / R06: the Go-binary constraint is not enforced

`find_bin` in `steps/2_step_two/tests/test_outputs.py:6` iterates `CANDIDATES` and returns
any existing executable **before** falling through to `go build`. `test_stdlib_only`
inspects Go sources, not the executable actually invoked. A shell or Python script placed
at `/app/router` would satisfy the stdlib-only Go requirement.

**Fix per the AFTR:** delete any pre-existing `/app/router`, force
`go build -o /app/router .` before tests run, and add a binary-provenance check.

### Also flagged: `test.sh` runs the whole suite twice on failure

```bash
if pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA 2>&1; then
  status=${PIPESTATUS[0]}
else
  pytest /tests/test_outputs.py -rA 2>&1   # full re-run
```

Any failure re-executes the entire suite, including the 2000-request performance tests.
That doubles wall-clock and makes perf failures far more expensive to diagnose. The AFTR
suggests removing the fallback or making it conditional.

### Calibration note: opus and codex are 0/10 because of two `--help` tests

All four downloaded opus/codex trials fail **exactly the same 2 of 140** Step 1 tests and
nothing else — 138/140 otherwise pass:

| Test | Step | Trials |
| --- | --- | --- |
| `test_help_equals_syntax` | 1 | 4/4 |
| `test_help_precedence_extra_hard` | 1 | 4/4 |

`instruction.md:19` does state *"Equals form `--help=true` also help"* plus help
precedence, so the rule is specified — but the tests additionally require `--help=1` and
`-h=true`, which must be generalized from the flag-syntax rule at line 21 rather than read
directly. The consequence is that the task's headline difficulty — Dijkstra, the
lexicographic tie-break cascade, the per-edge `raw*factor+delay` formula — is **not** what
gates the strong models. A help-flag parsing detail is. Worth deciding whether that is the
intended discriminator before the next run.


## Anti-Cheating Analysis
- Hardcoded outputs: Tests use randomized graphs and traffic (generated per test), multiple graph shapes (triangle, diamond, line, disconnected, duplicate edges reverse, leading-space distinct IDs, special chars dot/slash/hyphen/underscore, case-sensitive). No fixed output can pass. Traffic tests use random factors/delays, duplicate last-wins random.
- Overfitting to visible tests: Tests are hidden under `/tests`; agent cannot read them in production TBR (hidden). Moreover, tests include dynamic tie-breaking where lexicographic order matters – requires general Dijkstra with path comparison sorted neighbors, priority queue ordered by (effective, raw, lex), not memorized. Effective formula trap cannot be overfit.
- Modifying test files: `test.sh` runs as root in container but reward file is written via `pytest --ctrf /logs/verifier/ctrf.json`; modifying test files would require writing to `/tests` which is read-only in TBR (or hidden).
- Bypassing intended solution path: **currently a gap — see Blocker 2 below.** The intent is that `/app/router` must be a Go binary built via `go build -o router .` with a stdlib-only check (`go list` no dotted imports, `go.mod` no external require, `test_stdlib_only`). In practice `find_bin` accepts any pre-existing executable at `/app/router` before it attempts a rebuild, and `test_stdlib_only` inspects Go sources rather than the executable under test, so a shell or Python stub would satisfy the Go-only requirement. This is the AFTR's primary finding and must be fixed.
- Verifier isolation: `test.sh` creates restrictive lock via `mkdir -p /logs/verifier`, no `chmod 700 /tests`; but standard TBR hides tests. Output strictness checks exact keys, no extra fields, numbers not strings, path elements strings, -1 for no-route, prevents cheating via extra output.
- Effective formula cheating: test_traffic_effective_formula_strict_per_edge specifically checks multi-edge sum where per-edge vs combined formula differs (10*2+5 +10 =35 vs (10+5)*2+10=40) – agent must implement per-edge formula.
- Delay reset cheating: test_traffic_factor_zero_delay_reset_last_wins and duplicate reverse with delay reset checks that last duplicate without delay resets delay to 0 not keeps old.
