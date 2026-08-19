# codimango/distant-estimate – Multi-Turn Go Path Routing: Distance → Traffic-Aware (GIGA HARD EXTRA)

## Description
This multi-turn T-Bench task builds a Go CLI router in two phases, now both GIGA HARD EXTRA.

- **Turn1 (1_step_one) – GIGA HARD (149 tests):** Implements distance-based shortest path selection. The agent must read a graph JSON (`nodes` + undirected `edges` with positive `distance`), compute Dijkstra shortest paths minimizing total physical distance, handle source==destination, disconnected graphs (exit 1 with empty path), invalid graphs (duplicate nodes, empty IDs, self-loops, negative distance, missing node reference, **nodes containing non-string, edges containing non-object, edge missing from/to/distance, from/to non-string, distance null/bool/string/0/negative/-0, trailing comma, // comments, BOM, top-level not object, nodes empty array, edge leading/trailing space exact-match semantics, distance scientific 1e+3/1E+3/1e+2 plus valid, +5 invalid JSON** → exit 2), support single mode (`--graph --from --to`) and batch mode (`--graph --requests` array of `{source,destination}` or `{from,to}`), enforce lexicographically smallest path tie-breaking (critical for determinism when equal-distance paths exist, including **5-way/10-way ties, deeper diamond-of-diamonds where decision at depth 2, case-sensitive ASCII where 'A' < 'a' and '-' < '.' < '_'**, prefix shorter wins), produce help output containing `graph, from, to, requests, help` with **help precedence over unknown flags** (`--help --unknown` → help exit 0), **flag order independence**, **positional help `help`**, **single-mode empty/whitespace from/to invalid exit2 vs batch empty no-route**, **non-existing node query no-route exit1**, **duplicate edges reverse direction keep min**, **requests array entries must be objects, null/non-object invalid, null source/dest invalid, empty object {} invalid, missing key vs empty string distinction**, **invalid JSON trailing comma in requests**, **large graphs 2000 nodes 10000 edges, batch 1000 float distances, 5000 nodes <4.5s, 200 batch 2000 nodes <6s, same-source amortization, relative bound batch 100 <=25*base+1**. Built via `go build -o router .` from `/app`, stdlib only, no external deps, binary at `/app/router`.

- **Turn2 (2_step_two) – GIGA HARD EXTRA (296 tests):** Extends same binary with traffic-aware routing. New flag `--traffic` points to traffic JSON (`{"traffic":[{"from","to","factor":>0,"delay":>=0}]}` or direct array), validates factor >0, delay>=0 default 0, edge must exist in graph undirected, self-loop invalid, duplicate same unordered pair including reverse B-A **last-wins including delay reset to 0 when second entry missing delay**, missing entries default factor 1.0 delay 0, direct array and object-wrapped forms accepted, **BOM/trailing comma/comment must not crash**, **top-level must be object with traffic array or direct array – string/number/null invalid**, **wrapper traffic null vs empty valid distinction: {"traffic":null} invalid vs {"traffic":[]} and [] valid empty**, **direct array elements must be objects starting with '{' not null**, **null/number/string/array like [1,2,3] invalid**, **from/to must be string non-empty whitespace-only invalid, leading/trailing spaces exact-match no trim – " A" ≠ "A" → edge not found invalid (different from requests where " A" is no-route)**, **factor 0/negative/-0/null/string/bool/object/array invalid, factor scientific with plus 1e+3/1E+3/1e+2/2.5e+2 valid**, **delay negative/null/string/bool/object/array invalid, delay scientific plus 1e+2 valid**, **extra nested fields ignored**. Routing now minimizes effective distance `effective = raw*factor + delay` **strict per edge not (raw+delay)*factor** – multi-edge sum discrimination critical, reports both raw `distance`, `effective_distance`, `traffic_delay = effective - raw` (float tolerance 1e-6) per-edge sum, supports factor <1 (faster lane, negative delay allowed), **raw must be sum along effective-best path not raw-best** (reroute changes raw), batch mode outputs extra fields, source==dest 0 values, no-route outputs -1 for all distance fields, **3-level tie-break effective (1e-9) → raw (1e-9) → lex smallest path ASCII case-sensitive**, deeper diamond-of-diamonds effective equal where decision at depth2, **10-way effective tie B..K must pick B**, **secondary raw tie: effective equal 12 raw 11 vs 4 pick 4 even though B<C lex would pick 11**, **special chars '-' < '.' < '_'**, **case-sensitive 'A'<'a'**, **prefix shorter wins**, **float tolerance tie 1e-9 effective equal within epsilon → raw wins**, lexicographic tie-break remains but on effective distance. Help must now include `traffic` keyword (6 keywords). Turn1 functionality must still work when traffic not supplied (Turn1 tests pass with the Turn2 binary, excluding the Turn1-only "help must not contain traffic" checks). Also inherits all Turn1 extra-hard validations (BOM, trailing comma, leading space exact-match, null handling, help precedence, flag equals/order independence, etc.). Current suite sizes: **Turn1 149 tests, Turn2 296 tests**.

Why naive fails: simple BFS ignores weights; sorting only by distance without lexicographic tie-break fails deterministic tests; forgetting undirected nature; not handling duplicate edges with minimal distance including reverse; missing exit code distinction (0 all routed, 1 some no route, 2 invalid); ignoring batch order; traffic mode requiring effective-distance minimization not raw, requiring recomputed raw sum along traffic-chosen path (raw along effective-best not raw-best); handling both `{source,destination}` and `{from,to}` request keys; handling traffic file dual formats; floating tolerance; **help precedence** (Go flag package errors on unknown before help unless you scan args early); **BOM/trailing comma/comment JSON must not crash**; **nodes non-string, edges non-object must be invalid**; **edge " A" with leading space is not auto-trimmed – exact match matters – its reference missing → invalid graph, but request " A" is no-route, traffic " A" is invalid (edge not found)**; **null literal in batch source/dest – Go's json.Unmarshal null into string gives "" without error, must explicitly detect "null" raw**; **flag order independence**, **equals syntax --graph=path requires flag package or custom parsing**; **single vs batch empty semantics**; **deep lex tie requiring full path compare, not just second node**; **case-sensitive ASCII ordering**; **reverse duplicate min**; **traffic per-edge formula raw*factor+delay vs (raw+delay)*factor – sum over 2 edges discriminates**; **duplicate traffic last-wins with delay reset – second entry without delay must reset delay 0 not keep old delay**; **reverse duplicate traffic B-A overwrites A-B**; **factor scientific plus 1e+3 valid – many hand parsers reject plus**; **traffic wrapper null vs empty distinct – {"traffic":null} invalid vs [] valid**; **direct array invalid elements – null/number/string/array must be invalid**; **from/to whitespace-only invalid, leading/trailing space exact invalid**; **factor 0/negative invalid, delay negative invalid**; **secondary raw tie – effective equal raw differs pick raw smaller**; **float tolerance effective equal within 1e-9 considered tie**; **same-source amortization – cache per origin else 500 same vs 500 distinct fails 25% bound**; **output strictness – exactly 4 keys single with traffic, 6 keys batch, no extra, -1 for all fields on no-route**.

## Latest online validation result

**Commit `423ef128` (HEAD) · jobs 5139940 / 5139941 / 5139942 / 5139943 · AFTR job 5143546**

> **PASSING.** Oracle is 3/3, platform `validationStatus = passing`, and the Agentic
> Full-Task Review returned **`GOOD`** with difficulty **`GENUINELY_HARD`**, all 13
> required rubrics PASS and **secondary issues NONE**. Both blockers from the
> `a546ac05` run (golden OOM, Go-binary provenance) are fixed.

| Field | Value |
| --- | --- |
| `validationStatus` | **passing** — oracle 3/3 |
| Agentic Full-Task Review | **`GOOD`** (job 5143546) |
| Difficulty classification | **`GENUINELY_HARD`** |
| Required rubrics | **13/13 PASS**, trajectory analysis clean |
| Secondary issues | **NONE** |

### Completion rates

| Stage | Job | Agent / model | Overall | Step 1 | Step 2 (conditional on reaching it) |
| --- | --- | --- | --- | --- | --- |
| oracle | 5139942 | oracle | **3/3** | 3/3 | 3/3 |
| metacode | 5139943 | `meta/avocado-code-flex-5p15` | **5/10** | 7/10 | **5/7** |
| agent | 5139941 | `claude-opus-4-8` | **0/10** | 1/10 | 0/1 |
| codex | 5139940 | `gpt-5.5` | **0/10** | 0/10 | — |

Avocado at 5/10 sits at the top of the 3–5/10 target band. Do not add discriminating
tests.

### Model analysis

**Step 2 now carries real signal.** At the four preceding commits (`d0339450`,
`b895e663`, `14eccf86`, `a843117c`) Step 2 was passed by **every** trial that reached it —
17/17 conditional, i.e. zero discriminative power, and the headline pass rate was
entirely Step 1's doing. Six rounds of growing the Step 2 suite (251 → 261 → 269 → 279 →
285 → 295 → 296 tests) did not move that number, because `min_reward = 1.0` on Step 1
means only agents that already mastered strict-JSON validation and the tie-break cascade
ever see Step 2, and the added tests exercised those same two skills. At `423ef128` the
conditional rate is **5/7** — two genuine Step 2 failures.

**Frontier-model inversion — open question.** The weak model (5/10) outperforms
`claude-opus-4-8` (0/10) and `gpt-5.5` (0/10). Step 1 agent wall times are avocado
765–1307 s versus claude-code 1086–2019 s against `[steps.agent] timeout_sec = 1200`.
Worth confirming the strong models are failing on capability rather than being truncated
before accepting this as the intended discriminator.

### Known polish items (non-blocking; AFTR classified all as polish, not R06/R07 blockers)

1. **`find_bin` fallback candidates.** `CANDIDATES` still lists `/app/src/router` and
   `./router` after `/app/router`, and `find_bin` ignores the `go build` return code. If
   the build fails, the harness falls through to a pre-existing executable. Largely
   neutralised by the provenance test (ELF magic + `>500 KB` + Go runtime markers), which
   rejects a shell or Python stub — only a genuine Go binary built from other sources
   would slip through. Fix: reduce `CANDIDATES` to `["/app/router"]` and assert the build
   succeeded.
2. **`--help=1` / `-h=true` under-specified.** Tests assert both
   (`steps/1_step_one/tests/test_outputs.py:3333`), but the Turn-1 spec states only
   *"Equals form `--help=true` also help"* (line 19). Generalisable from the flag-syntax
   rule, but this is the pair that historically cost opus/codex trials. Fix: state that
   the equals form is help for any value. Note the spec file is author/Avocado-only.
3. **Absolute wall-clock perf gates.** ~39 `assert elapsed < N` assertions across both
   steps, tightest at 1.0 s (`test_traffic_performance_5000_nodes_dense_strict`) and 1.5 s.
   Hardware-dependent; relative bounds (`25*base+1`) are used elsewhere and are the safer
   pattern. Relax these first if flake appears on slow hosts.
4. **Canary GUID absent** from `environment/Dockerfile` and both `solution/solve.sh`.

Full static review: `.review/review-report_20260819_155613.md`.

## Anti-Cheating Analysis
- Hardcoded outputs: Tests use randomized graphs and traffic (generated per test), multiple graph shapes (triangle, diamond, line, disconnected, duplicate edges reverse, leading-space distinct IDs, special chars dot/slash/hyphen/underscore, case-sensitive). No fixed output can pass. Traffic tests use random factors/delays, duplicate last-wins random.
- Overfitting to visible tests: Tests are hidden under `/tests`; agent cannot read them in production TBR (hidden). Moreover, tests include dynamic tie-breaking where lexicographic order matters – requires general Dijkstra with path comparison sorted neighbors, priority queue ordered by (effective, raw, lex), not memorized. Effective formula trap cannot be overfit.
- Modifying test files: `test.sh` runs as root in container but reward file is written via `pytest --ctrf /logs/verifier/ctrf.json`; modifying test files would require writing to `/tests` which is read-only in TBR (or hidden).
- Bypassing intended solution path: **closed as of `423ef128`.** `/app/router` must be a Go binary built via `go build -o router .`, enforced three ways: (a) `tests/test.sh` deletes `/app/router` and force-rebuilds before pytest runs; (b) `find_bin` deletes `/app/router` and rebuilds again; (c) `test_go_binary_provenance` asserts the binary under test has ELF magic `\x7fELF`, is not a `#!` script, is `>500 KB`, and carries Go runtime markers (`go version -m`, else `Go`/`main.main`/`runtime.` byte scan). A shell or Python stub is rejected. Stdlib-only is checked separately via `test_stdlib_only` (`go.mod` no dotted-first-component require) plus `go list`. **Residual (polish, not a blocker per the AFTR):** `CANDIDATES` still lists `/app/src/router` and `./router` as fallbacks and `find_bin` ignores the `go build` return code, so a *genuine Go binary* built from other sources could be picked up if the agent makes the build fail. Recommended fix: reduce `CANDIDATES` to `["/app/router"]` and assert the build succeeded.
- Verifier isolation: `test.sh` creates restrictive lock via `mkdir -p /logs/verifier`, no `chmod 700 /tests`; but standard TBR hides tests. Output strictness checks exact keys, no extra fields, numbers not strings, path elements strings, -1 for no-route, prevents cheating via extra output.
- Effective formula cheating: test_traffic_effective_formula_strict_per_edge specifically checks multi-edge sum where per-edge vs combined formula differs (10*2+5 +10 =35 vs (10+5)*2+10=40) – agent must implement per-edge formula.
- Delay reset cheating: test_traffic_factor_zero_delay_reset_last_wins and duplicate reverse with delay reset checks that last duplicate without delay resets delay to 0 not keeps old.
