# Turn 2: Traffic-Aware Best Path Selection (Go) – Step 2 – EXTRA HARD (trimmed)

Turn1 built distance-based routing. Extend same binary to incorporate traffic multipliers+delay, selecting best path by **effective = distance*factor+delay**. Turn1 code present via `inherit_prior_session`. Turn1 must still work when --traffic not provided.

### CLI (MUST)

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) → help containing graph, from, to, requests, traffic, help, exit 0
```

- `--traffic <PATH>` optional. Effective = distance*factor+delay (strict, not (d+delay)*factor).
- **Equals syntax:** `--flag=value` accepted alongside `--flag value` for all flags (`--graph=`, `--from=`, `--to=`, `--requests=`, `--traffic=`). Go flag package gives this free.
- Order of flags does not matter.
- Unknown flag (including `--foobar=xxx`) → exit 2 no stdout.
- Invalid traffic file (unreadable, not found, invalid JSON like `//` comments or trailing comma, BOM must not crash) → exit 2.
- **Flag evolution (non-cumulative by design):** Turn1 does NOT support `--traffic` (exit 2 unknown), Turn2 DOES. `test_unknown_traffic_flag_in_step1_invalid` is Turn1-only.

### Traffic JSON (MUST) – EXTRA HARD (concise)

Forms:
1. Object-wrapped: `{"traffic":[...]}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5}]`

Validation:
- Wrapper strict: `traffic` key must be array. If value is object, string, number, **null** → invalid exit 2. **Null vs empty:** `{"traffic": null}` is invalid, not empty; `{"traffic":[]}` or `[]` is valid empty (no traffic, factor 1.0). Go collapses null/nil – you must check `json.RawMessage` trimmed == "null" to distinguish.
- Direct array elements must be objects with `from,to,factor`; `[1,2,3]`, `["A"]`, `[null]`, missing factor → invalid.
- Object without `traffic` key and not direct array (e.g., `{}`, `{"foo":[]}`) → invalid.
- Each entry: `from,to` non-empty, whitespace-only invalid, must be nodes in graph and edge must exist undirected (A-B and B-A same). **Leading/trailing spaces NOT trimmed:** `" A"` ≠ `"A"` → invalid (edge not found). If `from==to` after trim → invalid.
- Graph duplicate edges: keep min distance; traffic applies to that min.
- `factor` required >0 int/float/scientific (`1e-2`). If <=0, missing, NaN, Inf, string, bool, object, array, null → exit2. JSON forbids `+5`, `NaN`, `Infinity` → invalid JSON exit2. `-0` is 0 → invalid.
- `delay` optional >=0 default 0 int/float/scientific. If <0 or NaN/Inf/string/bool → exit2. `+5` invalid JSON. Duplicate last-wins **including delay reset**: second entry without delay resets delay to 0.
- Duplicate traffic same unordered pair (including reverse direction) → last wins (factor+delay both from last).
- Missing traffic entry for edge → default factor 1.0 delay 0.0.
- Extra unknown fields in traffic entry (`extra`) and top-level (`extra_top`, `version`) ignored.

### Routing (MUST) – EXTRA HARD

- Effective edge = `distance*factor+delay`. Path effective = sum effective. Raw = sum distance along chosen path.
- `traffic_delay` = `effective - raw` (includes factor-1 contribution, not just sum delay). Factor<1 → negative delay allowed.
- Dijkstra minimizing effective. Source==dest → path [src], distance 0, eff 0, delay 0. No path → `path:[], distance:-1, effective:-1, traffic_delay:-1` exit1.
- **3-level tie:** effective tie within 1e-9 → smaller raw → smaller lex path (element-by-element case-sensitive, prefix shorter wins). Tests include 3-way tie (B<C<E) and secondary raw tie (effective equal raw differs).
- Float tolerance 1e-6 output, 1e-9 tie detection, support `1e-9` to `1e12`.

### Output (MUST)

Single no traffic: `{"path":[...],"distance":8}` as Turn1
Single with traffic: `{"path":["A","C"],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` – all three present when --traffic and path found
No path with traffic: `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1
Batch: one JSON line per request in order, same extended fields, exit 0 all routed, 1 some no-route, 2 invalid
Batch no-traffic same as Turn1, batch with traffic per line includes effective and traffic_delay

### Business Rules – EXTRA HARD (trimmed)

- Turn1 still works without traffic.
- Traffic changes best path (direct short raw high factor → pick longer raw low effective). Formula discrimination test catches `(d+delay)*factor` bug.
- Factor<1 faster, negative delay allowed.
- Batch order preserved.
- Help 6 keywords, help with extra flags still help.
- Graph invalid → exit2 (whitespace, duplicate, self-loop, missing node, distance<=0, invalid JSON trailing comma/comments/BOM must not crash, file not found).
- Traffic invalid → exit2 (invalid JSON, not found, edge not in graph, whitespace/empty from/to, leading/trailing spaces invalid, factor<=0 missing string, delay<0 string, self-loop, wrapper not array, null invalid vs [] valid, direct array invalid elements). Direct array and object-wrapped both valid, extra fields ignored.
- Requests: **missing key vs empty string distinct:** `{"source":"A"}` missing destination → invalid exit2, while `{"source":"","destination":"B"}` empty string → no-route exit1. Must use *string or map to distinguish (Go struct collapses). Same pattern for traffic null: `{"traffic":null}` invalid vs `[]` valid empty – check RawMessage == "null".
- Unknown flags → exit2.
- Duplicate traffic last-wins reset delay to 0 if missing.
- Stdlib only: `go.mod` no external require, `go list -f '{{join .Imports " "}}' .` no dotted imports – tested by `test_stdlib_only`.
- Flag sets evolve: Turn1 no traffic, Turn2 adds traffic – non-cumulative by design.
- Performance EXTRA HARD: 1000 nodes 5000 edges 100 req <2s, 500 nodes 200 req <2.5s, dense 100 nodes 1000-5000 edges <1s, batch 100 traffic relative `<=25*base+1`, 2000 traffic `<=100*base+3`, 5000 traffic `<=200*base+5`. Catches O(n²) and per-request re-parse.

Examples:
```bash
go build -o router .
./router --graph graph.json --from A --to C --traffic traffic.json
./router --graph=graph.json --from=A --to=B --traffic=traffic.json  # equals syntax
```
