# Turn 1: Distance-Based Best Path Selection (Go) – Multi-Turn Step 1 – HARD

## Background
A logistics platform needs a routing service that selects the best path between locations based purely on physical distance. Build the first phase of the router: distance-only shortest path. This is HARD – tests cover floats, extra unknown fields, whitespace validation, 3-way tie-breaks, large batches, unknown flags, case-sensitive IDs.

You will extend this same binary in Turn 2 to incorporate live traffic data.

Data directories `/app` is writable. Binary must be built via `go build -o router .` from `/app`.

## Task – Implement Go Router at `/app` (module `router`)

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external `require`.

Binary location: `/app/router` after build. Build command verified: `go build -o router .` from `/app`.

### CLI Interface (MUST)

```
router --graph <PATH> --from <NODE> --to <NODE>
router --graph <PATH> --requests <PATH>
router --help | -h
router (no args) -> help
```

Flags:
- `--graph <PATH>` – required, path to graph JSON
- `--from <NODE>`, `--to <NODE>` – source and destination for single query mode, both required if --requests not used. Also accept alternative spellings `--source` and `--destination` or `-from/-to`? No – only `--from/--to` required, but tests may use `--graph` + `--from`/`--to` only.
- `--requests <PATH>` – optional batch mode, path to requests JSON array. If provided, ignores --from/--to.
- `--help`, `-h`, `help` – prints help to stdout containing keywords: `graph`, `from`, `to`, `requests`, `help` and exits 0
- Bare binary no args → help exit 0
- Unknown flag or missing required args → exit 2, no stdout expected
- **Equals syntax:** Flags must accept both `--flag value` and `--flag=value` forms (e.g., `--graph=path.json`, `--from=A`, `--to=B`, `--requests=req.json`). This is free for any flag-package implementation (Go `flag` package supports it automatically) and is tested by `test_from_to_equals_syntax`.

### Graph JSON Format (MUST) – HARDER

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
- `nodes` – required array of non-empty unique strings (node IDs). At least 1. **Whitespace-only strings** e.g., `"   "` or `""` are considered empty → invalid graph exit 2. Node IDs are case-sensitive: `"A"` and `"a"` are distinct, not duplicate. May contain letters, numbers, hyphen, underscore (e.g., `Node-A_1`). Unique check is exact string match.
- `edges` – required array. Each edge has `from` (string), `to` (string), `distance` (number >0). Distance may be integer, **float**, or **scientific notation** >0 (e.g., 2.5, `1e3` =1000). `from` and `to` must exist in nodes, trimmed whitespace invalid (if from/to is empty or whitespace-only → invalid). `from` != `to` (no self loops). Extra unknown fields inside edge objects or top-level (e.g., `"extra"`) must be **ignored**, not cause invalid.
- `--traffic` flag is **NOT supported** in Turn1 – if provided, exit 2 (unknown flag). Turn1 only supports --graph, --from/--to, --requests, --help.
- Graph is **undirected**: edge A-B can be traversed both ways with same distance. Duplicate edges between same unordered pair are allowed; keep smallest distance for routing.
- Invalid graph: empty/duplicate nodes, empty/whitespace node ID, edge referencing non-existing node, distance <=0 or missing or not a number, self-loop, invalid JSON (trailing comma, etc.), unreadable file → exit 2, no stdout. Tests check negative, zero, self-loop, duplicate, empty, whitespace, non-numeric distance, missing fields, extra top-level ignored (should NOT be invalid).

### Requests JSON Format (Batch Mode) – HARDER

When `--requests` is used:

```json
[
  {"source":"A","destination":"C","priority":1},
  {"from":"B","to":"D","extra":"ignore"}
]
```

- File must be JSON array. If not array or invalid JSON → exit 2 no stdout.
- Each element: object containing `source`/`destination` **or** `from`/`to`. If both forms present, prefer `source`/`destination`. Values must be strings. If value not string → invalid exit 2. If string is empty or whitespace-only → treat as **no route** (not invalid) – output empty path -1, counts as no route, batch continues. This includes `""`, `"   "`. Extra unknown fields in request objects (e.g., `priority`) must be **ignored**.
- Example batch with empty source is **no route**, not invalid: `{"source":"","destination":"B"}` → `{"source":"","destination":"B","path":[],"distance":-1}` exit 1 if any no route.
- Output order must match input order.

### Routing Algorithm – Distance-Based Shortest Path (MUST) – HARD

- Use Dijkstra minimizing sum of `distance` (float) along path. Distance may be scientific notation, parse as float.
- If source == destination: path = [source], distance = 0.
- If no path: special handling.
- **Tie-breaking HARD:** When multiple paths have identical total distance within 1e-9 tolerance, choose **lexicographically smallest path**:
  - Compare element-by-element as strings case-sensitive.
  - At first differing index, smaller string wins (e.g., B < C)
  - If one is prefix of other, shorter wins (rare but handle)
  - Tests include **3-way equal distance tie**: A-B-D (5+5), A-C-D (5+5), A-E-D (5+5) – B<C<E so A-B-D must win regardless of discovery order. Implementation must sort neighbors or use priority queue with lexicographic secondary.
  - Additional tie test: request contains both `source` and `from` keys – must prefer `source`/`destination` over `from`/`to`.
- Performance: 500 nodes, 2000 edges, 100 requests <2 sec, 200 requests <3 sec in Go. Tests include linear chain 100 nodes, 500 nodes with shortcuts, large batch 100 and 200 requests – must be efficient (<2 sec).

### Output Format (MUST)

Single query mode:

Success:
`{"path":["A","B","C"],"distance":8}` – distance may be float if sum is float, e.g., 8.5

No path:
`{"path":[],"distance":-1}` exit1

Invalid:
no stdout exit2

Batch mode: one JSON line per request in order

Success:
`{"source":"A","destination":"C","path":["A","B","C"],"distance":8}`

No path:
`{"source":"A","destination":"C","path":[],"distance":-1}`

Exit: 0 all routed, 1 at least one no route, 2 invalid

Distance output: integer if whole number, float otherwise – tests parse as number and accept both.

### Exit Codes

0 success/help, 1 no route but valid, 2 invalid

### Constraints – HARD

- Go stdlib only, `go build -o router .`
- Must handle: floats, extra unknown fields ignored (graph top-level, edge, requests), whitespace-only node IDs invalid (graph) but empty request source treated as no route, case-sensitive IDs, duplicate edges min, 3-way tie-break deterministic regardless of map iteration order (must sort), unknown flags exit2, file not found exit2
- Help contains 5 keywords
- Binary `/app/router`

Examples as before.
