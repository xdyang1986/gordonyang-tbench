# Turn 1: Distance-Based Best Path Selection (Go) – Multi-Turn Step 1 – EXTRA HARD

## Background
A logistics platform needs a routing service that selects the best path between locations based purely on physical distance. Build the first phase of the router: distance-only shortest path. This is EXTRA HARD – tests cover floats, scientific notation, extra unknown fields, whitespace vs missing-key distinction, 3-way/5-way/10-way lex tie-breaks with deep comparison and case-sensitive ASCII, special-char nodes, duplicate edges (including reverse), performance of 2000 nodes 10000 edges, 1000 requests, strict JSON validation (trailing comma, // comments, BOM), nodes/edges type validation, edge leading/trailing space exact-match semantics, help precedence over unknown flags, flag order independence, positional help, single-mode empty vs batch-mode empty semantics, and non-existing node no-route.

You will extend this same binary in Turn 2 to incorporate live traffic data.

Data directories `/app` is writable. Binary must be built via `go build -o router .` from `/app`.

## Task – Implement Go Router at `/app` (module `router`)

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external `require`.

Binary location: `/app/router` after build. Build command verified: `go build -o router .` from `/app`.

### CLI Interface (MUST) – EXTRA HARD

```
router --graph <PATH> --from <NODE> --to <NODE>
router --graph <PATH> --requests <PATH>
router --help | -h | help
router (no args) -> help
```

Flags:
- `--graph <PATH>` – required, path to graph JSON
- `--from <NODE>`, `--to <NODE>` – source and destination for single query mode, both required if --requests not used. Also accept alternative spellings `--source` and `--destination` as fallback if --from/--to missing.
- `--requests <PATH>` – optional batch mode, path to requests JSON array. If provided, ignores --from/--to.
- `--help`, `-h`, `help` – prints help to stdout containing keywords: `graph`, `from`, `to`, `requests`, `help` and exits 0. **Help precedence:** If any of `--help`, `-h`, or positional arg `help` appears anywhere in args, you must print help and exit 0 immediately, even if other flags are unknown, missing, or invalid. Example: `router --help --unknown` → help exit 0, not exit 2. `router --graph x --help` → help exit0. `router help` → help. `router --help --graph=dummy --from=A --to=B` → help exit0. This requires scanning args before strict flag parsing.
- Bare binary no args → help exit 0
- Unknown flag or missing required args → exit 2, no stdout expected, **unless help present** (see precedence above).
- **Equals syntax:** Flags must accept both `--flag value` and `--flag=value` forms (e.g., `--graph=path.json`, `--from=A`, `--to=B`, `--requests=req.json`). This is free for any flag-package implementation (Go `flag` package supports it automatically) and is tested by `test_from_to_equals_syntax` and `test_unknown_long_flag_with_equals` etc.
- **Flag order independence:** Flags may appear in any order. `router --from A --graph g.json --to B` must work same as `router --graph g.json --from A --to B`. Implementation must not assume fixed order.
- `--traffic` flag is **NOT supported** in Turn1 – if provided and help not present, exit 2 (unknown flag). Turn1 only supports --graph, --from/--to/--source/--destination, --requests, --help/-h. **Note on multi-turn evolution:** Turn2 adds `--traffic` as valid flag, so flag sets are non-cumulative by design. `test_unknown_traffic_flag_in_step1_invalid` is Turn1-only and not expected to pass with Turn2 binary.

### Graph JSON Format (MUST) – EXTRA HARD

```json
{
  "nodes": ["A","B","C","D"],
  "edges": [
    {"from":"A","to":"B","distance":5, "extra":"ignore"},
    {"from":"B","to":"C","distance":3}
  ],
  "extra_top":"ignore me"
}
```

Rules:
- `nodes` – required array of **strings only** (non-string element like number, null, bool, object, array → invalid exit2). At least 1 element. Elements must be non-empty after TrimSpace? **Whitespace-only strings** e.g., `"   "` or `""` are considered empty → invalid graph exit 2. Node IDs are case-sensitive: `"A"` and `"a"` are distinct, not duplicate. May contain letters, numbers, hyphen, underscore, dot, slash (e.g., `Node-A_1`, `A.B`, `C/D`). Unique check is **exact string match** including leading/trailing spaces: `" A"` and `"A"` are distinct valid IDs (if both present they are two nodes). But `"   "` trimmed empty is invalid even if considered distinct. Leading/trailing spaces inside a node ID are preserved and part of identity: node `" A"` is valid (not whitespace-only) and distinct from `"A"`.
- `edges` – required array. Must be array of **objects only**; if edge element is null, string, number, array, bool → invalid exit2. Each edge object has `from` (required **string**), `to` (required **string**), `distance` (required **number >0**). 
  - `from`/`to` type: must be string, not number/null/bool/object/array → invalid.
  - Presence: missing `from` or `to` or `distance` key altogether → invalid exit2.
  - Distance validation: must be JSON number >0, finite, not NaN/Inf (JSON forbids NaN/Inf literals, but Go may produce Inf from 1e309? Treat Inf/NaN as invalid). Allowed int, float, scientific notation >0 (e.g., 2.5, `1e3` =1000, `1e-3`=0.001). Zero, negative, -0 (which is 0) → invalid exit2. Missing, null, string `"5"`, bool, object, array → invalid exit2. `+5` with explicit plus is **invalid JSON** itself → whole graph JSON invalid → exit2.
  - `from` and `to` value validation: after JSON parsing, trimmed whitespace check: if `strings.TrimSpace(from)==""` or same for to → invalid (empty or whitespace-only). Otherwise, `from` and `to` must exist **exactly** in nodes set (exact string equality, no trimming, no case folding). Leading/trailing spaces matter: if nodes = ["A","B"] and edge from=" A" (leading space), that edge from value " A" does NOT exist in nodes → invalid graph exit2 (edge references non-existing node). It is NOT auto-trimmed to "A". Similarly "A " trailing space is different.
  - `from` != `to` exact match (no self loops). If from==" A" and to==" A" (same exact with spaces) → self-loop invalid. But from==" A" and to=="A" are distinct, not self-loop, but if " A" not in nodes, it's invalid due to missing node, not self-loop.
  - Extra unknown fields inside edge objects or top-level (e.g., `"extra"`) must be **ignored**, not cause invalid.
- Graph JSON file itself:
  - Must be JSON object with `nodes` and `edges`. If top-level is not object (array, string, number, null) → invalid.
  - If `nodes` or `edges` field missing or not array (e.g., string, object, number) → invalid.
  - Invalid JSON **must not crash** (no panic), must exit2 no stdout. Specifically:
    - Trailing comma e.g., `{"nodes":["A"],"edges":[...,]}` → invalid JSON → exit2.
    - Comments `//` or `/* */` → invalid JSON → exit2.
    - BOM (UTF-8 BOM `\xEF\xBB\xBF` prefix) → file starts with BOM, Go's json does not strip BOM → Unmarshal error → should be caught and exit2, not crash/panic.
    - Unreadable / not found file → exit2.
  - Empty nodes array → invalid.
  - Duplicate nodes exact match → invalid.
  - Graph is **undirected**: edge A-B can be traversed both ways with same distance. Duplicate edges between same unordered pair are allowed; keep smallest distance for routing. Reverse duplicate: `{"from":"A","to":"B","distance":10}` and `{"from":"B","to":"A","distance":3}` → same unordered pair (A,B), keep min 3. Keep min across all duplicates regardless of direction.
- Invalid graph: any above violation → exit 2, no stdout.

### Requests JSON Format (Batch Mode) – EXTRA HARD

When `--requests` is used:

```json
[
  {"source":"A","destination":"C","priority":1},
  {"from":"B","to":"D","extra":"ignore"}
]
```

- File must be JSON array. If not array or invalid JSON (trailing comma, comments, BOM) → exit 2 no stdout.
- Each element of the array must be a JSON **object**; if any element is null, number, string, array, bool → invalid whole file exit2.
- Each element: object containing `source`/`destination` **or** `from`/`to`. If both forms present, prefer `source`/`destination`.
  - **Missing key vs empty string distinct (critical):** If `source`/`destination` (or `from`/`to`) keys are **missing entirely** (e.g., `{"source":"A"}` missing destination), that request is **invalid** → whole file invalid exit 2, no stdout. This is distinct from empty string: `{"source":"","destination":"B"}` with explicit empty string is **no route** (not invalid) → `path:[] distance:-1` exit 1 if any. You must distinguish absent vs empty: In Go, unmarshalling `{"source":"A"}` into `struct { Destination string }` yields `""` indistinguishable from explicit empty, so you must use `*string` or decode to `map` / `json.RawMessage` to detect presence.
  - Values must be strings. If value not string (number, null, bool, object, array) → invalid exit 2.
  - If string is present but empty or whitespace-only (`""`, `"   "` ) → treat as **no route** (not invalid) – output empty path -1, counts as no route, batch continues. Extra unknown fields in request objects (e.g., `priority`) must be **ignored**.
  - Object `{}` with no keys → missing both → invalid exit2.
  - `null` for source/destination → not string → invalid.
- Example batch with empty source is **no route**, not invalid: `{"source":"","destination":"B"}` → `{"source":"","destination":"B","path":[],"distance":-1}` exit 1 if any no route. But `{"source":"A"}` missing destination → invalid exit 2.
- Output order must match input order.
- **Non-existing node in query:** If source or destination string present, non-empty, non-whitespace, but not found in graph nodes set → treat as **no route** (not invalid). That includes values with leading/trailing spaces that are valid strings but not exact node IDs: e.g., nodes ["A","B"], request source " A" (with leading space) is valid string, not whitespace-only, not missing, so not invalid; but node " A" does not exist → no route [] -1. Distinguish from graph edge case where same " A" reference is invalid graph because edge must reference existing node.
- Single mode empty/whitespace handling different: In **single mode** (`--from`/`--to`), empty or whitespace-only flag values should be treated as **invalid** exit2 (missing required args), not no route. Because missing flags already exit2, and empty string from flag package is same as missing. So `router --graph g.json --from "" --to B` → exit2. `router --graph g.json --from "   " --to B` → exit2. Whereas batch mode `{"source":"","destination":"B"}` → no route exit1. This distinction is intentional and tested: `test_empty_source_in_batch_no_route` expects no route, but `test_single_mode_empty_from_invalid` expects invalid.

### Routing Algorithm – Distance-Based Shortest Path (MUST) – EXTRA HARD

- Use Dijkstra minimizing sum of `distance` (float) along path. Distance may be scientific notation, parse as float.
- If source == destination exact match: path = [source], distance = 0, even if node isolated.
- If no path (disconnected, or source/dest not in nodes set for query): special handling → path [] distance -1 exit 1.
- **Tie-breaking EXTRA HARD:** When multiple paths have identical total distance within 1e-9 tolerance, choose **lexicographically smallest path**:
  - Compare element-by-element as strings case-sensitive ASCII (byte compare, not lowercased). At first differing index, smaller string wins (e.g., `"A-B"` < `"A.B"` < `"A_B"` because '-' 45 < '.' 46 < '_' 95). Case-sensitive: `"A"` (65) < `"a"` (97), so `"A"` < `"a"`. 
  - If one is prefix of other, shorter wins (rare but handle; with positive weights>0 this rarely occurs for distinct simple paths, but implement for completeness).
  - Must be **deterministic regardless of map iteration order or edge insertion order** – you must sort neighbors or use priority queue with lex secondary key. Tests include **3-way equal distance tie**: A-B-D (5+5), A-C-D (5+5), A-E-D (5+5) – B<C<E so A-B-D must win regardless of discovery order. 5-way, 10-way ties: nodes B..K each 5+5 to Z – B is lex smallest → A-B-Z must win.
  - **Deeper tie:** diamond of diamonds where first differing element is at depth 2, not depth1. Example graph: A->B (1), A->C (1), B->D (1), B->E (1), C->D (1), C->E (1), D->Z (1), E->Z (1). All paths A-B-D-Z, A-B-E-Z, A-C-D-Z, A-C-E-Z have total 3. Lexicographically smallest is A-B-D-Z (B<C, and D<E). Implementation that only sorts immediate neighbors may still pass, but more complex: A-X1-Y1-Z vs A-X1-Y2-Z where X equal, Y decides. Must compare full path lex, not just second node.
  - **Case-sensitive lex:** Paths A-a-Z vs A-A-Z – "A" < "a", so path containing uppercase wins. Test `test_lexicographic_case_sensitive_ascii`.
  - Additional tie test: request contains both `source` and `from` keys – must prefer `source`/`destination` over `from`/`to`.
  - Implementation hint: priority queue should order by (distance, path lex). When relaxing equal distance (abs diff <=1e-9), compare new path vs bestPath[v] lex and keep smaller.
- Performance: 
  - 500 nodes, 2000 edges, 100 requests <2 sec, 200 requests <3 sec in Go. 
  - 1000 nodes 5000 edges single query <2.5 sec.
  - 2000 nodes 10000 edges single query <3.5 sec.
  - 100 nodes linear chain 500 batch <4 sec.
  - Tests include linear chain 100 nodes, 500 nodes with shortcuts, large batch 100, 200, 500, 1000 requests – must be efficient (<2 sec batch 100 relative bound, <3 sec for 200, <4 sec for 500). Relative bound: 100 requests must not be ~100x single request time, catch per-request re-parse.

### Output Format (MUST)

Single query mode:

Success:
`{"path":["A","B","C"],"distance":8}` – distance may be float if sum is float, e.g., 8.5. Integer if whole number accepted (tests parse as number).

No path (including query for non-existing node):
`{"path":[],"distance":-1}` exit1

Invalid:
no stdout exit2

Batch mode: one JSON line per request in order

Success:
`{"source":"A","destination":"C","path":["A","B","C"],"distance":8}` – source/destination keys must echo input values exactly (including empty string if present). If input used `from`/`to`, output should still use `source`/`destination` keys normalized? Tests expect `source`/`destination` in output always (even if input used `from`/`to`), but accept original? Implementation in reference outputs `source`/`destination` always. Follow that.

No path:
`{"source":"A","destination":"C","path":[],"distance":-1}` – same, echo source/dest exact.

Exit: 0 all routed, 1 at least one no route, 2 invalid

Distance output: integer if whole number, float otherwise – tests parse as number and accept both. Use json.Marshal float, Go may output 8 not 8.0 for integer – that's fine.

### Exit Codes

0 success/help, 1 no route but valid, 2 invalid

### Constraints – EXTRA HARD

- Go stdlib only, `go build -o router .` Module `router`, go 1.22. `go.mod` must have no external `require`. `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib).
- Must handle: floats, scientific notation (including negative exponent 1e-3, large 1e12), extra unknown fields ignored (graph top-level, edge, requests), whitespace-only node IDs invalid (graph) but empty/whitespace request source present treated as no route in batch while **missing key is invalid** (must distinguish via *string or map), empty/whitespace from/to in single mode is invalid exit2 (different from batch), case-sensitive IDs, duplicate edges min including reverse direction, tie-break deterministic regardless of map iteration (sort neighbors), deep lex tie, case-sensitive ascii tie, unknown flags exit2 (including `--flag=value` form) unless help present, file not found exit2, equals syntax `--flag=value` alongside `--flag value`, flag order independence, help precedence with extra invalid flags, positional help "help", single dash unknown flag "-x" invalid, leading/trailing spaces in node IDs exact match semantics (edge " A" != "A" → invalid graph, request " A" != "A" → no route).
- JSON invalid forms (trailing comma, // comments, BOM) must be caught as invalid exit2, not crash.
- Nodes array elements must be strings only, edges array elements must be objects only.
- Requests array elements must be objects only.
- Flag sets evolve: Turn1 has no `--traffic`, Turn2 adds `--traffic` – non-cumulative by design.
- Help contains 5 keywords, binary `/app/router`.
- Performance EXTRA HARD as above.

### Examples

```bash
go build -o router .
./router --graph graph.json --from A --to C
./router --graph=graph.json --from=A --to=C
./router --from A --graph graph.json --to B   # order independence
./router --help --unknown flag  # help still wins
./router help
./router --graph graph.json --requests req.json
```
