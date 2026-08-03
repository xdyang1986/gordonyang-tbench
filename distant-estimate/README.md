# codimango/distant-estimate – Multi-Turn Go Path Routing: Distance → Traffic-Aware

## Description
This multi-turn T-Bench task builds a Go CLI router in two phases:
- **Turn1 (1_step_one):** Implements distance-based shortest path selection. The agent must read a graph JSON (`nodes` + undirected `edges` with positive `distance`), compute Dijkstra shortest paths minimizing total physical distance, handle source==destination, disconnected graphs (exit 1 with empty path), invalid graphs (duplicate nodes, empty IDs, self-loops, negative distance, missing node reference → exit 2), support single mode (`--graph --from --to`) and batch mode (`--graph --requests` array of `{source,destination}` or `{from,to}`), enforce lexicographically smallest path tie-breaking (critical for determinism when two equal-distance paths exist), and produce help output containing `graph, from, to, requests, help`. Built via `go build -o router .` from `/app`, stdlib only, no external deps, binary at `/app/router`.

- **Turn2 (2_step_two):** Extends same binary with traffic-aware routing. New flag `--traffic` points to traffic JSON (`{"traffic":[{"from","to","factor":>0}]}` or direct array), validates factor >0, edge must exist in graph, self-loop invalid, duplicate last-wins, missing entries default factor 1.0, direct array and object-wrapped forms accepted. Routing now minimizes effective distance `distance * factor` (raw * factor), reports both raw `distance`, `effective_distance`, `traffic_delay = effective - raw` (float tolerance 1e-6), supports factor <1 (faster lane, negative delay), batch mode outputs extra fields, source==dest 0 values, no-route outputs -1 for all distance fields, lexicographic tie-break remains but on effective distance. Help must now include `traffic` keyword. Turn1 functionality must still work when traffic not supplied.

Why naive fails: simple BFS ignores weights; sorting only by distance without lexicographic tie-break fails deterministic tests; forgetting undirected nature; not handling duplicate edges with minimal distance; missing exit code distinction (0 all routed, 1 some no route, 2 invalid); ignoring batch order; traffic mode requiring effective-distance minimization not raw, requiring recomputed raw sum along traffic-chosen path; handling both `{source,destination}` and `{from,to}` request keys; handling traffic file dual formats; floating tolerance; backup help keyword checks.

## Completion Rates

**Latest online validation — commit `2c6713a`, status: PASSING.** Structural 10/10 pass, oracle 3/3, contamination LOW, provenance CLEAN. All stages completed.

| Stage | Agent / Model | Full multi-turn | Turn 1 | Turn 2 | Mean |
|-------|---------------|-----------------|--------|--------|------|
| Oracle | oracle | 3/3 (100%) | 3/3 | 3/3 | 1.00 |
| Agent | claude-code / claude-opus-4-8 | 5/10 (50%) | 5/10 | 5/5 | 0.50 |
| Metacode | meta/avocado-5.14-code | 3/10 (30%) | 3/10 | 3/3 | 0.30 |
| Codex | gpt-5.5 | 1/10 (10%) | 1/10 | 1/1 | 0.10 |

Turn 2 only runs after a Turn-1 pass, so Turn-2 denominators equal Turn-1 passes.

## Model Analysis

**Turn 1 is the sole discriminator.** Every agent that clears Turn 1 passes Turn 2 (conditional Turn-2 pass rate is 100% for all: codex 1/1, avocado 3/3, opus 5/5, oracle 3/3). Difficulty is entirely in the distance-based Dijkstra CLI, not the traffic-aware extension.

### Failure analysis (from trial ctrf verifier output)

| Model | Dominant Turn-1 failure | Frequency | Root cause |
|-------|-------------------------|-----------|------------|
| Codex (gpt-5.5) | `test_from_to_equals_syntax` | **9/9 failing trials** | CLI doesn't accept equals-sign flag syntax `--graph=… --from=A --to=B`; hand-rolled arg parser only handles space-separated `--from A` form (Go's `flag` package would handle both for free). |
| Metacode (avocado) | `test_from_to_equals_syntax` | **7/7 failing trials** | Same equals-syntax parsing gap (one trial additionally failed `test_edge_string_distance_invalid`). |
| Agent (opus-4-8) | `test_batch_with_missing_field_invalid` | 4/5 failing trials | Batch request missing `destination` (`[{"source":"A"}]`) must exit 2; model returned no-route/exit 1 instead. One additional trial had a catastrophic build/run failure (all tests failed). |

Key observations:
- **`test_from_to_equals_syntax` is the primary wall** for the weaker models — codex and avocado fail it in essentially every losing trial. It's a CLI-robustness discriminator: agents using Go's stdlib `flag` package pass automatically; those hand-parsing `os.Args` and expecting `--from A` (space form) miss the `--from=A` equals form.
- **Opus is above that wall** (handles equals syntax) and instead trips on a subtler spec detail: exit-code-2 validation for a batch request with a missing field.
- **Turn 2 (traffic-aware routing) is not discriminating** in this run — no model that reached it ever failed it. If more difficulty is desired, it belongs in Turn 2, since Turn 1 already carries the entire signal.
- Oracle 3/3 confirms solvability; the codex/avocado failures are spec-adherence (flag parsing), not environmental.

## Anti-Cheating Analysis
- Hardcoded outputs: Tests use randomized graphs and traffic (generated per test), multiple graph shapes (triangle, diamond, line, disconnected, duplicate edges). No fixed output can pass.
- Overfitting to visible tests: Tests are hidden under `/tests`; agent cannot read them in production TBR (hidden). Local oracle runs from repo root may expose tests, but production grading hides them. Moreover, tests include dynamic tie-breaking where lexicographic order matters – requires general Dijkstra with path comparison, not memorized.
- Modifying test files: `test.sh` runs as root in container but reward file is written via `pytest --ctrf /logs/verifier/ctrf.json`; modifying test files would require writing to `/tests` which is read-only in TBR (or hidden). Even if modified locally, reward would still be 0 unless tests pass.
- Bypassing intended solution path: Task requires Go binary built via `go build -o router .`, stdlib only check (`go list` no dotted imports, `go.mod` no external require). Agent cannot bypass by writing Python stub router – tests invoke `/app/router` binary which must be Go-built (checked via file type? but more importantly, must handle all flag combos). Attempting to cheat by hardcoding specific graph IDs would fail on randomized graphs and batch order preservation tests.
- Verifier isolation: `test.sh` creates restrictive lock via `mkdir -p /logs/verifier`, no `chmod 700 /tests`; but standard TBR hides tests, so oracle can't read future traffic files. Our solution does not rely on test visibility.
