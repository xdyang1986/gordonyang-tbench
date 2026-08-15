# Turn 2: Traffic-Aware Routing – Effective Distance (GIGA HARD)

Turn1 built raw-distance routing (physical road length). Extend same binary with traffic-aware routing where best route minimizes **effective distance** – routing contract is `effective = raw*factor + delay`, `traffic_delay = effective - raw`. Turn1 code present via inherit; you must layer onto it (preserve M1 raw routing, add traffic factor manifest). Turn1 must still work without traffic.

## CLI – effective routing contract

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) -> help containing graph, from, to, requests, traffic, help exit 0
```

- `--traffic` optional traffic factor manifest.
- Flags accept `--flag value` and `--flag=value`, order independent, help precedence any help token anywhere → help exit0 even with unknown.
- Unknown flag → exit2 no stdout unless help present.
- Invalid traffic file (not found, unreadable, trailing comma `{"traffic":[...],}`, `//` comment, BOM `\xEF\xBB\xBF` must not crash) → exit2. Direct array trailing comma `[{...},]` invalid.
- Flag sets non-cumulative: Turn1 no traffic.

## Traffic factor manifest – effective routing

Forms:
1. Object-wrapped: `{"traffic":[...],"extra":"ignore","version":1}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5,"delay":3}]`

Validation:
- Wrapper: `traffic` key must be array; object/string/number/**null** → invalid. `{"traffic":null}` invalid vs `{"traffic":[]}` or `[]` valid empty → default factor 1.0 delay 0.
- Direct array elements must be objects with `from,to,factor`; null/number/string/array like `[1,2,3]` → invalid; missing factor → invalid; `{}` or `{"foo":[]}` invalid.
- Each entry: `from,to` non-empty, whitespace-only invalid, **exact no trim** (`" A"` ≠ `"A"` → edge not found invalid), nodes must exist and road leg must exist undirected, `from==to` invalid.
- Graph duplicate legs keep min raw; traffic applies to that min.
- `factor` required >0 int/float/scientific (`1e-2`, `1e+2`, `1E+3`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5`, `NaN`, `Infinity` invalid JSON → invalid. Plus in exponent `1e+2` valid.
- `delay` optional >=0 default 0 int/float/scientific (`1e2`, `1e+2` valid). Negative, string, bool, null → invalid. Duplicate same unordered pair including reverse B-A → last wins factor+delay; second entry without delay resets delay 0.
- Missing entry → default factor 1.0 delay 0.
- Extra nested fields ignored.

## Routing contract – effective + tie-break cascade

- Effective leg = `raw*factor+delay` (strict, not `(raw+delay)*factor`). Path effective = sum effective. Raw = sum raw along effective-best path. `traffic_delay = effective-raw` (factor-1 contrib included). Factor <1 → faster lane negative delay allowed.
- Minimize effective via Dijkstra-like with same-source amortization (cache per origin – 200 same-source should be << 200 distinct).
- Source == dest → `[src]` raw 0 effective 0 delay 0. No route → `[], -1, -1, -1` exit1. Batch non-existing location or leading-space `" A"` distinct → no route.
- **Tie-break cascade 3-level:** effective tie 1e-9 → smaller raw → smaller lex route ASCII case-sensitive `'-'<'.'<'_'`, `'A'<'a'`, prefix shorter wins. Covers 3-way B<C<E, effective equal raw differs (raw 4 vs 11 pick 4), special chars `B-1` vs `B_2` '-' < '_' .
- Deeper: diamond of diamonds effective equal.
- Traffic must reroute away from raw-shortest (raw-short 2 high factor 100 → effective 200 vs longer raw 20 effective 20 → pick longer). Delay-only reroute: factor 1 delay 100 on short path vs 0 on longer must reroute.
- Batch order preserved, same-source amortized.

## Output – effective routing contract strict

- Single no traffic: exactly `{"path":[...],"distance":8}` – no effective/traffic_delay.
- Single with traffic: exactly `{"path":[...],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` – 4 keys when found, distance number not string.
- No route with traffic exactly `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1.
- Invalid no stdout exit2.
- Batch no traffic exactly `source,destination,path,distance` 4 keys; with traffic exactly 6 keys `source,destination,path,distance,effective_distance,traffic_delay`. Echo source/dest exact.
- Exit 0 all routed, 1 some no-route, 2 invalid.

## Road network still validated – raw contract

Same as Turn1: nodes strings-only, legs objects-only, whitespace-only invalid, duplicate exact invalid, leading/trailing spaces distinct, exact match no trim, distance >0, undirected min reverse, invalid JSON trailing comma/comment/BOM must not crash, file not found exit2.

## Requests – missing vs empty distinct

`{"source":"A"}` missing dest → invalid exit2 vs `{"source":"","destination":"B"}` empty → no-route exit1. Use RawMessage == "null" to distinguish. Same for traffic null vs empty.

## Performance – effective

1000 locations line with traffic <3s, **5000 line <5.5s**; 100 batch traffic relative <=25*base+1, 2000 batch traffic <7.5s, 500 distinct sources, dense <1s, same-source amortization <=35% multi-source. Catches O(n²) and per-request re-parse.

## Constraints

Stdlib only, `go build -o router .`, binary `/app/router`, help 6 keywords, 3-level cascade effective→raw→lex, tolerance 1e-9 tie 1e-6 output, flag evolves.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C --traffic traffic.json
./router --graph=network.json --from=A --to=B --traffic=traffic.json  # 1e+2 valid
./router --graph network.json --requests routes.json --traffic traffic.json
```
