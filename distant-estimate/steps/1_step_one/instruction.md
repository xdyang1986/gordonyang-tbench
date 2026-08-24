# Turn 1: Logistics Route Planning – Raw Distance Routing

Logistics platform picks best route by physical road length (raw). You build phase-1 router: raw-distance only. Phase-2 will add traffic multipliers with directed semantics that breaks the undirected adjacency assumption established here.

Implement Go CLI at `/app`, module `router`, stdlib only. Binary `/app/router` via `go build -o router .`.

## CLI – routing contract

```
router --graph <PATH> --from <NODE> --to <NODE>
router --graph <PATH> --requests <PATH>
router --help | -h | help
router (no args) -> help
```

- `--graph` required, road network manifest.
- `--from`/`--to` origin/dest single; fallback `--source`/`--destination` (both documented aliases must be accepted).
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--help`, `-h`, positional `help` prints help stdout containing `graph, from, to, requests, help` and exits 0. Help precedence: any help token anywhere → help wins even with unknown/missing flags. Turn1 help must NOT contain `traffic` (only Turn2 adds it). Equals form `--help=true`, `--help=1`, `-h=true` also help.
- Bare no args → help 0. Unknown flag (`-x`, `--unknown`, `--unknown=foo`) or missing required or flag with missing value (`--graph` alone) → exit 2 no stdout.
- Flags accept `--flag value` and `--flag=value`, order independent.

## Road network manifest – raw

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}]}
```

- `nodes`: required array strings only (number/null/bool/object/array → invalid). At least 1, empty array invalid. Whitespace-only/empty → invalid. Location IDs case-sensitive, may contain `-_./` and slash: `A-1`, `A_2`, `A.3`, `A/B` distinct valid. Unique exact match including leading/trailing spaces: `" A"` vs `"A"` distinct valid, not duplicate. Extra top-level fields ignored.
- `edges`: required array objects only (null/string/number/array like `[1,2,3]` or `["A","B",1]` → invalid). Each leg: `from` string required, `to` string required, `distance` number >0 required.
  - `from`/`to` must be string, presence required, empty/whitespace-only → invalid, `from==to` exact → self-loop invalid, must exist exactly in nodes no trim (`" A"` ≠ `"A"` → missing → invalid).
  - `distance`: JSON number >0 finite, int/float/scientific (`2.5`, `1e3`, `1e-3`, `1e+3`, `1E+3`, `2.5e+2`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` invalid JSON (explicit plus).
  - Extra nested fields inside leg ignored.
- File: top-level must be object with `nodes`+`edges`; otherwise invalid. `nodes`/`edges` missing or not array → invalid. Invalid JSON trailing comma `{"edges":[...,]}`, comments `//`, BOM `\xEF\xBB\xBF`, unreadable → exit 2 no crash. Duplicate exact nodes invalid.
- Survey log: `edges` is an append-ordered survey log. If the log contains multiple records for the same undirected road segment, the later record is authoritative – the earlier measurements are superseded by the later entry, even when later entry is written in reverse orientation (B-A superseding A-B). Only the last record per unordered pair survives. Superseded records are still validated – a malformed earlier record for a pair that a later valid record re-surveys still invalidates the whole manifest.
  - Example: log `[A->B distance 10, B->A distance 2]` → that segment travels at distance 2 in both directions.

- Duplicate keys: JSON objects must not contain duplicate keys. If any edge object or batch request object contains the same key twice (e.g. `{"from":"A","from":"B","to":"C","distance":5}` or `{"distance":5,"distance":5}`), the whole file is invalid (exit 2). `encoding/json` silently keeps last, so you must detect duplicates explicitly.

- Node ID encoding: a node ID that decodes to the Unicode replacement character U+FFFD (e.g. lone surrogate escape `"\uD800"` which Go decodes to FFFD) is invalid. Any graph containing such a node, or any edge/request referencing it, is invalid exit 2 (not no-route).

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
  - Empty `""` or whitespace-only `"   "` present → no route not invalid: output `[]`, `-1`, exit 1 if any, batch continues.
  - Extra and nested fields ignored.
- Order preserved. Empty array `[]` valid exit 0 no lines. All no-route → exit 1 with N lines each `[] -1`.
- Non-existing location with non-empty value → no route (includes `" A"` leading space distinct from `"A"`). Single mode: empty/whitespace `--from`/`--to` invalid exit 2 (distinct from batch no-route); leading space `" A"` in single → no-route exit1 if not a node.
- Mixing families: within a single batch request object, mixing key families is invalid. E.g. `{"source":"A","to":"B"}` (source from one family, to from another) or `{"from":"A","destination":"B"}` or having both families `{"source":"A","destination":"C","from":"A","to":"B"}` → whole file invalid exit 2.
- Type validation: `distance` invalid-type matrix – null, bool true/false, object `{}`, array `[]` for distance are invalid (exit 2).

## Routing contract – raw distance + tie-break

- Minimize sum raw road length (float, scientific plus). Source == destination exact → path `[source]`, distance 0 even if isolated.
- No route → `{"path":[],"distance":-1}` exit1.
- Tie-break: when total raw equal within 1e-9, pick lexicographically smallest route.
  - Examples: `["A","B","D"]` < `["A","C","D"]` because `B<C`.
  - ASCII order matters: `'-'45 < '.'46 < '_'95`, `'A'65 < 'a'97`, and numeric chars by ASCII so `A10 < A2` because `'1'<'2'`.
  - Deterministic across runs.

## Output – raw routing contract

- Single success strict: exactly `{"path":[...],"distance":8}` keys – no extra fields. Distance number not string, path elements strings.
- Single no route strict: `{"path":[],"distance":-1}` exit1.
- Batch success strict: exactly `{"source":...,"destination":...,"path":[...],"distance":8}` – echo source/dest exact including empty.
- Batch no route same keys with `[] -1`.
- Invalid no stdout exit2. Exit 0 all routed, 1 some no-route, 2 invalid.

## Performance – raw

500 locations 2000 legs 100 req <2s, 200 req <3s; 1000 line <2.5s, 2000 <3.5s, 5000 line <4.5s; 100 locations 500 batch <4s, 200 linear 2000 batch <6s; dense 100 locations 5000 edges <2s; batch relative not ~100× single. Go required.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C
./router --graph=network.json --from=A --to=C
./router --graph network.json --source A --destination C
./router --from A --graph network.json --to B
./router --help --unknown
./router help
./router --graph network.json --requests routes.json
./router --graph network.json --requests []
```
