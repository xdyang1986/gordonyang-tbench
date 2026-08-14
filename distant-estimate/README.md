# codimango/distant-estimate – Multi-Turn Go Path Routing: Distance → Traffic-Aware (EXTRA HARD)

## Description
This multi-turn T-Bench task builds a Go CLI router in two phases:

- **Turn1 (1_step_one) – EXTRA HARD (97 tests, was 69):** Implements distance-based shortest path selection. The agent must read a graph JSON (`nodes` + undirected `edges` with positive `distance`), compute Dijkstra shortest paths minimizing total physical distance, handle source==destination, disconnected graphs (exit 1 with empty path), invalid graphs (duplicate nodes, empty IDs, self-loops, negative distance, missing node reference, **nodes containing non-string, edges containing non-object, edge missing from/to/distance, from/to non-string, distance null/bool/string/0/negative, trailing comma, // comments, BOM, top-level not object, nodes empty array, edge leading/trailing space exact-match semantics** → exit 2), support single mode (`--graph --from --to`) and batch mode (`--graph --requests` array of `{source,destination}` or `{from,to}`), enforce lexicographically smallest path tie-breaking (critical for determinism when equal-distance paths exist, including **5-way/10-way ties, deeper diamond-of-diamonds where decision at depth 2, case-sensitive ASCII where 'A' < 'a' and '-' < '.' < '_'**), produce help output containing `graph, from, to, requests, help` with **help precedence over unknown flags** (`--help --unknown` → help exit 0), **flag order independence**, **positional help `help`**, **single-mode empty/whitespace from/to invalid exit2 vs batch empty no-route**, **non-existing node query no-route exit1**, **duplicate edges reverse direction keep min**, **requests array entries must be objects, null/non-object invalid, null source/dest invalid, empty object {} invalid, missing key vs empty string distinction**, **invalid JSON trailing comma in requests**, **large graphs 2000 nodes 10000 edges, batch 1000 float distances**. Built via `go build -o router .` from `/app`, stdlib only, no external deps, binary at `/app/router`.

- **Turn2 (2_step_two):** Extends same binary with traffic-aware routing. New flag `--traffic` points to traffic JSON (`{"traffic":[{"from","to","factor":>0}]}` or direct array), validates factor >0, edge must exist in graph, self-loop invalid, duplicate last-wins, missing entries default factor 1.0, direct array and object-wrapped forms accepted. Routing now minimizes effective distance `distance * factor + delay` (raw * factor + delay), reports both raw `distance`, `effective_distance`, `traffic_delay = effective - raw` (float tolerance 1e-6), supports factor <1 (faster lane, negative delay), batch mode outputs extra fields, source==dest 0 values, no-route outputs -1 for all distance fields, **3-level tie-break effective → raw → lex**, lexicographic tie-break remains but on effective distance. Help must now include `traffic` keyword. Turn1 functionality must still work when traffic not supplied. Also inherits all Turn1 extra-hard validations (BOM, trailing comma, leading space exact-match, null handling, help precedence, etc.) – Turn2 solution updated to 122 tests passing plus 96/97 Turn1 tests (excluding Turn1-only traffic unknown).

Why naive fails: simple BFS ignores weights; sorting only by distance without lexicographic tie-break fails deterministic tests; forgetting undirected nature; not handling duplicate edges with minimal distance including reverse; missing exit code distinction (0 all routed, 1 some no route, 2 invalid); ignoring batch order; traffic mode requiring effective-distance minimization not raw, requiring recomputed raw sum along traffic-chosen path; handling both `{source,destination}` and `{from,to}` request keys; handling traffic file dual formats; floating tolerance; **help precedence** (Go flag package errors on unknown before help unless you scan args early); **BOM/trailing comma/comment JSON must not crash**; **nodes non-string, edges non-object must be invalid**; **edge " A" with leading space is not auto-trimmed – exact match matters – its reference missing → invalid graph, but request " A" is no-route**; **null literal in batch source/dest – Go's json.Unmarshal null into string gives "" without error, must explicitly detect "null" raw**; **flag order independence**; **single vs batch empty semantics**; **deep lex tie requiring full path compare, not just second node**; **case-sensitive ASCII ordering**; **reverse duplicate min**.

## What Changed to Make Step1 Harder (this PR)

- Added 28 new tests (69 → 97):
  - `test_invalid_graph_nodes_contain_non_string`, `test_invalid_graph_edges_contain_non_object`, `test_invalid_graph_edge_missing_fields`, `test_invalid_graph_edge_from_to_not_string`, `test_invalid_graph_edge_distance_various_invalid`, `test_invalid_graph_json_trailing_comma`, `test_invalid_graph_json_comment`, `test_invalid_graph_json_bom`, `test_graph_top_not_object_invalid`, `test_edge_with_leading_trailing_space_invalid`, `test_node_id_with_leading_space_distinct_valid`, `test_request_non_object_entries_invalid`, `test_request_with_null_source_invalid` (including raw `null` literal), `test_request_empty_object_invalid`, `test_batch_with_missing_field_invalid` (explicit dominant failure), `test_help_with_extra_invalid_flags_still_help`, `test_help_positional`, `test_flag_order_independence`, `test_single_mode_empty_from_invalid`, `test_query_non_existing_node_no_route`, `test_duplicate_edges_reverse_min`, `test_lexicographic_deeper_tie` (diamond of diamonds), `test_lexicographic_case_sensitive_ascii`, `test_large_graph_2000_nodes`, `test_batch_1000_float_distances`, `test_requests_file_invalid_json_trailing_comma`, `test_invalid_graph_nodes_empty_array`, `test_batch_source_equals_dest_batch`.
- Updated `instruction.md` to EXTRA HARD spec covering all new validations, help precedence, flag order, exact-match space semantics, JSON malformation, type checks.
- Rewrote `solution/solve.sh` for Turn1 with robust parsing: top-level must be object, nodes array elements must be strings, edges array elements must be objects starting with '{' not null, explicit "null" literal detection, leading/trailing space exact-match (no auto-trim except whitespace-only check), help early scan before flag.Parse, null handling for batch.
- Updated Turn2 solution similarly to pass new Turn1 validations (122 Turn2 tests + 96 excluding Turn1-only unknown traffic flag).
- Performance thresholds tightened: 2000 nodes <3.5s, 1000 float batch <5s.

## Completion Rates (old, before hardening)

**Latest online validation — commit `2c6713a`, status: PASSING.** Structural 10/10 pass, oracle 3/3, contamination LOW, provenance CLEAN. All stages completed.

| Stage | Agent / Model | Full multi-turn | Turn 1 | Turn 2 | Mean |
|-------|---------------|-----------------|--------|--------|------|
| Oracle | oracle | 3/3 (100%) | 3/3 | 3/3 | 1.00 |
| Agent | claude-code / claude-opus-4-8 | 5/10 (50%) | 5/10 | 5/5 | 0.50 |
| Metacode | meta/avocado-5.14-code | 3/10 (30%) | 3/10 | 3/3 | 0.30 |
| Codex | gpt-5.5 | 1/10 (10%) | 1/10 | 1/1 | 0.10 |

Turn 2 only runs after a Turn-1 pass, so Turn-2 denominators equal Turn-1 passes.

Expected after hardening: Turn1 pass rate should drop significantly (from 50% → <20%) due to added traps. Dominant old failures (`test_from_to_equals_syntax` and `test_batch_with_missing_field_invalid`) remain but now accompanied by help precedence, BOM/trailing comma, leading-space exact-match, non-string nodes, non-object edges, null literal, reverse duplicate, deeper lex tie, case-sensitive tie, etc.

### Previous Failure analysis (from trial ctrf verifier output, pre-hardening)

| Model | Dominant Turn-1 failure | Frequency | Root cause |
|-------|-------------------------|-----------|------------|
| Codex (gpt-5.5) | `test_from_to_equals_syntax` | **9/9 failing trials** | CLI doesn't accept equals-sign flag syntax `--graph=… --from=A --to=B`; hand-rolled arg parser only handles space-separated `--from A` form (Go's `flag` package would handle both for free). |
| Metacode (avocado) | `test_from_to_equals_syntax` | **7/7 failing trials** | Same equals-syntax parsing gap (one trial additionally failed `test_edge_string_distance_invalid`). |
| Agent (opus-4-8) | `test_batch_with_missing_field_invalid` | 4/5 failing trials | Batch request missing `destination` (`[{"source":"A"}]`) must exit 2; model returned no-route/exit 1 instead. One additional trial had a catastrophic build/run failure (all tests failed). |

Key observations pre-hardening:
- **`test_from_to_equals_syntax` is the primary wall** for weaker models.
- Opus handles equals but trips on missing-key vs empty-string distinction.
- Turn2 was not discriminating.

New harder gates added to push pass rate lower and require robust JSON validation, exact-match space semantics, null handling, help precedence, deeper lex ties.

## Anti-Cheating Analysis
- Hardcoded outputs: Tests use randomized graphs and traffic (generated per test), multiple graph shapes (triangle, diamond, line, disconnected, duplicate edges reverse, leading-space distinct IDs). No fixed output can pass.
- Overfitting to visible tests: Tests are hidden under `/tests`; agent cannot read them in production TBR (hidden). Moreover, tests include dynamic tie-breaking where lexicographic order matters – requires general Dijkstra with path comparison, not memorized.
- Modifying test files: `test.sh` runs as root in container but reward file is written via `pytest --ctrf /logs/verifier/ctrf.json`; modifying test files would require writing to `/tests` which is read-only in TBR (or hidden).
- Bypassing intended solution path: Task requires Go binary built via `go build -o router .`, stdlib only check (`go list` no dotted imports, `go.mod` no external require). Agent cannot bypass by writing Python stub router – tests invoke `/app/router` binary which must be Go-built.
- Verifier isolation: `test.sh` creates restrictive lock via `mkdir -p /logs/verifier`, no `chmod 700 /tests`; but standard TBR hides tests.
