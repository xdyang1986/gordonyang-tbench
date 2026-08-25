# Turn 2: Traffic-Aware Routing – Directed Traffic with Zone-Entry Toll (F1/F2)

Turn1 built raw-distance routing with survey-log last-wins semantics (unordered without traffic). You must extend same binary with traffic-aware routing where best route minimizes effective distance, but traffic is directional and delay is zone-entry toll charged at most once per route.

## CLI – effective routing contract

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) -> help containing graph, from, to, requests, traffic, help exit 0
```

- `--graph` required road network manifest (same validation as Turn1 including last-wins, duplicate-key, FFFD).
- `--from`/`--to` origin/dest single; fallback `--source`/`--destination` (both alias forms must be accepted with and without --traffic).
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--traffic` optional directed traffic manifest. When present, routing minimizes effective distance with zone tolls and raw becomes per-direction (F1). When absent, raw-only behavior identical to Turn1.
- `--help`, `-h`, positional `help` prints help stdout containing `graph, from, to, requests, traffic, help` and exits 0. Help precedence: any help token anywhere → help wins even with unknown/missing flags. Turn2 help MUST contain `traffic`.
- Bare no args → help 0. Unknown flag or missing required or flag with missing value → exit 2 no stdout unless help present.
- Flags accept `--flag value` and `--flag=value`, order independent.

## Road network manifest – raw (inherits Turn1 with F1 conditional)

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}]}
```

Same validation as Turn1 including survey-log override, duplicate-key detection, FFFD guard, superseded validation, and explicit top-level type matrix (number/null/bool/object/string invalid for nodes/edges top-level, elements typed), but **conditional on traffic**:

- **Without traffic (raw-only)**: `edges` is append-ordered survey log for undirected segments: later record authoritative for same unordered pair, even in reverse orientation (B-A superseding A-B). Only last per unordered pair survives both directions. Superseded records still validated – malformed earlier for same unordered pair that later re-surveys still invalidates whole manifest.
- **With traffic (F1)**: override resolves **per direction**. A traffic entry naming B->A revives raw record superseded in that direction. Concretely, per ordered pair last wins; if both orientations have records, raw becomes direction-dependent (A->B may be 10, B->A 2). If only one orientation ever appears, both directions share that raw to keep road undirected. This makes inherited step-1 code that assumes one raw per unordered pair actively wrong under traffic, and raw-along-effective-best becomes direction-dependent. The ~26 `test_raw_regression_*` no-traffic tests must stay bit-identical while traffic path diverges.
- Duplicate keys inside any edge object (e.g. `{"distance":5,"distance":5}`) → whole file invalid (exit2). Go's `Unmarshal` keeps last silently, need Token streaming.
- Node ID that decodes to U+FFFD (e.g. lone surrogate `"\uD800"` → Go decodes to replacement char) → invalid manifest exit2, not no-route. Applies to nodes and edge from/to.

Any violation → exit2 no stdout.

## Batch routing requests – raw (inherits Turn1)

```json
[{"source":"A","destination":"C"}, {"from":"B","to":"D"}]
```

Same validation as Turn1 plus:
- duplicate keys inside any request object → invalid exit2
- node ID containing U+FFFD → invalid exit2 (whole file, not no-route)
- mixing key families within one object is invalid: e.g. `{"source":"A","to":"B"}`, `{"from":"A","destination":"B"}`, or having both families `{"source":"A","destination":"C","from":"A","to":"B"}` → invalid exit2 (previous spec preferred, now rejected)
- otherwise: must be JSON array, each element object, needs source/destination or from/to from single family, missing key → invalid, value must be string (null literal invalid via RawMessage), empty/whitespace → no-route not invalid with `[] -1`, non-existing location → no-route, order preserved, empty valid.

Any violation → exit2.

## Traffic factor+delay manifest – directed zone tolls (F2 escalated)

Forms (both accepted):

1. Object-wrapped: `{"traffic":[...],"extra":"ignore","version":1}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5,"delay":3}]`

Directed semantics + zone definition (F1/F2):

- Traffic entries are directional.
- `factor` names a congestion zone; consecutive arcs carrying same factor are same zone, but toll is charged **at most once per route** (F2) – re-entering a zone already paid is free. So search state must remember paid-set. At most 4 distinct factor values per manifest (enforced invalid if more) so paid-set is bitmask ≤16 states per node (including default). Perf fixtures use at most 2 distinct factors, ≤3 per node expanded.
- `delay` is toll for **first entry** into zone, not per-arc. Arc contributes `raw*factor`, plus its delay only on first entry into its zone – i.e. on first arc that enters an unpaid zone, or when preceding arc had different factor and new zone unpaid. Two arcs share zone exactly when factors same number; arc with no traffic entry runs at factor 1, shares zone with explicit factor 1 entries. Zone equality exact (Float64bits) not epsilon.
- Per-route effective = sum over arcs `raw*factor` + sum of tolls for distinct zones entered (first entry each). `traffic_delay = effective - raw` where raw = sum raw along effective-best (direction-dependent per F1). Factor <1 → negative traffic_delay allowed.
- Example re-entering free: A-B f2 d5 (enter f2 pay5), B-C f1 d7 (enter f1 pay7), C-D f2 d5 (re-enter f2 already paid, free) → total tolls 12 not 17 per-arc, effective = raw*factor sum +12.
- Same zone once: A-B 10 f2 d5, B-C 10 f2 d5 → effective 45 not 50 per-arc.
- Boundary recharge: A-B f2 d5, B-C f2 d5, C-D f1 d3 → first f2 5, same zone free, third f1 3 → 58 total.

Validation:

- Top-level: Must be object containing `traffic` array OR direct array. If object: `traffic` key must exist and be array; `{"traffic":null}` etc → invalid. Missing key `{"foo":[]}` invalid. If direct: top-level must be JSON array. Empty `[]` and `{"traffic":[]}` valid → default factor 1 delay 0.
- Invalid JSON trailing comma, comment, BOM → invalid exit2.
- Duplicate keys: any object containing duplicate keys → invalid exit2. This includes traffic entry objects (e.g. `{"from":"A","to":"B","factor":2,"factor":2}`) and top-level wrapper object (e.g. `{"traffic":[...],"traffic":[...]}`) – same Go `Unmarshal` silent-last-wins trap as edges. Wrapper duplicate is invalid even if arrays identical.
- Distinct factor bound: at most 4 distinct factor values per manifest **counting only surviving ordered pairs after last-wins deduplication** (not all log records). I.e., if log contains 5 distinct factors but duplicate ordered pair overwrites one, surviving set may be 4 → valid. More than 4 distinct surviving factor values → invalid exit2 (keeps bitmask bounded). This pins the ambiguity: count surviving, not raw log, for fairness.
- Array elements: Each element must be object (null/string/number/array → invalid whole file). Empty object `{}` invalid. Raw null element invalid.
- Each entry required fields: `from` string required, `to` string required, `factor` >0 required, `delay` optional >=0 default 0.
  - `from`/`to`: must be string, non-empty TrimSpace, leading/trailing spaces exact distinct, must exist exactly in nodes, must correspond to existing undirected road leg (unordered pair must exist after per-unordered existence, even if per-direction fallback), must not contain U+FFFD → invalid. Self-loop invalid.
  - `factor`: >0 finite int/float/scientific, zero/negative/-0/missing/null/string/bool/object/array → invalid. `+5` invalid JSON.
  - `delay`: optional default 0, >=0 finite, negative/null/string/etc → invalid.
  - Extra fields ignored.
- Duplicate handling directed: same ordered pair exact → last wins factor+delay including delay reset to 0 when second missing delay. Reverse distinct. Superseded traffic records still validated – malformed earlier for same ordered pair that later re-surveys still invalidates.
- Missing entry for an arc → default factor 1.0 delay 0, zone = factor 1.

Any violation → exit2 no stdout.

## Routing contract – effective + tie-break (T5)

- Source == dest → path `[source]`, raw 0, effective 0, traffic_delay 0. With traffic: 4 keys all 0. Without: 2 keys 0.
- No route → with traffic `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1, without `{"path":[],"distance":-1}`.
- Tie-break cascade 4-level (T5):
  1. Total effective equal within 1e-9 → smaller raw wins.
  2. Raw equal within 1e-9 → fewer tolls paid (number of distinct zones entered, i.e. paid-set size) wins.
  3. Tolls equal → lexicographically smallest route ASCII case-sensitive: `A,B,D` < `A,C,D` because `B<C`; `'-' < '.' < '_'`, `'A'<'a'`, `A10 < A2` because `'1'<'2'`.
  - Deterministic. Toll count not monotone with effective, must ride in state alongside (node, zone, paid-set).

Example expensive arrival wins (node-level Dijkstra fails): graph A-B 10 f1 100, A-C 5 f2 0, C-B 5 f2 0, B-D 10 f1 100 plus reverse arcs pinned to f2 to block cheap cycle: cheaper arrival to B via C (20 zone f2) vs direct 110 zone f1, but B-D shares f1 so direct total 120 beats via C total 130 (costlier prefix wins). This is the difficulty of Change B + F2.

## Output – effective routing contract strict

- Single no traffic: exactly `{"path":[...],"distance":8}` 2 keys.
- Single with traffic: exactly `{"path":[...],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` 4 keys, -1 variant 4 keys.
- Batch no traffic: `source,destination,path,distance` 4 keys.
- Batch with traffic: `source,destination,path,distance,effective_distance,traffic_delay` 6 keys, -1 for all three distance fields on no-route.
- Invalid: no stdout exit2. Exit 0 all routed, 1 some no-route.

## Performance

500 locations 2000 legs 100 req with traffic <2.5s, 1000 line <3s, 2000 nodes <3.5s, 5000 <5.5s, 10000 <8s. Batch 100 relative <=25*base+1, 2000 batch <7.5s, 5000 <12s. Same-source amortization same 500 ≤25% multi 500 distinct. State expansion (node, zone, paidMask) bounded by 16*arc count with distinct ≤4, perf fixtures ≤2 distinct so ≤3 per node extra.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C --traffic traffic.json
./router --graph=network.json --from=A --to=B --traffic=traffic.json
./router --graph network.json --source A --destination C --traffic traffic.json
./router --graph network.json --requests routes.json --traffic traffic.json
./router --from A --graph network.json --to B --traffic traffic.json
./router --help
./router --graph network.json --requests [] --traffic []  # empty batch + empty traffic
```

Turn2 must still pass all Turn1 validations when traffic absent. F3: keep six inherited traps as regressions zero difficulty; new step2 budget is traffic-only.
