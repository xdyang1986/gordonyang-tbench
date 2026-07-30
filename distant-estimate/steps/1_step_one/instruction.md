# Turn 1: Distance-Based Best Path Selection (Go) – Multi-Turn Step 1

## Background
A logistics platform needs a routing service that selects the best path between locations based purely on physical distance. Build the first phase of the router: distance-only shortest path.

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
- `--graph <PATH>` – required, path to graph JSON (see format)
- `--from <NODE>`, `--to <NODE>` – source and destination for single query mode, both required if --requests not used
- `--requests <PATH>` – optional batch mode, path to requests JSON array. If provided, ignores --from/--to.
- `--help`, `-h`, `help` – prints help to stdout containing keywords: `graph`, `from`, `to`, `requests`, `help` and exits 0
- Bare binary no args → help exit 0
- Unknown flag or missing required args → exit 2, no stdout expected (stderr may contain message)

### Graph JSON Format (MUST)

```json
{
  "nodes": ["A","B","C","D"],
  "edges": [
    {"from":"A","to":"B","distance":5},
    {"from":"B","to":"C","distance":3},
    {"from":"A","to":"C","distance":10}
  ]
}
```

Rules:
- `nodes` – required array of non-empty unique strings (node IDs). At least 1.
- `edges` – required array. Each edge has `from` (string), `to` (string), `distance` (number >0 integer). `from` and `to` must exist in nodes. `from` != `to` (no self loops, invalid if self-loop). Distance must be >0, integer (tests use integers but accept floats as valid for Turn1 if >0).
- Graph is **undirected**: edge A-B can be traversed both ways with same distance. Duplicate edges between same unordered pair are allowed; keep smallest distance for routing (or keep all – shortest wins).
- Invalid graph: empty/duplicate nodes, empty node ID, edge referencing non-existing node, distance <=0, self-loop, invalid JSON, unreadable file → exit 2, no stdout (stderr may explain). Tests explicitly check negative distance, self-loop, duplicate node ID, empty node ID.

### Requests JSON Format (Batch Mode)

When `--requests` is used:

```json
[
  {"source":"A","destination":"C"},
  {"source":"B","destination":"D"}
]
```

Alternative key support: also accept `from`/`to` keys for backward compat: `{"from":"A","to":"C"}`. If request contains both forms, prefer `source`/`destination`. Mixed formats allowed.

Each entry must have source & destination strings that are non-empty (validation: if request has empty source/destination, treat as "no route" not invalid, unless source/destination not a string – then invalid -> exit 2). But file itself must be valid JSON array; otherwise exit 2.

### Routing Algorithm – Distance-Based Shortest Path (MUST)

- Use Dijkstra (or equivalent shortest path) minimizing sum of `distance` along path.
- If source == destination: path = [source], distance = 0.
- If no path exists: special handling (see Output).
- Tie-breaking: When multiple paths have identical total distance, choose **lexicographically smallest path** defined as:
  - Compare path arrays element-by-element as strings.
  - At first differing index, smaller string wins (e.g., ["A","B","D"] < ["A","C","D"] because B<C).
  - If one path is prefix of other, shorter wins (should not happen for same source/dest with positive weights except same node case, but handle).
  - This tie-break must be deterministic and enforced in all modes. Tests include explicit tie case where two equal-distance paths exist.

- Implementation must be efficient for up to 500 nodes, 2000 edges, 100 requests: <2 sec.

### Output Format (MUST)

Single query mode (`--from X --to Y`):

Success (path found):
- stdout: JSON object `{"path":["A","B","C"],"distance":8}` on one line
- exit 0

No path:
- stdout: `{"path":[],"distance":-1}` (empty path, distance -1)
- exit 1

Invalid input:
- no stdout
- exit 2

Batch mode (`--requests PATH`):

For each request in input order, output one JSON line to stdout (newline-delimited):

Success per request:
`{"source":"A","destination":"C","path":["A","B","C"],"distance":8}`

No path per request:
`{"source":"A","destination":"C","path":[],"distance":-1}`

Exit codes for batch:
- 0 if every request successfully routed (all found)
- 1 if at least one request has no path (but input valid)
- 2 if invalid input (graph invalid, requests file unreadable/invalid JSON structure, requests not an array, etc.)

Output must be exactly one JSON object per line, no extra lines, no extra spaces requirement except valid JSON (tests parse each line). Output order must match input request order.

For single mode, output source/destination fields NOT required (only path+distance).

Distance type: output integer if sum is integer; tests accept int. Do not output float for Turn1 unless needed.

### Exit Code Summary

0 – success, all routes found (or help)
1 – valid input but at least one route not found / disconnected
2 – invalid input (bad JSON, validation failure, missing files, missing flags)

### Constraints

- Go stdlib only (`go.mod` no external require, no dotted imports)
- Must compile via `go build -o router .` from `/app`
- No network access at runtime
- Must handle source==destination, disconnected graphs, tie-breaking
- Help output must contain all keywords: graph, from, to, requests, help (case-insensitive search)
- Bin binary name `router` at `/app/router`
- Must respect --graph path absolute or relative; assume file path exists only if provided.

### Examples

```bash
go build -o router .
./router --help
./router --graph graph.json --from A --to C
# Output: {"path":["A","B","C"],"distance":8}

./router --graph graph.json --requests req.json
# req.json: [{"source":"A","destination":"C"},{"source":"X","destination":"Y"}]
# Output per line:
# {"source":"A","destination":"C","path":["A","B","C"],"distance":8}
# {"source":"X","destination":"Y","path":[],"distance":-1}
```

Implement at `/app` – Turn1. Turn2 will reuse same binary with additional flag.
