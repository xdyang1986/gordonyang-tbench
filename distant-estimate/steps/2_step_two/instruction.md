# Turn 2: Traffic-Aware Best Path Selection (Go) – Multi-Turn Step 2 – EXTRA HARD

## Background
Turn 1 built distance-based routing. Production now requires live congestion data. Extend same router binary (Turn1 code present via `inherit_prior_session`) to incorporate traffic multipliers and delay and select best path by **effective distance** (physical distance * factor + delay). This is EXTRA HARD – adds float factor+delay parsing, dual JSON formats, extra-field tolerance, 3-level tie-break effective→raw→lex, 5-way/10-way ties, large batches with traffic, validation of missing edge, whitespace, scientific notation, duplicate last-wins with direction, duplicate graph edges min, BOM handling, equals-sign flag syntax, factor formula discrimination.

Turn1 functionality must still work when --traffic not provided.

## Task – Extend Go Router at `/app`, built via `go build -o router .`

Same module, same binary `/app/router`. Must keep Turn1 flags working.

### Extended CLI Interface (MUST)

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h
```

New flag:
- `--traffic <PATH>` – optional, path to traffic JSON. Routing must use traffic-aware effective distance for selection, while reporting raw distance.
- Help must now contain keywords: `graph`, `from`, `to`, `requests`, `traffic`, `help` (case-insensitive). Bare no args → help exit 0, must contain all 6.
- **Equals syntax MUST**: Go's `flag` package allows `--flag=VALUE`. All flags must support both `--graph PATH` and `--graph=PATH`, similarly `--from=A`, `--to=B`, `--requests=PATH`, `--traffic=PATH`. Test `test_from_to_equals_syntax` in step1 required this; step2 must also support equals for `--traffic`.
- Order of flags does not matter; --traffic may appear before or after.
- Unknown flag (including misspelling and with equals, e.g., `--foobar=xxx`) → exit 2, no stdout.
- Invalid traffic file (unreadable, invalid JSON, validation, not found) → exit 2, no stdout. Traffic file missing/unreadable must be exit 2, not crash.

### Traffic JSON Format (MUST) – EXTRA HARD

```json
{
  "traffic": [
    {"from":"A","to":"B","factor":2.5, "delay":3, "extra":"ignore"},
    {"from":"B","to":"C","factor":0.5}
  ],
  "extra_top":"ignore"
}
```

Rules:
- Two accepted forms (must support both):
  1. Object-wrapped: `{"traffic": [ {...}, ... ] }`
  2. Direct array: `[{"from":"A","to":"B","factor":1.5}]`
- **Strict validation of wrapper**: If object form, top-level must have `traffic` key whose value is array. If `traffic` key present but value is not array (object, null, string, number) → invalid exit 2. Direct array form must have elements that are objects with required fields; `[1,2,3]` or `["A"]` or `[null]` → invalid exit 2. Object without traffic key and not direct array (e.g., `{"foo":[]}`) → invalid exit 2.
- Each entry: `from`, `to` strings non-empty (whitespace-only invalid → exit2). **Leading/trailing spaces are NOT trimmed for matching**: Traffic `" A"` is distinct from `"A"`; if graph has node `"A"` but traffic says `" A"`, it references non-existing node → invalid exit 2 (edge not in graph). This catches implementations that trim incorrectly. Must be nodes existing in graph, and edge between them must exist in graph undirected (if graph has A-B, traffic A-B valid, B-A also valid). If references non-existing node or non-existing graph edge → invalid exit 2.
- Graph may have duplicate edges between same unordered pair with different distances – keep smallest distance for routing (as in Turn1). Traffic factor applies to that minimal edge, not first occurrence.
- `factor` – required number >0, may be int, float, or scientific notation (e.g., `1e-2`=0.01). Must be >0. If <=0, missing, NaN, Inf, or not a number (string `"2.5"`, bool, object, array, null) → exit 2. JSON forbids `+5` and `NaN`/`Infinity` as literals – writing `{"factor":+5}` or `{"factor":NaN}` is invalid JSON → exit 2. `-0` / `-0.0` is 0 → invalid because not >0. `0e0` is 0 → invalid.
- `delay` – **optional** number >=0, default 0. May be int, float, scientific notation. If present, must be >=0, not NaN/Inf, not string/bool/object. If <0 or invalid → exit 2. `+5` for delay also invalid JSON → exit 2. If duplicate traffic entries, last wins **including delay reset**: If first entry has `delay:10` and second entry for same edge has only `factor` (no delay), effective delay must be 0 (reset), not retained 10. Extra unknown fields **other than factor/delay/from/to** inside traffic entry or top-level object must be **ignored**, not invalid (e.g., `{"from":"A","to":"B","factor":2,"extra":"ignore"}` valid; `{"from":"A","to":"B","factor":2,"delay":5,"unknown":1}` valid with delay 5).
- Duplicate traffic entries for same unordered pair: **last occurrence wins**. If duplicate with different directions (A->B and B->A), treat as same undirected edge, last wins (factor and delay both from last). More than 2 duplicates – last wins.
- Missing traffic entry for an edge → default factor 1.0 delay 0.0.
- No self-traffic (from==to or whitespace trimmed equal) → invalid exit 2.
- BOM handling: JSON files may start with UTF-8 BOM `\xef\xbb\xbf`. Go's `encoding/json` does NOT strip BOM, so BOM file would be invalid JSON → exit 2 is acceptable, but must NOT crash/panic. Alternatively, stripping BOM and treating as valid is also acceptable. Must not crash.

### Routing Algorithm – Traffic-Aware (MUST) – EXTRA HARD

- Effective edge cost = `distance * factor + delay` **strictly** (distance float, factor default 1.0, delay default 0.0). Example: distance 10, factor 2, delay 5 → effective 25. This is NOT `(distance+delay)*factor` nor `distance*(factor+delay)`. Tests include formula-discrimination case where correct formula picks A-B-C but wrong formula picks A-C.
- Path selection with traffic: minimize **sum effective** using Dijkstra. Raw distance sum is still sum of physical distances along chosen path (sum distance, not effective). `traffic_delay` = `effective_distance - distance` (sum over path, includes factor-1 contribution plus delays, not just sum of delay fields). Must compute as `effective - raw`, not just sum of `delay` fields, otherwise factor<1 negative delay case fails.
- Tie-breaking **EXTRA HARD 3-level**: When multiple paths have identical total effective_distance within 1e-9 tolerance, choose:
  1. Smaller effective (primary)
  2. If tie, smaller **raw distance** sum (secondary) — favors physically shorter route when congestion equal
  3. If still tie (both effective and raw equal), lexicographically smallest path case-sensitive (tertiary) — compare element-by-element, smaller string wins, prefix shorter wins.
  Tests include 3-way effective tie where raw equal (A-B-D, A-C-D, A-E-D all effective 10 raw 10, B<C<E so A-B-D wins) AND secondary raw tie-break where effective equal but raw differs (A-B-D effective 12 raw 11 vs A-C-D effective 12 raw 4 → A-C-D wins because raw smaller, even though B<C) AND tertiary lex with traffic where all factors 1. Must sort neighbors and implement 3-level.
- Source == destination: path [source], distance 0, effective 0, delay 0 (traffic_delay 0).
- No path: same handling with extended fields.
- Float handling: distances, factors, delays may be int, float, scientific notation; sum may be float; output float; tolerance 1e-6 for comparison. Factor may be <1 (faster lane) → negative traffic_delay allowed (effective < raw). Very small `1e-9` and very large `1e9` factors must be handled without overflow/underflow beyond float64.

### Output Format Extended (MUST) – HARD

Single without traffic: as Turn1

Single with traffic success:
`{"path":["A","C"],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}`
- All three distance fields must be present when --traffic supplied and path found
- Path selected by effective, not raw
- Floating tolerance 1e-6

No path with traffic:
`{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1

Invalid → exit2 no stdout

Batch without traffic: as Turn1

Batch with traffic per line:
`{"source":"A","destination":"C","path":["A","B","C"],"distance":8,"effective_distance":12.5,"traffic_delay":4.5}`

No path:
`{"source":"A","destination":"C","path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}`

Exit: 0 all routed, 1 some no route, 2 invalid

### Output Format Extended (MUST) – HARD

Single without traffic: as Turn1

Single with traffic success:
`{"path":["A","C"],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}`
- All three distance fields must be present when --traffic supplied and path found
- Path selected by effective, not raw
- `traffic_delay` must equal `effective_distance - distance` within tolerance, not just sum of delay fields (factor<1 contributes)
- Floating tolerance 1e-6

No path with traffic:
`{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1

Invalid → exit2 no stdout

Batch without traffic: as Turn1

Batch with traffic per line:
`{"source":"A","destination":"C","path":["A","B","C"],"distance":8,"effective_distance":12.5,"traffic_delay":4.5}`

No path:
`{"source":"A","destination":"C","path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}`

Exit: 0 all routed, 1 some no route, 2 invalid

### Business Rules & Edge Cases (MUST) – EXTRA HARD

- Turn1 still passes without traffic (when --traffic not supplied, behavior identical to Turn1)
- Traffic changes best path: direct edge short raw but high factor/delay → effective 50, alternative longer raw 12 factor1 → effective12, must pick alternative (minimize effective, not raw). Tests include formula-discrimination where `(distance+delay)*factor` would pick wrong path.
- Factor <1 faster, negative traffic_delay allowed (effective < raw)
- Batch order preserved, output order matches input order
- Help keywords 6 (graph, from, to, requests, traffic, help). Help must also work with extra flags like `--help --traffic dummy --requests b` still exit 0 and contain keywords.
- Graph invalid → exit2 (including whitespace node IDs, duplicate nodes, self-loop, missing node ref, distance <=0, scientific notation valid, extra fields ignored). **Duplicate edges**: keep smallest distance; traffic applies to that minimal edge. Test `duplicate_edges_min_plus_traffic` discriminates.
- Traffic invalid (MUST):
  - invalid JSON, missing file / unreadable / not found, referenced edge not in graph, nodes whitespace/empty, **leading/trailing spaces in from/to** (e.g., `" A"` or `"A "` ) → invalid because exact node not found / edge not in graph
  - factor <=0 or missing or string/bool/object/array/null, delay <0 or string/bool/whitespace/object, self-loop, object without traffic key and not direct array, traffic key not array (object/null/string/number), direct array containing non-object or missing required fields → invalid exit2. Plus-sign `+5` for factor/delay is invalid JSON → exit2. `-0` is invalid for factor (0 not >0). BOM file must not crash: either valid if stripped or invalid exit2.
  - Direct array form valid, object-wrapped valid. Extra top-level fields ignored. Extra fields in traffic entry ignored.
- Requests invalid → exit2 for missing fields / not-string, but empty/whitespace source/destination in batch is **no route** not invalid (same as Turn1) — must handle. This extends to traffic batch: empty destination `{"source":"A","destination":""}` → no route with effective -1.
- Unknown flags → exit2, including misspelling, `--foobar`, `--foobar=xxx`, `--traffic` with equals must be valid, but unknown with equals also exit2.
- Extra unknown fields in graph top-level, edge, requests, traffic (other than from/to/factor/delay) must be **ignored** (not invalid) – tests cover this. Top-level traffic file extra fields ignored, but object must have traffic key or be direct array.
- **Duplicate traffic last-wins reset**: If last occurrence omits delay, delay resets to 0, not retained from previous. Both directions verified.
- Stdlib only, no external require, binary /app/router
- Performance **EXTRA HARD**: 
  - 1000 nodes 5000 edges 100 requests <2 sec, 500 nodes 200 requests <2.5 sec, 500 requests batch with traffic <4 sec remain
  - **Relative bounds**: Batch 100 with traffic must not be ~100x single request (per-request re-parse), measured as `elapsed_100 <= 25*elapsed_1 + 1s`. Batch 2000 with traffic similarly `<=40*elapsed_1 +1.5s`. Implementations that re-parse graph/traffic per request or do O(E*T) scan per relaxation will fail.
  - Dense 100 nodes ~1000-5000 edges with traffic must be <1.0s (strict) to catch O(V^2) Dijkstra without heap.
- Float tolerance 1e-6 for effective, 1e-9 for tie detection, support scientific notation for distance/factor/delay, very small 1e-9 and large 1e9-1e12 factors and delays up to 1e6.

### Examples – HARD (no pseudocode)

```bash
go build -o router .
./router --graph graph.json --from A --to C --traffic traffic.json
# Graph A-B6 B-C6 A-C5, traffic A-C factor10 => picks A-B-C raw12 eff12 delay0

./router --graph graph.json --requests req.json --traffic traffic.json
# req.json may have extra fields: [{"source":"A","destination":"C","priority":1}]
# traffic.json may be [{"from":"A","to":"B","factor":0.001}] direct array

./router --graph=graph.json --from=A --to=B --traffic=traffic.json
# equals syntax must work for all flags
```

Success criteria hard – must handle all above. Previous oracle 100% conditional pass – new tests add 12 hard discriminators (duplicate min+traffic, leading/trailing space invalid, file not found, equals syntax, formula discrimination, traffic key not array, direct array invalid elements, duplicate delay reset reverse, 2000 relative, BOM traffic, plus-sign delay invalid) to break naive extensions.
