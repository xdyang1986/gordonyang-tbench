# codimango/traffic-routing

## Task Overview

Build, from scratch in Go, a command-line consistent-hash traffic router
`router`. The agent starts with an empty `/app/src` and must implement the full
CLI:

- `router --config <PATH> --requests <PATH>`
- `--config` is a JSON document `{"replicas": R, "nodes": [{"id", "weight",
  "zone", "status"} ...]}` where `weight` is the number of virtual nodes the node
  places on the ring, `zone` is an optional rack/zone label (default: the node's
  own id), and `status` is `"up"` or `"down"`.
- `--requests` is a newline-delimited list of routing keys.
- Prints one JSON array per request, in input order — the ordered route
  (primary first) of the distinct nodes the key maps to, or `[]`.
- Exit `0` when every request routed to `R` nodes · `1` when at least one
  request is under-replicated (fewer than `R` eligible nodes) · `2` on unusable
  input (bad/missing config JSON, `replicas < 1`, empty/duplicate id, negative
  weight, bad status, or unreadable requests) — no output on exit 2.

## Routing model

Each eligible node (status `up` **and** `weight > 0`) places `weight` virtual
points on a 32-bit ring at positions `H("<id>#<i>")` for `i` in `[0, weight)`,
where `H` is CRC-32 with the IEEE polynomial (`crc32.ChecksumIEEE`, equal to
Python's `zlib.crc32`). A key sits at `H(key)`. The route for a key is found by
scanning the ring **clockwise** from the key's position, collecting **distinct**
nodes until `R` are gathered; the ring is **circular**, so the scan wraps past
the last point.

**Zone-aware replica placement** (a deliberately non-library routing rule):
over the clockwise distinct-node order, a node is chosen only if its zone is not
yet represented among the already-chosen nodes; nodes whose zone is already used
are set aside, and if fewer than `R` are chosen after the pass, the set-aside
nodes fill the remaining slots in clockwise order. `zone` defaults to the node's
id, so configs without zones reduce exactly to the plain first-`R` walk.

The instruction states the hash/point/tie-break mechanics and the zone
**objective** ("spread a key's nodes across distinct zones as much as possible")
precisely, but leaves the consequences a rushed implementation tends to miss:

1. **Circular wraparound.** Keys hashing past the last point, and replica walks
   that run off the end, must wrap to the start of the ring.
2. **Replication clamping.** With fewer than `R` eligible nodes each route is
   shorter than `R` (all available nodes, no duplicates, no crash) and the run
   is degraded (exit 1).
3. **`weight = 0` places no points**, so such a node is never routed to even
   when it is `up`, and it does not count toward eligibility.
4. **Down nodes are off the ring**, so their key ranges migrate clockwise to the
   next up node — routing over `{up nodes}`, not "route over all then drop the
   down node from the output".
5. **Distinct nodes only.** Repeated virtual points of an already-selected node
   are skipped while walking.
6. **Request-stream edges.** A trailing newline is not an extra empty key; a
   mid-file blank line is an empty key; `""` is routable; duplicate request
   lines produce repeated output lines in order; an empty requests file produces
   no output.
7. **Zone best-effort vs strict.** Reaching `R` distinct *nodes* takes priority,
   so a zone is reused once distinct zones run out (derivable from the exit-0
   contract) — a "strict distinct-zones" reading that under-replicates is wrong.
8. **Zone default.** An unset/empty `zone` is the node's own id (its own rack),
   not a single shared empty zone.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds the agent's source with
  `go build ./...`, enforces the standard-library-only constraint, then drives
  the built binary over configs and request streams constructed in Python. A
  reference `route_all()` computes the exact contract (`zlib.crc32` is the same
  IEEE CRC-32 as Go's `crc32.ChecksumIEEE`), so hard-coded outputs cannot pass.
  Coverage spans basic routing, clockwise wraparound (keys past the last point
  and replica walks that wrap), full replication reaching all nodes, replication
  clamping / degraded exit, `weight = 0`, down-node exclusion and key migration,
  zone-diverse replica placement (prefer distinct zones; fall back when fewer
  than `R` zones are reachable; default zone = id), request-parsing edges
  (trailing newline, blank lines, empty key, duplicates, empty file), the exit-2
  validation contract, unicode keys/ids, and a seeded randomized model (with
  zones). Because the reference and oracle share the same hash and `(pos, id, i)`
  tie-break, exact output comparison is fair even when ring positions tie.
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation (`encoding/json`, `hash/crc32`, `sort`, `flag`) and builds it —
  no python/sed dependency, since the solution container has only the Go
  toolchain. Passes the full suite locally (41/41); a plausible naive
  implementation (no wraparound, next-`R`-points without dedup, down nodes left
  on the ring and filtered from output, zone-unaware replicas, trailing-newline
  empty key, always exit 0, no config validation) fails 37/41.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

Latest validation run (commit `88b7a34`) — **passing**:

| Check | Result |
|---|---|
| Structural | 9/9 |
| Oracle | 3/3 |
| Difficulty balance | passed — avocado 1/5, gpt 0/5, opus no-trials (not trivial, ≥1 solved) |
| AI assessment | Accept (0 Critical / 0 High / 1 Medium / 0 Low) |
| Contamination | MEDIUM |
| Provenance | clean |

The difficulty gate passes because the weak runner (avocado) is not trivial
(1/5) while at least one runner solves it. The single AI-assessment Medium is the
intended "spec requires some guessing" — it comes from the two zone behaviors the
instruction leaves implicit (items 7–8 above): whether under-replication is
allowed when zones run short (it is not — reaching `R` distinct *nodes* takes
priority, so a zone is reused), and what an unset zone means (the node's own
rack). Both are enforced by exact hidden tests.

Difficulty comes from those genuinely two-way *semantic* guess-points, not from
algorithm complexity or standard implicit edges: a plausible good-faith solution
that reads them the other way fails. Verified locally: the reference passes the
full suite (41/41), a *strict distinct-zones* variant fails 3, a *shared-empty
zone-default* variant fails 2, and a deliberately-naive implementation fails
37/41.

The margin is thin on the hard side (avocado 1/5, gpt 0/5, opus no-trials — a
known platform flake). If a re-validation ever flips it to too-hard, the remedy
is to state the zone default (own-id) explicitly to nudge avocado up while
keeping the best-effort behavior implicit.
