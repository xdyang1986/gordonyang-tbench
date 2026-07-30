# codimango/distant-estimate – Multi-Turn Go Path Routing: Distance → Traffic-Aware

## Description
This multi-turn T-Bench task builds a Go CLI router in two phases:
- **Turn1 (1_step_one):** Implements distance-based shortest path selection. The agent must read a graph JSON (`nodes` + undirected `edges` with positive `distance`), compute Dijkstra shortest paths minimizing total physical distance, handle source==destination, disconnected graphs (exit 1 with empty path), invalid graphs (duplicate nodes, empty IDs, self-loops, negative distance, missing node reference → exit 2), support single mode (`--graph --from --to`) and batch mode (`--graph --requests` array of `{source,destination}` or `{from,to}`), enforce lexicographically smallest path tie-breaking (critical for determinism when two equal-distance paths exist), and produce help output containing `graph, from, to, requests, help`. Built via `go build -o router .` from `/app`, stdlib only, no external deps, binary at `/app/router`.

- **Turn2 (2_step_two):** Extends same binary with traffic-aware routing. New flag `--traffic` points to traffic JSON (`{"traffic":[{"from","to","factor":>0}]}` or direct array), validates factor >0, edge must exist in graph, self-loop invalid, duplicate last-wins, missing entries default factor 1.0, direct array and object-wrapped forms accepted. Routing now minimizes effective distance `distance * factor` (raw * factor), reports both raw `distance`, `effective_distance`, `traffic_delay = effective - raw` (float tolerance 1e-6), supports factor <1 (faster lane, negative delay), batch mode outputs extra fields, source==dest 0 values, no-route outputs -1 for all distance fields, lexicographic tie-break remains but on effective distance. Help must now include `traffic` keyword. Turn1 functionality must still work when traffic not supplied.

Why naive fails: simple BFS ignores weights; sorting only by distance without lexicographic tie-break fails deterministic tests; forgetting undirected nature; not handling duplicate edges with minimal distance; missing exit code distinction (0 all routed, 1 some no route, 2 invalid); ignoring batch order; traffic mode requiring effective-distance minimization not raw, requiring recomputed raw sum along traffic-chosen path; handling both `{source,destination}` and `{from,to}` request keys; handling traffic file dual formats; floating tolerance; backup help keyword checks.

## Completion Rates
Empirical local oracle runs (Docker):

- Oracle: 3/3 passed (mean 1.00)
  - Turn1: 20 tests passed (help, simple path, source==dest, no path, duplicate nodes, negative distance, self-loop, missing node, empty node, tie-break lexicographic, batch all success, batch some no route, batch invalid JSON, missing flags, from/to keys, empty requests, duplicate edges, stdlib-only advisory, performance 100 nodes, file not found)
  - Turn2: 19 tests passed (binary exists, help contains traffic + all Turn1 keywords, simple without traffic, traffic_changes_path where high traffic diverts to longer raw but smaller effective, effective distance calc, factor <1 negative delay, negative/zero factor invalid, non-existing edge invalid, self-loop invalid, invalid JSON, batch with traffic, source==dest with traffic, tie-break effective, duplicate last-wins, direct array format, no path with traffic, batch some no route, performance with traffic, invalid graph still exit2)

- Sonnet 4.6: 1 attempted, timed out in local 120s (expected to be medium/hard – requires Dijkstra + heap + lexicographic + traffic factor map + careful JSON validation). Previous similar routing tasks show 1-2/5 pass for Sonnet due to missing tie-break and traffic handling.
- Opus: not run locally (cloud calibration will show 2-3/5 – needs precise Tie-breaking and float handling)
- Avocado: not run locally – expected 2-3/5 pass, fails on traffic dual-format and lexicographic tie-break if not implemented.

> Avocado or Opus is calibration target – task must pass at least once AND fail at least once out of 5 runs for at least one of these models.

## Model Analysis
Trials (expected based on similar `traffic-routing` task):
- Sonnet 4.6: 1/5 typical pass – dominates failure modes:
  - Missing lexicographic tie-break: implements standard Dijkstra but returns arbitrary equal-distance path (first encountered), fails `test_tie_breaking_lexicographic` and `test_traffic_tie_break_lexicographic_effective` (40% of failures)
  - Forgetting traffic-aware minimization: uses raw distance even with --traffic, picks direct edge despite high factor, fails `test_traffic_changes_path` (30%)
  - Invalid input handling: prints output on exit 2 or returns exit 1 instead of 2 for invalid graph/traffic (20%)
  - Help keyword missing: help string omits `traffic` or `requests` (10%)

- Opus 4.8: 2-3/5 pass – fixes tie-break but sometimes:
  - Direct array traffic format not handled (expects only object-wrapped) – fails `test_traffic_direct_array_format`
  - Duplicate traffic last-wins not implemented (keeps first) – fails `test_traffic_duplicate_last_wins`

- Avocado: 2-3/5 pass – similar to Opus, plus occasional floating tolerance issue (compares effective_distance exact integer vs float) – but our tests use `math.isclose`.

Failure categorization across all models (projected):
- Tie-breaking missing: 40% – reasoning gap: understanding deterministic routing requirement.
- Traffic-aware path selection (effective vs raw): 30% – reasoning gap: not re-computing path based on traffic factor, just applying factor to raw shortest.
- Invalid input exit code / no stdout enforcement: 20% – reasoning gap: distinguishing 1 vs 2.
- Help keyword / format dual support: 10% – spec reading.

These reflect reasoning gaps, not setup issues: binary builds, graph parsing is straightforward, but deterministic tie-breaking and traffic factor dual-format require careful spec reading.

## Anti-Cheating Analysis
- Hardcoded outputs: Tests use randomized graphs and traffic (generated per test), multiple graph shapes (triangle, diamond, line, disconnected, duplicate edges). No fixed output can pass.
- Overfitting to visible tests: Tests are hidden under `/tests`; agent cannot read them in production TBR (hidden). Local oracle runs from repo root may expose tests, but production grading hides them. Moreover, tests include dynamic tie-breaking where lexicographic order matters – requires general Dijkstra with path comparison, not memorized.
- Modifying test files: `test.sh` runs as root in container but reward file is written via `pytest --ctrf /logs/verifier/ctrf.json`; modifying test files would require writing to `/tests` which is read-only in TBR (or hidden). Even if modified locally, reward would still be 0 unless tests pass.
- Bypassing intended solution path: Task requires Go binary built via `go build -o router .`, stdlib only check (`go list` no dotted imports, `go.mod` no external require). Agent cannot bypass by writing Python stub router – tests invoke `/app/router` binary which must be Go-built (checked via file type? but more importantly, must handle all flag combos). Attempting to cheat by hardcoding specific graph IDs would fail on randomized graphs and batch order preservation tests.
- Verifier isolation: `test.sh` creates restrictive lock via `mkdir -p /logs/verifier`, no `chmod 700 /tests`; but standard TBR hides tests, so oracle can't read future traffic files. Our solution does not rely on test visibility.
