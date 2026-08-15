# Turn 2: Traffic-Aware Routing – Effective Distance (EXTRA HARD)

Turn1 built raw-distance routing. Extend same binary with traffic-aware routing where best route minimizes **effective distance**. Turn1 code present via inherit.

## CLI – effective routing contract

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) -> help containing graph, from, to, requests, traffic, help exit 0
```

- `--traffic` optional path to traffic factor manifest.
- Flags accept `--flag value` and `--flag=value`, order independent.
- Help precedence: any help token anywhere → help exit 0 even with unknown/invalid others.
- Unknown flag (including `--foobar=xxx`) → exit 2 no stdout unless help present.
- Invalid traffic file (not found, unreadable, trailing comma, `//` comment, BOM must not crash) → exit 2.
- Turn1 did not support traffic (exit 2 unknown); Turn2 adds it – flag sets non-cumulative by design.

## Traffic factor manifest – effective = distance*factor+delay

Forms:
1. Object-wrapped: `{"traffic":[...],"extra":"ignore"}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5,"delay":3}]`

Validation:
- Wrapper: `traffic` key must be array; if object/string/number/**null** → invalid. `{"traffic":null}` invalid vs `{"traffic":[]}` or `[]` valid empty (no traffic → factor 1.0 delay 0).
- Direct array elements must be objects with `from,to,factor`; elements null/number/string/array → invalid; missing factor → invalid; object without `traffic` and not array (e.g. `{}`, `{"foo":[]}`) → invalid.
- Each entry: `from,to` non-empty, whitespace-only invalid, exact match **no trim** (`" A"` ≠ `"A"` → edge not found → invalid), nodes must exist and road leg must exist undirected (A-B same as B-A), `from==to` → invalid even with spaces.
- Graph duplicate legs: keep min raw; traffic applies to that min.
- `factor` required >0 int/float/scientific (`1e-2`, `0.5`). Zero, negative, `-0` (0) , missing, null, string, bool, object/array → invalid. `+5`, `NaN`, `Infinity` are invalid JSON → invalid.
- `delay` optional >=0 default 0 int/float/scientific; negative, string, bool, null → invalid. Duplicate same unordered pair (including reverse) → last wins factor+delay; second entry without delay resets delay to 0.
- Missing entry for leg → default factor 1.0 delay 0.
- Extra fields inside entry and top-level ignored.

## Routing contract – effective distance + tie-break cascade

- Effective leg = `raw*factor+delay` (strict, not `(raw+delay)*factor`). Path effective = sum effective legs. Raw = sum raw along chosen effective-best path.
- `traffic_delay` = `effective - raw` (includes factor-1 contribution). Factor <1 → faster lane, negative delay allowed.
- Minimize effective distance via Dijkstra-like routing. Same-source batch should be amortized (cache per origin).
- Source == dest exact → path `[src]`, raw 0, effective 0, traffic_delay 0.
- No route → `path:[], raw:-1, effective:-1, traffic_delay:-1` exit 1.
- **Tie-break cascade 3-level:** effective tie within 1e-9 → smaller raw → smaller lex route element-by-element ASCII case-sensitive (`'-'<'.'<'_'`, `'A'<'a'`), prefix shorter wins. Covers equal effective equal raw (B<C<E → A-B-D) and effective equal raw differs (raw 4 vs 11 → pick raw 4).
- Float tolerance 1e-6 output, 1e-9 tie detection, range `1e-9` to `1e12`.
- Turn1 still works without traffic flag. Traffic must change best route (direct short raw high factor vs longer raw low effective).

## Output – effective routing

Single no traffic: `{"path":[...],"distance":8}` as Turn1
Single with traffic: `{"path":["A","C"],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` – three fields present when path found
No route with traffic: `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1
Invalid: no stdout exit2

Batch: one JSON line per request in order, same extended fields when traffic present, exit 0 all routed, 1 some no-route, 2 invalid. Batch order preserved. Non-existing location in batch → no route, not invalid. Empty/whitespace source present → no route in batch, invalid in single.

## Road network still validated (same as Turn1)

Whitespace-only node invalid, duplicate exact invalid, leading/trailing spaces distinct location IDs, exact match no trim (`" A"` ≠ `"A"`), nodes strings-only, legs objects-only, distance >0, undirected min including reverse, invalid JSON trailing comma/comment/BOM must not crash exit2.

## Requests – missing vs empty distinct

`{"source":"A"}` missing destination → invalid exit2, while `{"source":"","destination":"B"}` empty explicit → no-route exit1. Must use `*string` or map/RawMessage to distinguish. Same pattern for traffic null vs empty.

## Constraints

Stdlib only, `go.mod` no external require, `go build -o router .`, binary `/app/router`, help 6 keywords, 3-level tie cascade effective→raw→lex.

## Performance – effective routing

1000 locations 5000 legs 100 req <2s, 500 locations 200 req <2.5s, dense 100 locations 1000-5000 legs <1s, batch 100 traffic relative <=25*base+1, 2000 traffic <=100*base+3, 5000 traffic <=200*base+5, same-source amortization. Catches O(n²) and per-request re-parse.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C --traffic traffic.json
./router --graph=network.json --from=A --to=B --traffic=traffic.json
./router --graph network.json --requests routes.json --traffic traffic.json
```
