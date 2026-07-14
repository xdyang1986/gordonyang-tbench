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

## Why the weaker model fails (difficulty analysis)

Scoring is all-or-nothing (reward 1 only if every test passes), so a single missed
behavior sinks the whole trial. The failure modes observed in the weaker model's
losing trials:

1. **Prior-violating sticky leadership.** The natural implementation re-computes the
   "best" alive node on every `QUERY_PRIMARY`. The spec instead requires the primary
   to be *sticky*: set on the first REGISTER, never preempted by a later higher-weight
   or fresher node, unaffected by heartbeat expiration, and unseated only by an
   explicit `FAIL` of the incumbent. A recompute-best-alive implementation fails ~9
   leadership tests. This is the primary discriminator — it fights the model's default
   instinct rather than testing a spec it can transcribe.

2. **Minimal-disruption stable routing.** `QUERY_ROUTE` is specified as a *property*
   (changing one node must not reshuffle clients among the others), not an algorithm.
   The tempting move is to reuse the `QUERY_CONNECT` modulo scheme, which reshuffles
   almost every client when the node count changes and fails the stability tests. A
   correct solution must recognize the property demands consistent/rendezvous hashing.

3. **Crash-recovery edges.** The durable log must tolerate a torn/corrupt trailing
   record (partial write, bad CRC) on recovery, truncate it, and keep appending
   cleanly; compaction must be atomic (temp-file + rename) and must preserve the
   order-dependent sticky incumbent. Weaker solutions pass the happy path but miss an
   edge such as torn-tail-then-append.

4. **Cumulative surface + interactions.** Membership, expiration, sticky election,
   two distinct routing schemes, zone-aware replica selection, and crash-consistent
   persistence must all be correct simultaneously and interact correctly (e.g. the
   primary can be absent from `QUERY_NODES` yet still be primary; `timeout` is
   per-session config and is not persisted). The sheer volume of interacting,
   individually-subtle requirements means the weaker model sometimes fails to produce
   a fully correct — or even buildable — solution at all.
