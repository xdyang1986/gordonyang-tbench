# codimango/control-plan

Control Plane Coordinator — a ZooKeeper-like cluster coordinator in Go, built from scratch.

Implement a single Go program at `/app` that reads a stream of commands from stdin and writes query results to stdout. It maintains in-memory cluster state and, when `COORD_STATE_DIR` is set, persists that state durably to a crash-consistent on-disk log.

The coordinator must handle:

- **Membership** — `REGISTER` / `HEARTBEAT` / `FAIL`, with lazy heartbeat expiration (`timeout`).
- **Sticky weighted leader election** (`QUERY_PRIMARY`) — the first-registered node becomes primary and stays primary; a later higher-weight/fresher node does **not** preempt it; expiration does not unseat it; only an explicit `FAIL` of the incumbent triggers re-election (best non-failed by weight → freshness → address → id).
- **Client routing** (`QUERY_CONNECT`) — deterministic sum-of-bytes-modulo over id-sorted alive nodes.
- **Stable routing** (`QUERY_ROUTE`) — deterministic, minimal-disruption assignment (changing one node must not reshuffle clients among the others).
- **Zone-aware replica selection** (`QUERY_REPLICAS <k>`) — preference-ordered, zone-diverse first then fill; `min(k, alive)` entries.
- **Durability** — append-only CRC-framed log, crash-consistent recovery (torn/corrupt trailing record tolerated), atomic compaction (`COMPACT`), and leadership preserved across restart/compaction.

Go standard library only. Tests are black-box pytest driving the built binary via stdin/stdout, including process restarts against a shared `COORD_STATE_DIR` to verify durability.

See `instruction.md` for the full specification.

## Validation

Latest online validation — all gates passing:

| Gate | Result |
|------|--------|
| Structural | PASS (9/9) |
| Oracle | 3/3 |
| Metacode/Opus pass-fail balance | PASSED (avocado 4/5, opus [claude-opus-4-6] 5/5, gpt [gpt-5.5] 5/5) |
| AI assessment | Accept (0 Critical / 0 High / 0 Medium / 0 Low) |
| Contamination | MEDIUM (passing) |
| Provenance | Clean |

Difficulty comes from a prior-violating leadership rule (sticky election) combined with a large cumulative correctness surface (persistence + election + routing + zone replicas + stable routing) under all-or-nothing scoring: the strong models solve it reliably while the weaker model degrades (edge misses and occasional failure to complete a buildable solution).
