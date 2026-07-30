# Turn 2: Traffic-Aware Best Path Selection (Go) – Multi-Turn Step 2 – HARD

## Background
Turn 1 built distance-based routing. Production now requires live congestion data. Extend same router binary (Turn1 code present via `inherit_prior_session`) to incorporate traffic multipliers and select best path by **effective distance** (physical distance * traffic factor). This is HARD – adds float factor parsing, dual JSON formats, extra-field tolerance, 3-way effective tie-breaks, large batches with traffic, validation of missing edge, whitespace, factor string.

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

### Traffic JSON Format (MUST) – HARDER

```json
{
  "traffic": [
    {"from":"A","to":"B","factor":2.5, "extra":"ignore"},
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
- `factor` – required number >0, may be int or float. Must be >0. If <=0, missing, NaN, Inf, or not a number (string `"2.5"`, bool, object) → exit 2. Extra unknown fields inside traffic entry or top-level object must be **ignored**, not invalid (e.g., `{"from":"A","to":"B","factor":2,"delay":5}` is valid, factor 2).
- Duplicate traffic entries for same unordered pair: **last occurrence wins**. If duplicate with different directions (A->B and B->A), treat as same undirected edge, last wins.
- Missing traffic entry for an edge → default factor 1.0.
- No self-traffic (from==to or whitespace trimmed equal) → invalid exit 2.

### Routing Algorithm – Traffic-Aware (MUST) – HARD

- Effective edge cost = `distance * factor` (distance is float, factor default 1.0)
- Path selection with traffic: minimize **sum effective** using Dijkstra.
- Still compute raw distance sum along selected path.
- Tie-breaking: Same as Turn1 but on effective_distance within 1e-9 tolerance – if multiple paths have identical effective_distance, choose lexicographically smallest path case-sensitive. If still tie? Shorter wins. Tests include 3-way effective tie: A-B-D, A-C-D, A-E-D all effective 10 (raw may differ), B<C<E so A-B-D wins regardless of discovery order. Must sort neighbors.
- Source == destination: path [source], distance 0, effective 0, delay 0.
- No path: same handling with extended fields.
- Float handling: distances and factors float, sum may be float; output float; tolerance 1e-6 for comparison.

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

### Business Rules & Edge Cases (MUST) – HARDER

- Turn1 still passes without traffic
- Traffic changes best path: direct edge short but high factor 10 → effective 50, alternative longer raw 12 factor1 → effective12, must pick alternative
- Factor <1 faster, negative delay allowed
- Batch order preserved
- Help keywords 6
- Graph invalid → exit2 (including whitespace node IDs)
- Traffic invalid: invalid JSON, missing file, referenced edge not in graph, nodes whitespace, factor <=0, factor string, self-loop, extra top-level without traffic key (when expecting object?) – but direct array is valid alternative, object must have traffic key, array missing traffic key invalid
- Requests invalid → exit2
- Unknown flags → exit2
- Extra unknown fields in graph top-level, edge, requests, traffic must be **ignored** (not invalid) – tests cover this
- Stdlib only
- Performance: 500 nodes, 2000 edges, 100 requests with traffic <2 sec
- Float tolerance

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
