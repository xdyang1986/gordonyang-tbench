# Turn 2: Traffic-Aware Routing (Go) – Step 2

Extend Turn1 router to incorporate traffic: effective = distance*factor+delay, minimize sum effective, report raw and effective and delay.

Binary: `/app/router` via `go build -o router .` from `/app`. Stdlib only – no external require, no dotted imports (tested). Turn1 must still work without --traffic.

### CLI

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
--help | -h | help | no args → help containing graph, from, to, requests, traffic, help, exit 0
```

- `--traffic` optional. Effective formula strict `distance*factor+delay`.
- Accept both `--flag value` and `--flag=value` for all flags.
- Order does not matter. Unknown flag (`--foobar`, `--foobar=xxx`) → exit2.
- Invalid traffic file (unreadable, not found, invalid JSON) → exit2, must not crash (BOM, `//` comments, trailing comma are invalid JSON but must not panic).
- Flag sets are non-cumulative: Turn1 has no `--traffic` (exit2), Turn2 adds it. Turn1-only test.

### Traffic JSON

Forms:
1. `{"traffic":[...]}`
2. `[{"from":"A","to":"B","factor":1.5}]`

- Wrapper: `traffic` key must be array. `null`, object, string, number → invalid. Null vs empty distinct: `{"traffic":null}` invalid, `{"traffic":[]}` and `[]` valid empty (factor 1.0, delay 0.0).
- Direct array elements must be objects with `from,to,factor`; `[1,2,3]`, missing factor → invalid. Object without `traffic` key and not direct array (`{}`, `{"foo":[]}`) → invalid.
- Entry: `from,to` non-empty, whitespace-only invalid, must exist in graph and edge must exist undirected. Leading/trailing spaces not trimmed: `" A"` ≠ `"A"` → invalid. `from==to` after trim → invalid.
- Duplicate graph edges keep min distance; traffic applies to min.
- `factor` required >0 int/float/scientific (`1e-2`). ≤0, missing, NaN, Inf, not number → exit2. JSON forbids `+5`, `NaN`, `Infinity` → invalid. `-0` invalid.
- `delay` optional ≥0 default 0. <0 invalid. Last duplicate wins, missing delay in last resets to 0.
- Duplicate same unordered pair (including reverse) → last wins.
- Missing entry → default factor 1.0 delay 0.0. Extra fields ignored.

### Routing

- Effective edge = `distance*factor+delay`, path effective = sum, raw = sum distance on chosen path.
- `traffic_delay = effective - raw`.
- Dijkstra minimizing effective. Source==dest → path [src] 0. No path → `[], -1, -1, -1` exit1.
- **3-level tie:** effective tie 1e-9 → smaller raw → lex smallest path (element case-sensitive). Includes reroute case where raw-shortest differs from effective-optimal – must minimize effective not raw.
- Float tolerance 1e-6 output, 1e-9 tie, support `1e-9` to `1e12`, factor<1 negative delay allowed.

### Output

Single with traffic: `{"path":["A","C"],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}`
No path: `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1
Batch: one JSON line per request, same fields, order preserved, exit 0 all routed, 1 some no-route, 2 invalid
Without traffic same as Turn1.

### Rules

- Turn1 still works.
- Traffic may reroute (effective-optimal ≠ raw-optimal) – tested.
- Delay accumulates per edge, not once per path – tested.
- Batch order preserved, help 6 keywords.
- Graph invalid → exit2. Traffic invalid → exit2 (null invalid vs [] valid, leading/trailing spaces invalid, missing key invalid vs empty no-route).
- Requests: missing key → invalid exit2, empty string present → no-route exit1 (must be distinguished).
- Unknown flags exit2, stdlib only tested, performance relative bounds `100 <=25*base+1`, `2000 <=100*base+3`, `5000 <=200*base+5`, same-source amortization `t_same <=0.35*t_multi` for 500 nodes traffic on every edge 200 req – catches per-request Dijkstra.
