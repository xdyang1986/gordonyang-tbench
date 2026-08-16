# Turn 2: Traffic-Aware Routing – Effective Distance (GIGA HARD EXTRA)

Turn1 built raw-distance routing (physical road length) minimizing sum raw. You must extend same binary with traffic-aware routing where best route minimizes **effective distance** – routing contract is `effective = raw*factor + delay` **per edge**, `traffic_delay = effective - raw` (sum of (factor-1)*raw + delay). Turn1 code present via inherit; you must layer onto it (preserve M1 raw routing, add traffic factor+delay manifest). Turn1 must still work without traffic (help must now include traffic keyword, flag set expanded).

## CLI – effective routing contract GIGA HARD

```
router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>]
router --graph <PATH> --requests <PATH> [--traffic <PATH>]
router --help | -h | help | (no args) -> help containing graph, from, to, requests, traffic, help exit 0
```

- `--graph` required road network manifest (same validation as Turn1 GIGA HARD).
- `--from`/`--to` origin/dest single; fallback `--source`/`--destination`.
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--traffic` optional traffic factor+delay manifest. When present, routing minimizes effective distance, reports raw+effective+delay. When absent, raw-only behavior identical to Turn1.
- `--help`, `-h`, positional `help` prints help stdout containing `graph, from, to, requests, traffic, help` (6 keywords) and exits 0. **Help precedence:** any help token anywhere (`--help`, `-h`, `help`, `--help=true`, `--help= true`? equals form `--help=true` also help, `-h=true`) → help wins even with unknown/missing flags or invalid graph/traffic. `help` positional anywhere. Turn2 help MUST contain `traffic`.
- Bare no args → help 0. Unknown flag (`-x`, `--unknown`, `--unknown=foo`, `--unknown=`, `--trafficish`) or missing required or flag with missing value (`--graph` alone, `--traffic` alone) → exit 2 no stdout unless help present.
- Flags accept `--flag value` and `--flag=value`, order independent (e.g., `--from A --graph g.json --to B --traffic t.json` and `--traffic=t.json --graph=g.json --from=A --to=B` both valid). Equals syntax for all flags.
- Flag sets non-cumulative: Turn1 had no traffic. Turn2 adds traffic but still must support raw-only.

## Road network manifest – raw (inherits Turn1 GIGA HARD, still enforced with/without traffic)

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}]}
```

- `nodes`: required array **strings only** (number/null/bool/object/array → invalid). At least 1, empty array invalid. Whitespace-only/empty → invalid. Location IDs case-sensitive, may contain `-_./` and slash: `A-1`, `A_2`, `A.3`, `A/B` distinct valid. Unique exact match including leading/trailing spaces: `" A"` vs `"A"` distinct valid, not duplicate. Extra top-level fields ignored.
- `edges`: required array **objects only** (null/string/number/array like `[1,2,3]` or `["A","B",1]` → invalid). Each leg: `from` string required, `to` string required, `distance` number >0 required.
  - `from`/`to` must be string, presence required, empty/whitespace-only → invalid, `from==to` exact → self-loop invalid, must exist exactly in nodes **no trim** (`" A"` ≠ `"A"` → missing → invalid).
  - `distance`: JSON number >0 finite, int/float/scientific (`2.5`, `1e3`, `1e-3`, `1e+3`, `1E+3`, `2.5e+2`, `1e+2`, `1E+2`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` invalid JSON (explicit plus). Both `1e+3` and `1E+3` valid – many hand parsers reject plus in exponent.
  - Extra nested fields inside leg ignored (`meta: {x:1}`).
- File: top-level must be object with `nodes`+`edges`; otherwise invalid (array/string/number/null → invalid). `nodes`/`edges` missing or not array → invalid. Invalid JSON trailing comma `{"edges":[...,]}`, comments `//` or `/* */`, BOM `\xEF\xBB\xBF`, unreadable, not found → exit 2 no crash stdout empty. Duplicate exact nodes invalid. Network undirected: leg A-B both ways, duplicate same unordered pair including reverse B-A → keep smallest raw. Traffic applies to that min kept edge.

Any violation → exit 2 no stdout irrespective of traffic flag.

## Batch routing requests – raw (inherits Turn1 GIGA HARD, still enforced with traffic)

```json
[{"source":"A","destination":"C"}, {"from":"B","to":"D"}]
```

- File must be JSON array (object/string/trailing comma `[{...},]` → invalid, BOM/comment trailing comma → exit2, not crash).
- Each element must be object (null/number/string/array like `[1,2,3]` → invalid whole file exit2).
- Each object needs `source`/`destination` or `from`/`to`; prefer `source`/`destination` if both present. Extra and nested fields ignored.
  - Missing key entirely → invalid whole file exit 2 (e.g., `[{"source":"A"}]` missing dest, `{"from":"A"}` missing to, `{}` missing both).
  - Value must be string (number/null/bool/object/array → invalid). Raw JSON null literal `{"source":null}` → invalid (Go collapses null to "" – must check `RawMessage` == "null"). Same for `destination`, `from`, `to`.
  - Empty `""` or whitespace-only `"   "` present → **no route** not invalid: output `[]`, `-1` (with traffic `-1,-1,-1`), exit 1 if any, batch continues order preserved.
  - Order preserved (200 random must match). Empty array `[]` valid exit 0 no lines.
- Non-existing location with non-empty value → no route including `" A"` leading space distinct from `"A"`. Single mode: empty/whitespace `--from`/`--to` invalid exit 2 (distinct from batch no-route); leading space `" A"` in single → no-route exit1 (if `" A"` not a node) vs invalid if node exists but whitespace-only? Whitespace-only always invalid graph but request empty/whitespace is no-route.
- Output strictness for requests still: echo source/dest exact including empty string, special chars.
- Batch order preserved, same-source amortization needed.

Any violation → exit2 no stdout.

## Traffic factor+delay manifest – effective routing (GIGA HARD EXTRA)

Forms (both accepted):

1. Object-wrapped: `{"traffic":[...],"extra":"ignore","version":1}`
2. Direct array: `[{"from":"A","to":"B","factor":1.5,"delay":3}]`

Validation GIGA HARD (must not crash on malformed JSON, must exit2 no stdout):

- **Top-level:** Must be object containing `traffic` array OR direct array. If object: top-level must be JSON object; `traffic` key must exist and be array; object/string/number/null (`{"traffic":null}`, `{"traffic":{}}`, `{"traffic":"x"}`, `{"traffic":5}`) → invalid. Missing key `{"foo":[]}` invalid. If direct: top-level must be JSON array; object/string/number/null → invalid. Empty `[]` and `{"traffic":[]}` valid empty → default factor 1.0 delay 0 for all edges.
- **Wrapper extra fields:** Extra top-level fields like `version`, `extra`, etc. ignored.
- **Invalid JSON:** trailing comma `{"traffic":[...],}` or `[{...},]` → invalid exit2 no crash. `//` comment or `/* */` comment → invalid exit2 no crash. BOM `\xEF\xBB\xBF` at start → invalid exit2 no crash (json.Unmarshal fails, must not panic). File not found, unreadable → exit2.
- **Array elements:** Each element must be object (null/string/number/array like `[1,2,3]` or `["A","B",1]` → invalid whole file). Empty object `{}` invalid (missing from/to/factor). `{"foo":[]}` invalid. Raw `null` literal element invalid.
- **Each entry required fields:** `from` string required, `to` string required, `factor` number >0 required. `delay` optional >=0 default 0.
  - **from/to:** must be string (number/null/bool/object/array → invalid), non-empty after TrimSpace? No, whitespace-only invalid, empty invalid, leading/trailing spaces **exact no trim**: `" A"` vs `"A"` distinct, but if graph does not contain `" A"` → edge not found → invalid (since traffic references non-existing node/edge). Self-loop `from==to` exact → invalid. Must exist exactly in nodes **no trim**. Must correspond to existing undirected road leg: if nodes exist but leg A-B not in graph edges → invalid. Graph duplicate legs keep min raw; traffic applies to that min, but if edge missing entirely → invalid.
  - **factor:** JSON number >0 finite, int/float/scientific (`1`, `2.5`, `1e3`, `1e-3`, `1e+3`, `1E+3`, `2.5e+2`, `1e+2`, `1e-2`, `1E+2`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` invalid JSON (explicit plus sign not at exponent). `NaN`, `Infinity`, `-Infinity` invalid JSON → invalid. Plus in exponent `1e+2`, `1E+3` valid – critical: many hand-rolled parsers reject plus. Very small `1e-9` valid, very large `1e9` valid. Integer valid.
  - **delay:** optional, default 0. If present: JSON number >=0 finite, int/float/scientific (`1e2`, `1e+2`, `1E+3`, `2.5`, `0`, `0.0`). Negative, `-0`? `-0` ==0 so valid as >=0? But factor `-0` is <=0 → invalid. For delay, `d <0` invalid, so `-0` not <0 valid as 0. Missing → default 0. Null, string, bool, object/array → invalid. `+5` invalid JSON, `NaN` invalid.
  - **Extra nested fields:** Inside entry extra fields like `extra`, `weight`, `meta` ignored (except factor/delay).
- **Duplicate handling (critical GIGA HARD):** Same unordered pair including reverse B-A → last wins factor+delay **including delay reset**. Examples:
  - `A-B factor2 delay5` then `A-B factor3` (no delay) → effective must be `raw*3+0` not `raw*3+5` (delay reset to 0).
  - `A-B factor2` then `B-A factor3 delay10` → last wins `factor3 delay10` applies undirected.
  - Interleaved duplicates: 10 duplicates, last wins.
  - Many agents forget delay reset or forget reverse direction.
- **Missing entry:** If traffic does not contain entry for a graph edge → default factor 1.0 delay 0 (no traffic effect).
- **Exact match semantics:** No auto-trim for traffic from/to. `" A"` with leading space is not trimmed; validation checks exact node set and exact edge existence – if `graph` nodes contain `" A"` distinct, then `" A"`→`"B"` could be valid, but if graph only contains `"A"` (no leading space), traffic `" A"`→`"B"` → invalid because node `" A"` not in nodeSet → exit2. This is different from requests where `" A"` is no-route, not invalid. Agents often trim incorrectly.
- **Case-sensitive:** Node IDs case-sensitive: `A` vs `a` distinct.

Any violation → exit2 no stdout, no crash, even when graph valid.

## Routing contract – effective + tie-break cascade GIGA HARD EXTRA

- **Effective leg = `raw * factor + delay`** (strict per edge, not `(raw+delay)*factor`). Path effective = sum over edges of effective leg. Path raw = sum of raw distances along **effective-best** path (not raw-best). `traffic_delay = effective - raw` (includes factor-1 contribution plus delay). Factor <1 → faster lane, negative `traffic_delay` allowed (effective < raw). Effective never negative because raw>0, factor>0, delay>=0, but traffic_delay can be negative.
- **Minimize effective** via Dijkstra-like minimizing sum effective. Must handle float, scientific, factor <1.
- **Source == dest exact match →** path `[source]`, raw 0, effective 0, traffic_delay 0 even if isolated node, traffic irrelevant. With traffic flag: 4 keys, all 0. Without traffic: 2 keys raw 0.
- **No route:** disconnected graph, non-existing location, empty/whitespace source/dest in batch, leading-space `" A"` distinct no-route when node `" A"` not present. Output with traffic: `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1. Without traffic: `{"path":[],"distance":-1}` exit1. Batch: some no-route → exit1 with N lines.
- **Tie-break cascade 3-level GIGA HARD:**
  1. Total **effective** equal within 1e-9 → smaller **raw** wins.
  2. Raw equal within 1e-9 → lexicographically smallest route.
     - Element ASCII case-sensitive: `'-'45 < '.'46 < '_'95`, `'A'65<'a'97`, `B<C`, `A10<A2` because `'1'<'2'` string compare not numeric.
     - Prefix shorter wins (e.g., `["A","B"]` < `["A","B","C"]`).
     - Deterministic – sort neighbor nodes ASCII, priority queue ordered by (effective, raw, path lex).
     - Deeper: diamond of diamonds where first diff at depth 2 (A-B1-B2-Z vs A-B1-C2-Z vs A-C1-B2-Z vs A-C1-C2-Z cost 3 effective equal → B1<C1 and B2<C2 → A-B1-B2-Z). 5-way tie S-B..F-T must pick B, 10-way B..K must pick B.
     - Special chars: `A-1` vs `A_2` vs `A.B` – '-' < '.' < '_' ASCII.
     - Secondary raw test: effective equal 12, raw differs A-B-D raw 11 vs A-C-D raw 4 → pick A-C-D raw smaller even though B<C lex would favor A-B-D. Many agents only do effective→lex missing raw level.
     - Effective tie due to factor/delay: A-B-D factor 1 delay 1 effective 11 raw 10 vs A-C-D factor 2 delay 0 effective 8? Actually need equal effective different raw – construct with factor and delay.
- **Reroute:** Traffic must cause reroute away from raw-shortest:
  - Raw-short path 2 edges distance 1 each total raw 2 but factor 100 each effective 200 vs longer raw 20 effective 20 → pick longer raw 20.
  - Delay-only reroute: factor 1 all, but short path has delay 100 per edge vs longer 0 → must reroute to longer.
- **Float tolerance:** Tie within 1e-9 effective, 1e-9 raw, output tolerance 1e-6 for effective_distance, traffic_delay, distance.
- **Batch order preserved,** same-source amortization (cache per origin) required: 200 same-source should be << 200 distinct origins (perf catch). 500 same ≤25% of 500 distinct.

## Output – effective routing contract strict GIGA HARD EXTRA

- **Single no traffic:** exactly `{"path":[...],"distance":8}` – no `effective_distance`, `traffic_delay`, `source`/`destination`. Distance number not string, path elements strings. Exactly 2 keys.
- **Single with traffic:** exactly `{"path":[...],"distance":10,"effective_distance":15.5,"traffic_delay":5.5}` – 4 keys when found, distance number not string, path elements strings, `traffic_delay = effective - raw` within 1e-6. Raw = sum raw along effective-best path (critical). Effective = sum raw*factor+delay. Single with traffic no route exactly 4 keys with -1.
- **No route with traffic:** exactly `{"path":[],"distance":-1,"effective_distance":-1,"traffic_delay":-1}` exit1. Without traffic: `{"path":[],"distance":-1}`.
- **Batch no traffic:** exactly `source,destination,path,distance` 4 keys per line, echo source/dest exact including empty (but empty → no-route), order preserved.
- **Batch with traffic:** exactly 6 keys `source,destination,path,distance,effective_distance,traffic_delay` per line, echo exact, order preserved, -1 for no-route all three distance fields.
- **Invalid:** no stdout (empty) exit2 irrespective of traffic/graph/requests invalid. No partial output. No crash on BOM/trailing comma/comment.
- **Exit codes:** 0 all routed success, 1 some no-route (at least one -1), 2 invalid input/flag/file error.
- **Distance types:** JSON numbers not strings. Path elements strings. Keys exactly as specified, no extra.

## Requests – missing vs empty distinct (extra hard)

`{"source":"A"}` missing dest → invalid exit2 vs `{"source":"","destination":"B"}` empty → no-route exit1 with -1 fields (6 keys if traffic). Use RawMessage null check: `{"source":null}` invalid vs `""` empty no-route. Same for traffic wrapper null vs empty: `{"traffic":null}` invalid vs `{"traffic":[]}` or `[]` valid empty. Factor/delay null → invalid.

## Performance – effective (GIGA HARD EXTRA)

- 500 locations 2000 legs 100 req with traffic <2.5s, 200 req <3s
- 1000 locations line with traffic <3s, **2000 nodes line <3.5s, 5000 line <5.5s, 10000 line <8s**
- 100 batch traffic relative <=25*base+1 (catch per-request re-parse): batch 100 must not be ~100× single request time (re-parse traffic/graph each request).
- **2000 batch with traffic <7.5s, 5000 batch <12s**
- Dense 100 locations 5000 edges with traffic <2s, 200 nodes dense <3s
- Same-source amortization **same 500 ≤25% multi 500 distinct** (cache per origin) – if you run Dijkstra per request without caching per source, 500 same-source will be similar to 500 distinct and fail relative bound; need caching.
- Catches O(n²) Dijkstra, per-request re-parse of graph/traffic, non-amortized.

Tie pressure: **10-way effective tie** B..K each 5+5 must pick B lex smallest; secondary raw tie with delay; deeper diamond effective equal; **10-way secondary raw tie** (effective equal raw equal lex). **Formula discrimination:** multi-edge sum must be per-edge `raw*factor+delay` not `(raw+delay)*factor` – checked via 2 edges where sums differ.

## Constraints – GIGA HARD EXTRA

Stdlib only (`go list` no dotted imports, `go.mod` no external require), `go build -o router .`, binary `/app/router`, help 6 keywords including traffic, help with extra invalid flags still help (help precedence), flag `--traffic=path` equals syntax and order independence, effective formula must be `raw*factor+delay` not `(raw+delay)*factor` – checked via multi-edge sum, delay reset on duplicate last-wins (second without delay → delay 0), reverse duplicate last-wins, 3-level cascade effective→raw→lex, tolerance 1e-9 tie 1e-6 output, flag sets evolve (Turn1 traffic unknown). Traffic wrapper null vs empty distinct, direct array empty valid, BOM/trailing comma/comment must not crash, leading/trailing spaces exact match no trim – `" A"` distinct, case-sensitive ASCII ordering, prefix shorter wins.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C --traffic traffic.json
./router --graph=network.json --from=A --to=B --traffic=traffic.json  # 1e+3, 1e+2, 1E+3 valid plus in exponent
./router --graph network.json --requests routes.json --traffic traffic.json
./router --from A --graph network.json --to B --traffic traffic.json  # order independent
./router --help --unknown   # help precedence still contains traffic
./router help
./router --graph network.json --requests [] --traffic []  # empty batch + empty traffic direct array valid
./router --graph network.json --requests []  # no traffic still valid raw-only
```

Turn2 must still pass all Turn1 extra-hard validations when traffic absent.
