# codimango/control-plan

Debug and fix a buggy ZooKeeper-like control-plane coordinator in Go — **debug-in-place**.

The image ships a coordinator at `/app/main.go` (from `environment/broken/main.go`) that
builds and runs but has subtle defects. The agent must localize and fix them in place —
without a written spec to rewrite from and without seeing the tests — so the hidden
black-box suite passes. This is the *debug-in-place* task shape (same shape as
`dr-buffer` / `database-corruption`), chosen because the from-scratch spec shape for this
coordinator repeatedly validated as too easy for the weaker model.

The coordinator handles:

- **Membership** — `REGISTER` / `HEARTBEAT` / `FAIL`, with lazy heartbeat expiration (`timeout`).
- **Sticky weighted leader election** (`QUERY_PRIMARY`) — first-registered node is primary and stays primary (a later higher-weight/fresher node does **not** preempt; expiration does not unseat); only an explicit `FAIL` of the incumbent re-elects. A leadership epoch increments on each election and is reported by `QUERY_PRIMARY`.
- **Client routing** (`QUERY_CONNECT`) — deterministic sum-of-bytes-modulo over id-sorted alive nodes.
- **Stable routing** (`QUERY_ROUTE`) — deterministic, minimal-disruption (rendezvous hashing).
- **Zone-aware replica selection** (`QUERY_REPLICAS <k>`) — preference-ordered, zone-diverse first then fill; `min(k, alive)` entries.
- **Durability** — append-only CRC-framed log, crash-consistent recovery (torn/corrupt trailing record tolerated), atomic compaction, leadership preserved across restart/compaction.

Go standard library only. Tests are black-box pytest driving the built binary via
stdin/stdout, including process restarts against a shared `COORD_STATE_DIR`.

## The planted defects

The shipped program is correct apart from three independent, cross-subsystem bugs (the
agent is not told where they are — only that outputs are wrong on certain cases):

1. **Missing epoch bump on failover** — `doFail()` re-elects the best remaining node when
   the primary is `FAIL`ed, but forgets to increment the leadership epoch, so
   `QUERY_PRIMARY` reports the stale term after a failover / fail+revive. (The sibling
   election path in `doRegister()` bumps it correctly — the inconsistency is the tell.)
2. **Zone-constraint leak in replica fill** — `replicas()` phase 2 is meant to fill up to
   `min(k, alive)` *regardless of zone*, but still tests `!usedZone[n.zone]`, so once every
   zone is used the result stops short of `k`.
3. **Off-by-one in durable recovery** — `recover()` uses `off+8+plen >= len(data)` instead
   of `>`, so a valid record that ends exactly at EOF (i.e. the last record in the log) is
   dropped on recovery, silently losing the most recent durable write across restarts.

Each is fair to find: the retained comments document the intended behavior, and the
`instruction.md` gives failing-case examples touching each subsystem. Difficulty comes from
there being **three** independent defects scattered across election, replica selection, and
crash-recovery under **all-or-nothing** scoring — the weaker model tends to fix one or two
and miss the third (typically the replica-fill leak, which only manifests when zones are
exhausted and `k` exceeds the distinct-zone count, or the durable off-by-one, which only
manifests across a restart).

## Validation

Re-validation pending for this revision (pivoted from the too-easy from-scratch shape to
debug-in-place). Local checks: the shipped broken program builds and fails 16 of 55 tests;
the reference fix (`solution/solve.sh`) passes 55/55. Re-run the online balance gate
(avocado + opus/gpt refs) before landing.

See `instruction.md` for the agent-facing task (buggy program + failing examples).
