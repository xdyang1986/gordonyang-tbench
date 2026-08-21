# Turn 2: Traffic-Aware Routing – Directed Traffic

Turn1 built raw-distance routing: undirected edges collapsed to one min-raw entry per unordered pair, minimizing sum raw. You must extend same binary with traffic-aware routing where best route minimizes effective distance, but traffic is **directional** – this breaks the Step-1 undirected adjacency invariant.

Raw graph stays undirected. Traffic provides directed multipliers.

## CLI – effective routing contract

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) -> help containing graph, from, to, requests, traffic, help exit 0
```

- `--graph` required road network manifest (same validation as Turn1).
- `--from`/`--to` origin/dest single; fallback `--source`/`--destination`.
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--traffic` optional directed traffic manifest. When present, routing minimizes effective distance. When absent, raw-only behavior identical to Turn1.
- `--help`, `-h`, positional `help` prints help stdout containing `graph, from, to, requests, traffic, help` and exits 0. Help precedence: any help token anywhere → help wins even with unknown/missing flags. Turn2 help MUST contain `traffic`.
- Bare no args → help 0. Unknown flag or missing required or flag with missing value → exit 2 no stdout unless help present.
- Flags accept `--flag value` and `--flag=value`, order independent.

## Road network manifest – raw (inherits Turn1)

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}]}
```

Same validation as Turn1: nodes strings only, unique exact including leading/trailing spaces distinct, edges objects only, from/to must exist exactly in nodes no trim, distance >0 finite int/float/scientific (`1e3`, `1e-3`, `1e+3`, `1E+3`, `1e+2`), extra fields ignored, duplicate same unordered pair including reverse B-A → keep smallest raw, BOM/trailing comma/comment → exit2 no crash, etc.

Any violation → exit2 no stdout.

## Batch routing requests – raw (inherits Turn1)

```json
[{"source":"A","destination":"C"}, {"from":"B","to":"D"}]
```

Same validation: must be JSON array, each element object, needs source/destination or from/to, missing key entirely → invalid, value must be string (null literal invalid via RawMessage check), empty/whitespace → no-route not invalid with `[] -1`, non-existing location → no-route, order preserved, empty array valid.

Any violation → exit2.

## Traffic factor+delay manifest – directed

Forms (both accepted):

1. Object-wrapped: `{"traffic":[...],"extra":"ignore","version":1}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5,"delay":3}]`

Directed semantics:

- Traffic entries are directional.

Validation:

- Top-level: Must be object containing `traffic` array OR direct array. If object: `traffic` key must exist and be array; `{"traffic":null}`, `{"traffic":{}}`, `{"traffic":"x"}` → invalid. Missing key `{"foo":[]}` invalid. If direct: top-level must be JSON array. Empty `[]` and `{"traffic":[]}` valid empty → default factor 1 delay 0 for all arcs.
- Invalid JSON trailing comma, `//` comment, BOM → invalid exit2 no crash.
- Array elements: Each element must be object (null/string/number/array → invalid whole file). Empty object `{}` invalid. Raw null element invalid.
- Each entry required fields: `from` string required, `to` string required, `factor` number >0 required. `delay` optional >=0 default 0.
  - `from`/`to`: must be string, non-empty after TrimSpace? Empty/whitespace-only invalid, leading/trailing spaces exact no trim: `" A"` vs `"A"` distinct, must exist exactly in nodes, must correspond to existing undirected road leg (unordered pair must exist in graph edges after min-collapse). Self-loop `from==to` invalid.
  - `factor`: JSON number >0 finite int/float/scientific (`2.5`, `1e3`, `1e+3`, `1E+3`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` invalid JSON. `1e+2` valid.
  - `delay`: optional default 0, number >=0 finite scientific valid (`1e2`, `1e+2`). Negative, null, string, bool, object/array → invalid.
  - Extra fields ignored.
- Duplicate handling directed: same **ordered** pair exact (from==from && to==to) → last wins factor+delay including delay reset to 0 when second entry missing delay. Reverse B->A is distinct, does NOT overwrite A->B.
  - Example: `A->B f2 d5` then `A->B f3` (no delay) → effective `raw*3+0` reset.
  - `A->B f2` then `B->A f3 d10` → A->B stays f2, B->A f3 d10.
- Missing entry for an arc → default factor 1.0 delay 0.

Any violation → exit2 no stdout.

## Routing contract – effective + tie-break cascade

- Per directed arc: `effective = raw * factor + delay`, `traffic_delay = effective - raw` per arc sum. Path effective = sum over traversed directed arcs of effective. Path raw = sum raw distances along effective-best path (not raw-best). Factor <1 → faster lane, negative traffic_delay allowed.
- Minimize total effective via Dijkstra-like on directed arcs (undirected edges provide two directed arcs with possibly different weights).
- Source == dest → path `[source]`, raw 0, effective 0, traffic_delay 0. With traffic: 4 keys all 0. Without: 2 keys 0.
- No route → with traffic `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1, without `{"path":[],"distance":-1}`.

Worked example that discriminates grouping:

Graph: `A-B raw10, B-C raw10`. Traffic: `A->B factor2 delay5, B->C factor1 delay0`.
Effective best path A->C: `A->B` effective `10*2+5=25`, `B->C` effective `10*1+0=10`, total effective 35, raw along effective-best =20, traffic_delay=15.
If grouped as `(raw+delay)*factor`, first edge would be `(10+5)*2=30` plus second 10 total 40 – different numbers, only per-edge grouping is correct.

Directional worked example:

Graph: `A-B raw10`. Traffic: `A->B factor3, B->A factor1`.
Routing A->B: effective 30, raw 10, delay 20.
Routing B->A: effective 10, raw 10, delay 0. Same undirected edge, different effective per direction.

- Tie-break cascade 3-level:
  1. Total effective equal within 1e-9 → smaller raw wins.
  2. Raw equal within 1e-9 → lexicographically smallest route ASCII case-sensitive: `'-'45<'.'46<'_'95`, `'A'65<'a'97`, `B<C`, `A10<A2` (`'1'<'2'`), prefix shorter wins.
  - Deterministic – sort neighbor nodes ASCII, priority queue ordered by (effective, raw, path lex).

## Output – effective routing contract strict

- Single no traffic: exactly `{"path":[...],"distance":8}` 2 keys.
- Single with traffic: exactly `{"path":[...],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` 4 keys, -1 variant 4 keys.
- Batch no traffic: `source,destination,path,distance` 4 keys.
- Batch with traffic: `source,destination,path,distance,effective_distance,traffic_delay` 6 keys, -1 for all three distance fields on no-route.
- Invalid: no stdout exit2. Exit 0 all routed, 1 some no-route.

## Performance

500 locations 2000 legs 100 req with traffic <2.5s, 1000 line <3s, 2000 nodes <3.5s, 5000 <5.5s, 10000 <8s. Batch 100 relative <=25*base+1, 2000 batch <7.5s, 5000 <12s. Same-source amortization same 500 ≤25% multi 500 distinct.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C --traffic traffic.json
./router --graph=network.json --from=A --to=B --traffic=traffic.json
./router --graph network.json --requests routes.json --traffic traffic.json
./router --from A --graph network.json --to B --traffic traffic.json
./router --help
./router --graph network.json --requests [] --traffic []  # empty batch + empty traffic
```

Turn2 must still pass all Turn1 validations when traffic absent.
