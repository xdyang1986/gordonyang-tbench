# Turn 1: Distance-Based Shortest Path (Go) – Step 1

Build a routing service that selects best path by physical distance. You will extend same binary in Turn 2.

Binary: `/app/router` via `go build -o router .` from `/app`. Stdlib only – `go.mod` no external require, no dotted imports (tested by test_stdlib_only).

### CLI

```
router --graph <PATH> --from <NODE> --to <NODE>
router --graph <PATH> --requests <PATH>
router --help | -h | help | (no args) → help containing graph, from, to, requests, help, exit 0
```

- `--graph` required, `--from/--to` required for single mode (also accept `--source/--destination` alternative keys, prefer source/destination if both present).
- `--requests` optional batch mode, ignores `--from/--to`.
- `--flag=value` and `--flag value` both accepted (`--graph=path.json`, `--from=A` etc.).
- Unknown flag or missing required → exit 2 no stdout.
- `--traffic` NOT supported in Turn1 → exit 2 unknown. Turn2 adds `--traffic` as valid, so flag sets are non-cumulative by design (Turn1-only test).

### Graph JSON

```json
{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5,"extra":"ignore"}],"extra_top":"ignore"}
```

- `nodes`: required non-empty unique strings, at least 1. Whitespace-only `""` or `"   "` invalid → exit2. Case-sensitive: `A` vs `a` distinct.
- `edges`: required array, each `from,to` strings non-empty whitespace-only invalid, must exist in nodes, `from!=to`, `distance` number >0 int/float/scientific (`1e3`). Extra fields ignored. Graph undirected, duplicate edges allowed – keep smallest distance.
- Invalid graph (empty/dup nodes, whitespace id, missing node ref, distance ≤0 missing not number, self-loop, invalid JSON trailing comma/comments/BOM, unreadable) → exit2.

### Requests JSON (Batch)

```json
[{"source":"A","destination":"C","priority":1},{"from":"B","to":"D"}]
```

- Must be JSON array, else invalid exit2.
- Each element must contain `source/destination` or `from/to`. Prefer `source/destination` if both present.
- **Missing key vs empty:** missing key (e.g., `{"source":"A"}`) → invalid exit2, whole file invalid. Empty string present (`{"source":"","destination":"B"}` or `"   "`) → no-route `path:[], distance:-1`, batch continues, exit1 if any. Must be distinguished.
- Values must be strings else invalid. Extra fields ignored. Order preserved.

### Routing

- Dijkstra minimizing sum distance (float). Source==dest → path [src], distance 0. No path → `path:[], distance:-1` exit1.
- **Tie-break:** equal distance within 1e-9 → lexicographically smallest path (element-by-element case-sensitive, prefix shorter wins). Tests include 3-way equal tie A-B-D, A-C-D, A-E-D (B<C<E).
- Performance: batch 100/5000 efficient, relative bound `elapsed_100 <=25*base_1+1` and `5000 <=200*base+5` catches re-parse, dense graph <2s.

### Output

Single success: `{"path":["A","B","C"],"distance":8}` (float if needed)
Single no path: `{"path":[],"distance":-1}` exit1
Batch: one JSON line per request in order, exit 0 all routed, 1 some no-route, 2 invalid
Invalid → no stdout exit2
