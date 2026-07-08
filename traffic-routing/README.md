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

The instruction states the objective and the hash/point/tie-break/zone mechanics
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
7. **Zone diversity + fallback.** Replicas prefer distinct zones and only reuse a
   zone once all reachable zones are exhausted — a rushed impl that ignores
   `zone` (or that drops slots instead of falling back) gets the replica set
   wrong.

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
  toolchain. Passes the full suite locally (40/40); a plausible naive
  implementation (no wraparound, next-`R`-points without dedup, down nodes left
  on the ring and filtered from output, zone-unaware replicas, trailing-newline
  empty key, always exit 0, no config validation) fails 36/40.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

Calibration history:

| Version | Change | avocado / opus / gpt | Verdict |
|---|---|---|---|
| v1 (`30aec83`) | no zone rule | 4/5 · 5/5 · 0/5 | passed |
| v2 (`646d99a`) | +zone rule, fully specified | 5/5 · 4/5 · 0/5 | **too easy** |
| v3 (current) | zone rule kept; re-hide weight-0 + wraparound | — | re-validating |

v2 added **zone-aware replica placement** to lower algorithm-recall risk (the
routing mechanism is no longer a stock consistent-hash `GetN` — see
`.review/novelty-report_*.md`, which rated v1 MEDIUM recall). But stating the
zone rule precisely made the whole spec fully-specified (AI assessment
0C/0H/0M/0L), and avocado — which aces precise specs — went 5/5 (too easy).

v3 keeps the zone rule (for novelty) but restores implicit difficulty by
re-hiding two standard, derivable, test-enforced edges: **weight-0 ⇒ no traffic**
(derivable from "weight = virtual-node count") and **ring wraparound** (derivable
from "clockwise around the ring"). Local signal (unchanged tests): reference
**40/40 pass**, plausible naive impl **36/40 fail**. If avocado still lands 5/5,
the next lever is to leave the zone *fallback* objective-only (implicit
mechanism) or revert to the passing v1 shape.
