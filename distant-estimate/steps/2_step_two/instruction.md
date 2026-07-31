# Turn 2: Traffic-Aware Best Path Selection (Go) – Multi-Turn Step 2 – EXTRA HARD

## Background
Turn 1 built distance-based routing. Production now requires live congestion data. Extend same router binary (Turn1 code present via `inherit_prior_session`) to incorporate traffic multipliers and delay and select best path by **effective distance** (physical distance * factor + delay). This is EXTRA HARD – adds float factor+delay parsing, dual JSON formats, extra-field tolerance, 3-level tie-break effective→raw→lex, 5-way ties, large batches with traffic, validation of missing edge, whitespace, scientific notation, duplicate last-wins with direction.

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
- `--traffic <PATH>` – optional, path to traffic JSON. Routing must use traffic-aware effective distance for selection, while reporting raw distance. Also accept alternative long form `--traffic-factor`? No, only `--traffic` required.
- Help must now contain keywords: `graph`, `from`, `to`, `requests`, `traffic`, `help` (case-insensitive). Bare no args → help exit 0, must contain all 6.
- Order of flags does not matter; --traffic may appear before or after.
- Unknown flag (including misspelling) → exit 2, no stdout.
- Invalid traffic file (unreadable, invalid JSON, validation) → exit 2, no stdout.

### Traffic JSON Format (MUST) – BALANCED (HARD but documented)

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
- Each entry: `from`, `to` strings non-empty (whitespace-only invalid → exit2), must be nodes existing in graph, and edge between them must exist in graph undirected (if graph has A-B, traffic A-B valid, B-A also valid). If references non-existing node or non-existing graph edge → invalid exit 2.
- `factor` – required number >0, may be int, float, or scientific notation (e.g., `1e-2`=0.01). Must be >0. If <=0, missing, NaN, Inf, or not a number (string `"2.5"`, bool, object) → exit 2.
- `delay` – **optional** number >=0, default 0. May be int, float, scientific notation. If present, must be >=0, not NaN/Inf, not string. If <0 or invalid → exit 2. If duplicate traffic entries, last wins including delay. Extra unknown fields **other than factor/delay** inside traffic entry or top-level object must be **ignored**, not invalid (e.g., `{"from":"A","to":"B","factor":2,"extra":"ignore"}` valid; `{"from":"A","to":"B","factor":2,"delay":5,"unknown":1}` valid with delay 5).
- Duplicate traffic entries for same unordered pair: **last occurrence wins**. If duplicate with different directions (A->B and B->A), treat as same undirected edge, last wins (factor and delay both from last).
- Missing traffic entry for an edge → default factor 1.0 delay 0.0.
- No self-traffic (from==to or whitespace trimmed equal) → invalid exit 2.

### Routing Algorithm – Traffic-Aware (MUST) – EXTRA HARD

- Effective edge cost = `distance * factor + delay` (distance float, factor default 1.0, delay default 0.0). Example: distance 10, factor 2, delay 5 → effective 25.
- Path selection with traffic: minimize **sum effective** using Dijkstra. Raw distance sum is still sum of physical distances along chosen path (sum distance, not effective).
- Tie-breaking **EXTRA HARD 3-level**: When multiple paths have identical total effective_distance within 1e-9 tolerance, choose:
  1. Smaller effective (primary)
  2. If tie, smaller **raw distance** sum (secondary) — favors physically shorter route when congestion equal
  3. If still tie (both effective and raw equal), lexicographically smallest path case-sensitive (tertiary) — compare element-by-element, smaller string wins, prefix shorter wins.
  Tests include 3-way effective tie where raw equal (A-B-D, A-C-D, A-E-D all effective 10 raw 10, B<C<E so A-B-D wins) AND secondary raw tie-break where effective equal but raw differs (A-B-D effective 12 raw 11 vs A-C-D effective 12 raw 4 → A-C-D wins because raw smaller, even though B<C). Must sort neighbors and implement 3-level.
- Source == destination: path [source], distance 0, effective 0, delay 0 (traffic_delay 0).
- No path: same handling with extended fields.
- Float handling: distances, factors, delays may be int, float, scientific notation; sum may be float; output float; tolerance 1e-6 for comparison. Factor may be <1 (faster lane) → negative traffic_delay allowed (effective < raw).

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

### Business Rules & Edge Cases (MUST) – EXTRA HARD

- Turn1 still passes without traffic (when --traffic not supplied, behavior identical to Turn1)
- Traffic changes best path: direct edge short raw but high factor/delay → effective 50, alternative longer raw 12 factor1 → effective12, must pick alternative (minimize effective, not raw)
- Factor <1 faster, negative traffic_delay allowed (effective < raw)
- Batch order preserved, output order matches input order
- Help keywords 6 (graph, from, to, requests, traffic, help)
- Graph invalid → exit2 (including whitespace node IDs, duplicate nodes, self-loop, missing node ref, distance <=0, scientific notation valid, extra fields ignored)
- Traffic invalid: invalid JSON, missing file, referenced edge not in graph, nodes whitespace/empty, factor <=0 or missing or string, delay <0 or string or whitespace, self-loop, object without traffic key and not direct array → invalid exit2. Direct array form valid. Scientific notation valid for factor/delay.
- Requests invalid → exit2 for missing fields / not-string, but empty/whitespace source/destination in batch is **no route** not invalid (same as Turn1) — must handle.
- Unknown flags → exit2, including misspelling, --traffic not supported? No in Turn2 --traffic is supported, but unknown like --foobar → exit2. In Turn1 --traffic invalid, in Turn2 valid.
- Extra unknown fields in graph top-level, edge, requests, traffic (other than factor/delay) must be **ignored** (not invalid) – tests cover this. Top-level traffic file extra fields ignored, but object must have traffic key or be direct array.
- Stdlib only, no external require, binary /app/router
- Performance **EXTRA HARD**: 1000 nodes 5000 edges 100 requests <2 sec, 500 nodes 200 requests <2.5 sec, 500 requests batch with traffic <4 sec
- Float tolerance 1e-6 for effective, 1e-9 for tie detection, support scientific notation for distance/factor/delay, very small 1e-9 and large 1e9 factors

### Examples – HARD

```bash
go build -o router .
./router --graph graph.json --from A --to C --traffic traffic.json
# Graph A-B6 B-C6 A-C5, traffic A-C factor10 => picks A-B-C raw12 eff12 delay0

./router --graph graph.json --requests req.json --traffic traffic.json
# req.json may have extra fields: [{"source":"A","destination":"C","priority":1}]
# traffic.json may be [{"from":"A","to":"B","factor":0.001}] direct array
```

Success criteria hard – must handle all above.
