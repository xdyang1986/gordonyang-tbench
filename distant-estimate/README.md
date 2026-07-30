# codimango/distant-estimate – Multi-Turn Go Path Routing: Distance → Traffic-Aware

## Description
This multi-turn T-Bench task builds a Go CLI router in two phases:
- **Turn1 (1_step_one):** Implements distance-based shortest path selection. The agent must read a graph JSON (`nodes` + undirected `edges` with positive `distance`), compute Dijkstra shortest paths minimizing total physical distance, handle source==destination, disconnected graphs (exit 1 with empty path), invalid graphs (duplicate nodes, empty IDs, self-loops, negative distance, missing node reference → exit 2), support single mode (`--graph --from --to`) and batch mode (`--graph --requests` array of `{source,destination}` or `{from,to}`), enforce lexicographically smallest path tie-breaking (critical for determinism when two equal-distance paths exist), and produce help output containing `graph, from, to, requests, help`. Built via `go build -o router .` from `/app`, stdlib only, no external deps, binary at `/app/router`.

- **Turn2 (2_step_two):** Extends same binary with traffic-aware routing. New flag `--traffic` points to traffic JSON (`{"traffic":[{"from","to","factor":>0}]}` or direct array), validates factor >0, edge must exist in graph, self-loop invalid, duplicate last-wins, missing entries default factor 1.0, direct array and object-wrapped forms accepted. Routing now minimizes effective distance `distance * factor` (raw * factor), reports both raw `distance`, `effective_distance`, `traffic_delay = effective - raw` (float tolerance 1e-6), supports factor <1 (faster lane, negative delay), batch mode outputs extra fields, source==dest 0 values, no-route outputs -1 for all distance fields, lexicographic tie-break remains but on effective distance. Help must now include `traffic` keyword. Turn1 functionality must still work when traffic not supplied.

Why naive fails: simple BFS ignores weights; sorting only by distance without lexicographic tie-break fails deterministic tests; forgetting undirected nature; not handling duplicate edges with minimal distance; missing exit code distinction (0 all routed, 1 some no route, 2 invalid); ignoring batch order; traffic mode requiring effective-distance minimization not raw, requiring recomputed raw sum along traffic-chosen path; handling both `{source,destination}` and `{from,to}` request keys; handling traffic file dual formats; floating tolerance; backup help keyword checks.

## Completion Rates
**Latest online validation — commit `c85906b` ("Balance difficulty: Turn1 hard but solvable"), status: PASSING.**
Aggregate: avgReward **0.839** across 28 trials. Structural 10/10 pass, contamination LOW, provenance CLEAN.

| Gate | Model | Pass | Rate | Mean reward |
|------|-------|------|------|-------------|
| Oracle | oracle | 3/3 | 100% | 1.00 |
| Metacode | meta/avocado-5.14-code | 5/10 | 50% | 0.55 |
| Codex | gpt-5.5 | 10/10 | 100% | 1.00 |
| Agent | claude-opus-4-8 | 10/10 | 100% | 1.00 |

The **metacode/avocado gate at 5/10 (0.55)** is the discriminating calibration signal — it passes at least once and fails at least once, satisfying the calibration requirement.

Metacode (avocado) per-trial breakdown (10 trials):
- **5 full pass** (reward 1.0) — both turns solved.
- **1 partial** (reward 0.5) — Turn1 solved, `firstFailedStep = 2_step_two` (Turn2 traffic-aware routing failed).
- **4 fail** (reward 0.0) — `firstFailedStep = 1_step_one` (could not complete Turn1 distance routing).

This confirms the intended difficulty shape: **Turn1 is the primary gate** for the weak model (4/10 can't finish it), and Turn2 adds a further discriminating step (1 more trial drops there). Codex (gpt-5.5) and Opus solve it cleanly (too easy for the strong models), while avocado sits in the target 40–60% band.

> Calibration target satisfied: metacode/avocado passes ≥1 and fails ≥1 out of 10 (5/10). Oracle 3/3 confirms the task is solvable; codex 10/10 confirms no environmental blockers.

## Model Analysis
Empirical from the latest online run (commit `c85906b`):

- **Oracle: 3/3 (100%)** — reference solution is correct and deterministic across randomized graph/traffic fixtures.
- **Metacode / avocado-5.14-code: 5/10 (50%)** — the discriminating model. Failure distribution:
  - **Turn1 failure (4/10):** cannot complete distance-based Dijkstra routing to the point of passing all Turn1 tests within the step budget — the harder-but-solvable Turn1 (36 tests) blocks the weak model outright before Turn2 is ever reached.
  - **Turn2 failure after Turn1 pass (1/10):** solves distance routing but fails the traffic-aware extension (effective-distance minimization / dual traffic-file format / tie-break on effective distance).
- **Codex / gpt-5.5: 10/10 (100%)** — strong model solves both turns reliably; confirms the task has no setup/build friction.
- **Agent / Opus 4.8: 10/10 (100%)** — clean pass, consistent with codex; Opus is above the discrimination band for this calibration.

Interpretation: difficulty is concentrated in **Turn1 completion for the weak model** (the primary gate), with **Turn2 traffic-aware routing** as the secondary discriminator. These are reasoning/spec-adherence gaps (Dijkstra + lexicographic tie-break, then effective-distance re-selection and dual-format traffic parsing), not environment issues — the binary builds and graph parsing is straightforward, as evidenced by 100% oracle and codex pass rates.

## Anti-Cheating Analysis
- Hardcoded outputs: Tests use randomized graphs and traffic (generated per test), multiple graph shapes (triangle, diamond, line, disconnected, duplicate edges). No fixed output can pass.
- Overfitting to visible tests: Tests are hidden under `/tests`; agent cannot read them in production TBR (hidden). Local oracle runs from repo root may expose tests, but production grading hides them. Moreover, tests include dynamic tie-breaking where lexicographic order matters – requires general Dijkstra with path comparison, not memorized.
- Modifying test files: `test.sh` runs as root in container but reward file is written via `pytest --ctrf /logs/verifier/ctrf.json`; modifying test files would require writing to `/tests` which is read-only in TBR (or hidden). Even if modified locally, reward would still be 0 unless tests pass.
- Bypassing intended solution path: Task requires Go binary built via `go build -o router .`, stdlib only check (`go list` no dotted imports, `go.mod` no external require). Agent cannot bypass by writing Python stub router – tests invoke `/app/router` binary which must be Go-built (checked via file type? but more importantly, must handle all flag combos). Attempting to cheat by hardcoding specific graph IDs would fail on randomized graphs and batch order preservation tests.
- Verifier isolation: `test.sh` creates restrictive lock via `mkdir -p /logs/verifier`, no `chmod 700 /tests`; but standard TBR hides tests, so oracle can't read future traffic files. Our solution does not rely on test visibility.
