# Turn 1: Logistics Route Planning – Raw Distance Routing (GIGA HARD)

Logistics platform picks best route by physical road length (raw). You build phase-1 router: raw-distance only. Phase-2 adds traffic multipliers: effective = raw*factor+delay.

Implement Go CLI at `/app`, module `router`, stdlib only. Binary `/app/router` via `go build -o router .`.

## CLI – routing contract

```
router --graph <PATH> --from <NODE> --to <NODE>
router --graph <PATH> --requests <PATH>
router --help | -h | help
router (no args) -> help
```

- `--graph` required, road network manifest.
- `--from`/`--to` origin/dest single; fallback `--source`/`--destination`.
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--help`, `-h`, positional `help` prints help stdout containing `graph, from, to, requests, help` and exits 0. **Help precedence:** any help token anywhere → help wins even with unknown/missing flags. Turn1 help **must NOT contain `traffic`** (only Turn2 adds it). Equals form `--help=true` also help.
- Bare no args → help 0. Unknown flag (`-x`, `--unknown`, `--unknown=foo`) or missing required or flag with missing value (`--graph` alone) → exit 2 no stdout.
- Flags accept `--flag value` and `--flag=value`, order independent.

## Road network manifest – raw

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}]}
```

- `nodes`: required array **strings only** (number/null/bool/object/array → invalid). At least 1, empty array invalid. Whitespace-only/empty → invalid. Location IDs case-sensitive, may contain `-_./` and slash: `A-1`, `A_2`, `A.3`, `A/B` distinct valid. Unique exact match including leading/trailing spaces: `" A"` vs `"A"` distinct valid, not duplicate. Extra top-level fields ignored.
- `edges`: required array **objects only** (null/string/number/array like `[1,2,3]` or `["A","B",1]` → invalid). Each leg: `from` string required, `to` string required, `distance` number >0 required.
  - `from`/`to` must be string, presence required, empty/whitespace-only → invalid, `from==to` exact → self-loop invalid, must exist exactly in nodes **no trim** (`" A"` ≠ `"A"` → missing → invalid).
  - `distance`: JSON number >0 finite, int/float/scientific (`2.5`, `1e3`, `1e-3`, `1e+3`, `1E+3`, `2.5e+2`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` invalid JSON (explicit plus). Both `1e+3` and `1E+3` valid – many hand parsers reject plus.
  - Extra nested fields inside leg ignored (`meta: {x:1}`).
- File: top-level must be object with `nodes`+`edges`; otherwise invalid. `nodes`/`edges` missing or not array → invalid. Invalid JSON trailing comma `{"edges":[...,]}`, comments `//`, BOM `\xEF\xBB\xBF`, unreadable → exit 2 no crash. Duplicate exact nodes invalid. Network undirected: leg A-B both ways, duplicate same unordered pair including reverse B-A → keep smallest raw.

Any violation → exit 2 no stdout.

## Batch routing requests – raw

```json
[{"source":"A","destination":"C"}, {"from":"B","to":"D"}]
```

- File must be JSON array (object/string/trailing comma `[{...},]` → invalid).
- Each element must be object (null/number/string/array → invalid whole file).
- Each object needs `source`/`destination` or `from`/`to`; prefer `source`/`destination` if both.
  - Missing key entirely → invalid whole file exit 2.
  - Value must be string (number/null/bool/object/array → invalid). `{}` missing both → invalid. Raw JSON null literal `{"source":null}` → invalid (Go collapses null to "" – check `RawMessage` == "null").
  - Empty `""` or whitespace-only `"   "` present → **no route** not invalid: output `[]`, `-1`, exit 1 if any, batch continues.
  - Extra and nested fields ignored.
- Order preserved (200 random must match). Empty array `[]` valid exit 0 no lines. All no-route → exit 1 with N lines each `[] -1`.
- Non-existing location with non-empty value → no route (includes `" A"` leading space distinct from `"A"`). Single mode: empty/whitespace `--from`/`--to` invalid exit 2 (distinct from batch no-route); leading space `" A"` in single → no-route exit1.

## Routing contract – raw distance + tie-break cascade

- Minimize sum raw road length (float, scientific plus). Source == destination exact → path `[source]`, distance 0 even if isolated.
- No route → `{"path":[],"distance":-1}` exit1.
- **Tie-break cascade:** total raw equal within 1e-9 → lexicographically smallest route:
  - Element ASCII case-sensitive: `'-'45<'.'46<'_'95`, `'A'65<'a'97`, `B<C`, `A10<A2` because `'1'<'2'`.
  - Prefix shorter wins. Deterministic – sort neighbor locations, priority queue ordered by (raw, path lex).
  - Deeper: diamond of diamonds where first diff at depth 2 (A-B1-B2-Z vs A-B1-C2-Z vs A-C1-B2-Z vs A-C1-C2-Z cost 3 → B1<C1 and B2<C2 → A-B1-B2-Z). 5-way tie S-B..F-T must pick B.

## Output – raw routing contract

- Single success strict: exactly `{"path":[...],"distance":8}` keys – no `effective_distance`, `traffic_delay`, `source`/`destination`. Distance number not string, path elements strings.
- Single no route strict: `{"path":[],"distance":-1}` exit1.
- Batch success strict: exactly `{"source":...,"destination":...,"path":[...],"distance":8}` – echo source/dest exact including empty.
- Batch no route same keys with `[] -1`.
- Invalid no stdout exit2. Exit 0 all routed, 1 some no-route, 2 invalid.

## Performance – raw

500 locations 2000 legs 100 req <2s, 200 req <3s; 1000 line <2.5s, 2000 <3.5s, **5000 line <4.5s**; 100 locations 500 batch <4s, **200 linear 2000 batch <6s**; dense 100 locations 5000 edges <2s; batch 100 relative not ~100× single (catch per-request re-parse). Go required.

## Constraints

Stdlib only, `go build -o router .`, binary `/app/router`, help 5 keywords no traffic in Turn1, flag sets evolve, tolerance 1e-9 tie.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C
./router --graph=network.json --from=A --to=C  # equals plus: 1e+3 valid
./router --from A --graph network.json --to B  # order independent
./router --help --unknown   # help precedence
./router help
./router --graph network.json --requests routes.json
./router --graph network.json --requests []  # empty batch
```
