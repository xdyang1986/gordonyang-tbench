# Turn 1: Logistics Route Planning – Raw Distance Routing (EXTRA HARD)

Logistics platform needs routing that picks best route by physical road length. You build phase-1 router: raw-distance only. Phase-2 will add traffic multipliers.

Implement Go CLI at `/app`, module `router`, stdlib only (`go list` no dotted imports, no external require). Binary `/app/router` via `go build -o router .`.

## CLI – routing contract

```
router --graph <PATH> --from <NODE> --to <NODE>
router --graph <PATH> --requests <PATH>
router --help | -h | help
router (no args) -> help
```

- `--graph` required, path to road network JSON.
- `--from` / `--to` origin/destination for single route; fallback `--source`/`--destination`.
- `--requests` batch file; if present, ignores `--from`/`--to`.
- `--help`, `-h`, positional `help` prints help to stdout containing `graph, from, to, requests, help` and exits 0. **Help precedence:** if any help token appears anywhere, help wins even with unknown/missing flags.
- Bare no args → help 0. Unknown flag or missing required → exit 2 no stdout.
- Flags accept `--flag value` and `--flag=value` forms; order does not matter.
- `--traffic` not supported in Turn1 → exit 2 unknown (flag evolves in Turn2).

## Road network JSON – raw-distance manifest

```json
{
  "nodes": ["A","B","C"],
  "edges": [{"from":"A","to":"B","distance":5,"extra":"ignore"}],
  "extra_top":"ignore"
}
```

- `nodes`: required array **strings only** (number/null/bool/object/array → invalid). At least 1. Whitespace-only or empty → invalid. Location IDs case-sensitive (`A` vs `a` distinct). May contain `-_./` etc. Unique **exact** match including leading/trailing spaces: `" A"` vs `"A"` are distinct valid IDs. Extra top-level fields ignored.
- `edges`: required array **objects only** (null/string/number → invalid). Each leg: `from` string required, `to` string required, `distance` number >0 required.
  - `from`/`to` must be string (not number/null), presence required, empty/whitespace-only → invalid, exact equality `from==to` → self-loop invalid, must exist **exactly** in nodes (no trim, `" A"` ≠ `"A"` → missing location → invalid).
  - `distance`: JSON number >0 finite, int/float/scientific (`2.5`, `1e3`, `1e-3`). Zero, negative, `-0`, missing, null, string, bool, object/array → invalid. `+5` is invalid JSON.
  - Extra fields inside leg ignored.
- File itself: top-level must be object with `nodes`+`edges`; if array/string/number/null → invalid. `nodes` or `edges` missing or not array → invalid. Invalid JSON trailing comma, `//`/`/* */` comments, BOM `\xEF\xBB\xBF`, unreadable → exit 2 no crash. Empty nodes → invalid. Duplicate exact nodes → invalid.
- Road network is undirected: leg A-B usable both ways. Duplicate legs same unordered pair (including reverse B-A) → keep smallest raw for routing.

Any violation → exit 2 no stdout.

## Batch routing requests – raw

```json
[{"source":"A","destination":"C"}, {"from":"B","to":"D","extra":"ignore"}]
```

- File must be JSON array (object/string → invalid).
- Each element must be object (null/number/string → invalid whole file).
- Each object needs `source`/`destination` **or** `from`/`to`; if both, prefer `source`/`destination`.
  - Missing key entirely (e.g. `{"source":"A"}`) → invalid whole file exit 2.
  - Empty `""` or whitespace-only `"   "` present → **no route** (not invalid): output path `[]`, distance `-1`, counts as no-route, batch continues.
  - Value must be string (number/null/bool → invalid). `{}` missing both → invalid. `null` → invalid.
  - Extra fields ignored.
- Order preserved. Non-existing location with non-empty value → no route, not invalid (includes `" A"` with leading space distinct from `"A"`). Single mode: empty/whitespace `--from`/`--to` → invalid exit 2 (distinct from batch no-route).

## Routing contract – raw distance with tie-break cascade

- Minimize sum raw road length (float, scientific). Source == destination exact → path `[source]`, distance 0 even if isolated.
- No route (disconnected or location not in set for query) → `{"path":[],"distance":-1}` exit 1.
- **Tie-break cascade:** when total raw equal within 1e-9, pick lexicographically smallest route:
  - Element-by-element ASCII case-sensitive: `'-' 45 < '.' 46 < '_' 95`, `'A' 65 < 'a' 97`, `B<C`.
  - Prefix shorter wins (completeness). Must be deterministic regardless of map iteration or insertion – sort neighbor locations.
  - Deeper case: decision may be at depth 2 (diamond of diamonds, e.g. all paths A-B1-B2-Z, A-B1-C2-Z, A-C1-B2-Z, A-C1-C2-Z cost 3, smallest B1<B… and B2<… → A-B1-B2-Z).
  - Request may contain both `source` and `from` keys – prefer `source`/`destination`.

## Performance

- 500 locations 2000 legs 100 requests <2s, 200 requests <3s.
- 1000 locations line <2.5s, 2000 locations <3.5s, 100 locations 500 batch <4s.
- Batch 100 must not be ~100× single (catch per-request re-parse). Go required.

## Output

Single success: `{"path":["A","B","C"],"distance":8}` (int if whole else float)
Single no route: `{"path":[],"distance":-1}` exit 1
Invalid: no stdout exit 2

Batch: one JSON line per request in order:
Success `{"source":"A","destination":"C","path":["A","B","C"],"distance":8}`
No route `{"source":"A","destination":"C","path":[],"distance":-1}`
Exit 0 all routed, 1 some no-route, 2 invalid.

## Constraints

Stdlib only, `go build -o router .`, `go.mod` no external require, binary `/app/router`, help 5 keywords, flag sets evolve (Turn1 no traffic), float tolerance 1e-9 tie, 1e-6 output.

## Examples

```
go build -o router .
./router --graph network.json --from A --to C
./router --graph=network.json --from=A --to=C
./router --from A --graph network.json --to B
./router --help --unknown
./router help
./router --graph network.json --requests routes.json
```
