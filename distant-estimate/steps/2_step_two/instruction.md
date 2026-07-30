# Turn 2: Traffic-Aware Best Path Selection (Go) – Multi-Turn Step 2

## Background
Turn 1 built distance-based routing. Production traffic now requires live congestion data. Extend the same router binary (Turn1 code present via `inherit_prior_session`) to incorporate traffic multipliers and select the best path by **effective distance** (physical distance * traffic factor).

Turn1 functionality must still work when --traffic is not provided.

## Task – Extend Go Router at `/app`, built via `go build -o router .`

Same module, same binary `/app/router`. Must keep Turn1 flags working.

### Extended CLI Interface (MUST)

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h
```

New flag:
- `--traffic <PATH>` – optional, path to traffic JSON (see format). If provided, routing must use traffic-aware effective distance for path selection, while still reporting raw distance.
- Help must now contain keywords: `graph`, `from`, `to`, `requests`, `traffic`, `help` (case-insensitive check). Must still handle bare no args → help exit 0, must contain all keywords.
- Order of flags does not matter; --traffic may appear before or after others.
- Invalid traffic file (unreadable, invalid JSON, validation failure) → exit 2, no stdout.

### Traffic JSON Format (MUST)

```json
{
  "traffic": [
    {"from":"A","to":"B","factor":2.5},
    {"from":"B","to":"C","factor":0.5}
  ]
}
```

Rules:
- `traffic` – required array in object, or alternative format: array directly `[{"from":"A","to":"B","factor":1.5}]` (accept both; object wrapping recommended format). Tests accept both forms.
- Each entry: `from`, `to` strings non-empty must be nodes existing in graph, and edge between them must exist in graph (undirected existence – if graph has A-B edge, traffic A-B is valid, B-A also valid). If traffic references non-existing node or non-existing graph edge → invalid input exit 2.
- `factor` – number >0 (float allowed). Must be >0. If <=0 or missing or not number → exit 2.
- Duplicate traffic entries for same unordered pair: last occurrence wins (or first – deterministically choose last). If duplicate with different directions (A->B and B->A), treat as same undirected edge unless you implement directional – for simplicity spec says **undirected factor**: factor applies both ways. If both directions specified separately, last wins.
- Missing traffic entry for an edge → default factor 1.0 (no congestion).
- No self-traffic (from==to) → invalid exit 2.

### Routing Algorithm – Traffic-Aware (MUST)

- Effective edge cost = `distance * factor` (factor default 1.0 if no traffic entry).
- Path selection in traffic mode: minimize **sum of effective distances** (effective_distance) using Dijkstra.
- Still compute **raw distance** sum (sum of original distances) along selected path for reporting.
- Tie-breaking: Same as Turn1 but on effective_distance – if multiple paths have identical effective_distance (within floating tolerance 1e-9), choose lexicographically smallest path.
- Source == destination: path [source], distance 0, effective_distance 0, traffic_delay 0.
- No path: same as Turn1 handling but with extended fields when traffic present.

### Output Format Extended (MUST)

Single query **without** --traffic: Same as Turn1 (backward compat):
`{"path":["A","B"],"distance":5}` success exit 0
`{"path":[],"distance":-1}` no route exit1

Single query **with** --traffic:

Success:
`{"path":["A","C"],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}`
- `distance` = sum raw distances (int, but may output int; tests accept float if same value)
- `effective_distance` = sum effective (float, may be integer value but output as number)
- `traffic_delay` = effective - raw (float)
- All three must be present when --traffic supplied and path found.
- Path must be selected based on effective_distance, not raw.
- Floating comparisons: tests allow tolerance 1e-6 for effective_distance and traffic_delay.

No path with traffic:
`{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit 1

Invalid → exit 2 no stdout.

Batch mode **without** traffic: Same as Turn1.

Batch mode **with** traffic:

For each request, one JSON line:

Success:
`{"source":"A","destination":"C","path":["A","B","C"],"distance":8,"effective_distance":12.5,"traffic_delay":4.5}`

No path:
`{"source":"A","destination":"C","path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}`

Exit codes batch with traffic:
- 0 all routed
- 1 at least one no route
- 2 invalid

### Business Rules & Edge Cases (MUST)

- Turn1 still passes without traffic: your Turn2 binary will be tested with Turn1 cases too.
- Traffic changes best path: explicit test where direct edge A-C distance 5 factor 10 => effective 50, alternative A-B-C distance 6+6=12 factor 1 each => effective 12, so longer raw but traffic-aware prefers alternative. Must select alternative.
- Factor <1 means faster (e.g., no congestion, carpool lane): effective < raw, traffic_delay negative allowed.
- Batch order preserved, single vs batch consistent.
- Help keywords: graph, from, to, requests, traffic, help.
- Validation expanded:
  - graph invalid → exit 2 (same as Turn1)
  - traffic file invalid JSON / missing / referenced edge not in graph / nodes missing / factor <=0 / self-loop traffic → exit 2
  - requests file invalid → exit 2
  - Unknown flags → exit 2 (or handle, but no stdout)
- Stdlib only, build via `go build -o router .`
- Performance: 500 nodes, 2000 edges, 100 requests with traffic <2 sec.
- Floating tolerance: effective sum may have floating errors; tests compare with abs diff <1e-6.

### Examples

```bash
go build -o router .
./router --graph graph.json --from A --to C --traffic traffic.json
# traffic.json: {"traffic":[{"from":"A","to":"C","factor":10},{"from":"A","to":"B","factor":1},{"from":"B","to":"C","factor":1}]}
# Graph: A-B 6, B-C 6, A-C 5
# Raw shortest is A-C distance 5, but effective A-C=50, A-B-C effective=12, so selects A-B-C
# Output: {"path":["A","B","C"],"distance":12,"effective_distance":12,"traffic_delay":0}

./router --graph graph.json --requests req.json --traffic traffic.json
# Per line includes effective fields
```

### Success Criteria – Hard

- Turn1 cases still pass when traffic not supplied
- Single and batch modes both work with traffic
- Traffic-aware selection picks minimal effective distance, not raw
- Lexicographic tie-break enforced for equal effective distance
- Validation: negative/zero factor, missing edge, self-loop traffic, duplicate handling, file not found → exit 2
- Help contains all 6 keywords, bare no args help exit 0
- Output JSON valid, correct fields, floating tolerance handled
- Stdlib only, builds via go build

Implement at `/app` – Turn2 extends Turn1.
