# Turn 2: Traffic-Aware Routing – Directed Traffic with Zone-Entry Toll

Turn1 built raw-distance routing with survey-log last-wins semantics. You must extend same binary with traffic-aware routing where best route minimizes effective distance, but traffic is **directional** and the delay is a **zone-entry toll**.

Raw graph stays undirected with last-wins override per unordered pair (inherited from Turn1). Traffic provides directed multipliers and zone tolls.

## CLI – effective routing contract

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) -> help containing graph, from, to, requests, traffic, help exit 0
```

- `--graph` required road network manifest (same validation as Turn1 including last-wins).
- `--from`/`--to` origin/dest single; fallback `--source`/`--destination` (both alias forms must be accepted with and without --traffic).
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--traffic` optional directed traffic manifest. When present, routing minimizes effective distance with zone tolls. When absent, raw-only behavior identical to Turn1.
- `--help`, `-h`, positional `help` prints help stdout containing `graph, from, to, requests, traffic, help` and exits 0. Help precedence: any help token anywhere → help wins even with unknown/missing flags. Turn2 help MUST contain `traffic`.
- Bare no args → help 0. Unknown flag or missing required or flag with missing value → exit 2 no stdout unless help present.
- Flags accept `--flag value` and `--flag=value`, order independent.

## Road network manifest – raw (inherits Turn1 with last-wins)

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}]}
```

Same validation as Turn1 including survey-log override:

- `edges` is an append-ordered survey log. When the same unordered pair appears more than once — including reverse B-A — the later record replaces the earlier, longer or shorter, last-wins. Exactly one raw length per unordered pair: the last written.
- Example: A-B 3 then A-B 10 → raw 10 survives (not min).

Any violation → exit2 no stdout.

## Batch routing requests – raw (inherits Turn1)

```json
[{"source":"A","destination":"C"}, {"from":"B","to":"D"}]
```

Same validation: must be JSON array, each element object, needs source/destination or from/to, missing key entirely → invalid, value must be string (null literal invalid via RawMessage check), empty/whitespace → no-route not invalid with `[] -1`, non-existing location → no-route, order preserved, empty array valid.

Any violation → exit2.

## Traffic factor+delay manifest – directed zone tolls

Forms (both accepted):

1. Object-wrapped: `{"traffic":[...],"extra":"ignore","version":1}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5,"delay":3}]`

Directed semantics + zone definition:

- Traffic entries are directional.
- `factor` names a congestion zone; consecutive arcs carrying the same factor are the same zone.
- `delay` is the toll for **entering** the zone, not a per-arc cost. An arc contributes `raw * factor`, plus its delay **only when the route enters the zone on that arc** — i.e. on the route's first arc, or when the preceding arc carried a different factor. Two arcs share a zone exactly when their factors are the same number; an arc with no traffic entry runs at factor 1, so it shares a zone with any entry whose factor is 1.
- Zone equality is exact factor number equality, not epsilon fuzzy.
- Per-route effective = sum over arcs of `raw*factor` + sum of entry tolls (delay at each zone entry). `traffic_delay = effective - raw` where raw = sum raw along effective-best path. Factor <1 → faster lane, negative traffic_delay allowed because raw*factor can be less than raw.

Worked examples:

1) Grouping vs entry distinction (survives from before):
Graph: `A-B raw10, B-C raw10`. Traffic: `A->B factor2 delay5, B->C factor1 delay0`.
First arc enters its zone (origin always pays), effective `10*2+5=25`. Second arc enters new zone (factor 1 !=2), pays its delay 0, effective `10*1+0=10`. Total effective 35, raw 20, traffic_delay 15.
If grouped as `(raw+delay)*factor`, first edge would be `(10+5)*2=30` plus second 10 total 40 – different numbers, only per-entry `raw*factor+delay-at-entry` is correct. This example has different factors so entry vs per-arc is same.

2) Same zone – toll charged once:
Graph: `A-B raw10, B-C raw10`. Traffic: `A->B factor2 delay5, B->C factor2 delay5`.
Same factor → same zone. Route A->B->C: enter zone at A->B, pay delay 5 once. Effective = (10*2+5) + 10*2 = 45. Per-arc model would give 25+25=50. Only once is correct.

3) Zone boundary re-charges:
Graph: `A-B raw10, B-C raw10, C-D raw10`. Traffic: `A->B f2 d5, B->C f2 d5, C->D f1 d3`.
Path A->D: first entry f2 pays d5 at A->B (25), stays f2 at B->C no toll (20) total 45, then enters f1 at C->D pays d3 => 10*1+3=13 total 58. Re-charge at factor change.

4) Origin always pays:
First arc always counts as entering its zone, even if its factor is 1. Example: A->B f1 d7: effective 10*1+7=17 at origin.

5) Factor-1 sharing untolled zone:
Arc with no traffic entry runs at factor 1, sharing zone with explicit factor 1 entries. Route X->Y (no entry) f=1 zone, Y->Z explicit f=1 d=10 same zone → no re-charge at Y->Z if preceding was implicit 1. So X->Y->Z with both factor 1: if X->Y implicit (default 1, no delay) and Y->Z explicit f=1 d=10, origin X->Y cost 10*1+0 (if implicit delay 0) =10 (origin pays but delay 0), stays same zone so Y->Z costs 10*1+0=10 total 20, not 30. If the order were reversed (first arc explicit f=1 d=10, second implicit), first arc pays 10+10=20, second arc same zone 10 => total 30 but delay counted only at entry. Both illustrate sharing.

6) Node-level Dijkstra trap – costlier arrival can win:
Graph: A->B raw10 f1 d100, A->C raw1 f2 d0, C->B raw1 f2 d0, B->D f2 d0. Traffic: A->B f1 d100 (zone1), A->C f2 d0, C->B f2 d0, B->D f2 d0. Two ways to reach B:
- direct A->B effective 10*1+100=110 raw10 zone1
- via C: A->C 1*2=2 + C->B 1*2=2 (same zone f2, second no toll) =4 raw2 zone f2
Direct is more expensive to B (110 vs 4) but is in zone1? Actually direct zone is f1, via C zone f2. To go B->D f2, via C stays same zone (f2→f2) no extra toll, total via C: 4+2*1? Wait B->D f2 raw? Assume B->D raw10 f2: effective via C =4+20=24. Via direct A->B 110 then B->D 20? But B->D f2 zone different from direct's zone f1, so direct→B->D pays no? Actually B->D f2 after direct f1 zone change, so B->D entry toll 0, total 130. So via C still wins. Need a case where expensive arrival wins: make B->D share zone with direct's factor but not with cheap arrival's factor.
Example trap:
Nodes A,B,C,D.
Edges: A->B raw10, A->C raw5, C->B raw5, B->D raw10 all raw 10 except A-C 5 C-B 5.
Traffic:
A->B f1 d0 zone f1
A->C f2 d0 zone f2
C->B f2 d0 zone f2 (same as A->C)
B->D f1 d100 zone f1 toll 100
Now arrivals to B:
- direct A->B: eff 10 raw10 zone f1
- via C: A->C 5*2=10 + C->B 5*2=10 =20 raw10 zone f2
Direct cheaper eff 10 vs 20, but both raw 10 tie?. Actually direct 10 wins to B. But onward B->D f1 d100: if you arrived via direct f1, you stay same zone f1→f1, so no toll at B->D: cost 10*1=10 total 20. If via C f2→f1 zone change, you pay toll 100 at B->D: cost 10*1+100=110 total 130. So cheaper arrival to B (direct f1) wins overall despite being? In this variant cheaper arrival already wins. Need opposite: make direct expensive but in right zone.
Flip: A->B f1 d100 (110), A->C f2 d0 (10) C->B f2 d0 (10) total via C 20 zone f2, direct 110 zone f1. B->D f1 d0 zone f1: direct arrival stays f1 no toll => 110+10=120, via C arrival f2→f1 zone change but delay 0 => 20+10=30 still via C wins. Need toll on B->D f1 high and via C must pay it, direct not.
So make B->D f1 d100 zone f1: direct f1→f1 no toll? Actually if direct is f1, B->D f1 same zone, so direct avoids toll, via C pays toll. Let's compute: A->B f1 d100 =10+100=110 zone f1. A->C f2 10 + C->B f2 10 =20 zone f2. B->D f1 d100 zone f1.
Direct to D: 110 + 10 =120 (since same zone f1, no second toll)
Via C to D: 20 + (10+100)=130 (change f2→f1 pays toll)
So direct expensive arrival to B (110) beats cheap arrival (20) because it avoids the toll. This is the phenomenon: costlier prefix can beat cheap prefix due to zone entry. The oracle must search over (node, zone) states.

Validation:

- Top-level: Must be object containing `traffic` array OR direct array. If object: `traffic` key must exist and be array; `{"traffic":null}`, `{"traffic":{}}`, `{"traffic":"x"}` → invalid. Missing key `{"foo":[]}` invalid. If direct: top-level must be JSON array. Empty `[]` and `{"traffic":[]}` valid empty → default factor 1 delay 0 for all arcs.
- Invalid JSON trailing comma, `//` comment, BOM → invalid exit2 no crash.
- Array elements: Each element must be object (null/string/number/array → invalid whole file). Empty object `{}` invalid. Raw null element invalid.
- Each entry required fields: `from` string required, `to` string required, `factor` number >0 required. `delay` optional >=0 default 0.
  - `from`/`to`: must be string, non-empty after TrimSpace? Empty/whitespace-only invalid, leading/trailing spaces exact no trim: `" A"` vs `"A"` distinct, must exist exactly in nodes, must correspond to existing undirected road leg (unordered pair must exist in graph edges after last-wins collapse). Self-loop `from==to` invalid.
  - `factor`: JSON number >0 finite int/float/scientific (`2.5`, `1e3`, `1e+3`, `1E+3`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` invalid JSON. `1e+2` valid.
  - `delay`: optional default 0, number >=0 finite scientific valid (`1e2`, `1e+2`). Negative, null, string, bool, object/array → invalid.
  - Extra fields ignored.
- Duplicate handling directed: same **ordered** pair exact (from==from && to==to) → last wins factor+delay including delay reset to 0 when second entry missing delay. Reverse B->A is distinct, does NOT overwrite A->B.
  - Example: `A->B f2 d5` then `A->B f3` (no delay) → effective per entry: if that arc is first, `raw*3+0` reset.
  - `A->B f2` then `B->A f3 d10` → A->B stays f2, B->A f3 d10.
- Missing entry for an arc → default factor 1.0 delay 0, zone = factor 1.

Any violation → exit2 no stdout.

## Routing contract – effective + tie-break cascade

- Source == dest → path `[source]`, raw 0, effective 0, traffic_delay 0. With traffic: 4 keys all 0. Without: 2 keys 0.
- No route → with traffic `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1, without `{"path":[],"distance":-1}`.
- Tie-break cascade 3-level:
  1. Total effective equal within 1e-9 → smaller raw wins.
  2. Raw equal within 1e-9 → lexicographically smallest route ASCII case-sensitive with examples: `A,B,D` < `A,C,D` because `B<C`; ASCII order `'-' < '.' < '_'`, `'A'<'a'`, and `A10 < A2` because `'1'<'2'`.
  - Deterministic.

Directional worked example (asymmetric):

Graph: `A-B raw10`. Traffic: `A->B factor3, B->A factor1`.
Routing A->B: effective `raw*factor + toll_at_entry = 10*3+0=30` raw10 delay20.
Routing B->A: effective 10 raw10 delay0 (default factor 1 no toll or factor1 no delay). Same undirected edge, different effective per direction.

Zone sharing example: see f-1 sharing discussion above.

## Output – effective routing contract strict

- Single no traffic: exactly `{"path":[...],"distance":8}` 2 keys.
- Single with traffic: exactly `{"path":[...],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` 4 keys, -1 variant 4 keys.
- Batch no traffic: `source,destination,path,distance` 4 keys.
- Batch with traffic: `source,destination,path,distance,effective_distance,traffic_delay` 6 keys, -1 for all three distance fields on no-route.
- Invalid: no stdout exit2. Exit 0 all routed, 1 some no-route.

## Performance

500 locations 2000 legs 100 req with traffic <2.5s, 1000 line <3s, 2000 nodes <3.5s, 5000 <5.5s, 10000 <8s. Batch 100 relative <=25*base+1, 2000 batch <7.5s, 5000 <12s. Same-source amortization same 500 ≤25% multi 500 distinct. State expansion bounded by directed arc count; perf fixtures use at most two distinct factors so ≤3 per node.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C --traffic traffic.json
./router --graph=network.json --from=A --to=B --traffic=traffic.json
./router --graph=network.json --source A --destination C --traffic traffic.json
./router --graph network.json --requests routes.json --traffic traffic.json
./router --from A --graph network.json --to B --traffic traffic.json
./router --help
./router --graph network.json --requests [] --traffic []  # empty batch + empty traffic
```

Turn2 must still pass all Turn1 validations when traffic absent.
