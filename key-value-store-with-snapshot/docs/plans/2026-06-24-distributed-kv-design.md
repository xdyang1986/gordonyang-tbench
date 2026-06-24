# Distributed Key-Value Store (leader + quorum) — Task Redesign

**Date:** 2026-06-24
**Planning skill:** brainstorming
**Task folder:** `key-value-store-with-snapshot/` (replace in place)
**Status:** design agreed; pending novelty check before build

## Why

The single-node task became too easy once the `Get`-fails contract was spelled
out in `instruction.md` (Opus 5/5, Sonnet 5/5; only Avocado failing, via process
crashes). To restore genuine difficulty, evolve it into a **distributed, multi-node
replicating store** with leader + quorum semantics.

## Locked decisions

1. **Execution model:** in-process cluster of N replicas wired by a test-controlled
   in-memory transport. No sockets, timers, or threads on the critical path →
   deterministic grading.
2. **Consistency:** static (configured) **leader + quorum writes**. Deterministic,
   **test-triggered failover** (no timing-based election → avoids flakiness and the
   high contamination risk of a Raft reimplementation).
3. **Feature surface:** slim core — typed `Set/Get/Remove/ContainsKey/Count` +
   `TypeRegistry` (replicated values serialize via the allow-list). DROP TTL and the
   WAL (OpenLog/Compact). Snapshot repurposed as follower state-transfer / anti-entropy.
4. **Testing:** scenario-based against a **thin driver seam** — assert observable
   behavior (commit/reject, convergence, no-resurrection), not method signatures or
   specific exception types.

## Architecture

### Two-phase determinism model
- **Synchronous quorum on the write path.** `Set`/`Remove` on the leader replicate
  synchronously to currently-reachable followers and commit iff a strict majority
  (⌊N/2⌋+1, counting the leader) acknowledges. If a majority is unreachable, the
  write **fails** — no commit, no local apply. Resolves immediately; no `Settle`
  needed for a write to decide.
- **`Settle()`-driven anti-entropy.** A partitioned follower misses commits and is
  stale. After `Heal()`, `Settle()` reconciles: for each key the higher `(epoch,seq)`
  wins (tombstones included) until all connected nodes converge. Deterministic, no
  polling.

### Versioning
Every entry carries `(epoch, seq)`. `seq` = leader's monotonic per-write counter;
`epoch` bumps on `PromoteLeader`. Ordering / conflict resolution = highest
`(epoch, seq)` wins — also makes post-failover writes supersede stale data.

## The seam (the entire pinned surface)

```csharp
IReplicatedKvCluster DistributedKv.CreateCluster(int nodes, int leaderId, TypeRegistry? registry = null);

interface IReplicatedKvCluster {
    void Set(int nodeId, object key, object? value);   // quorum write (followers forward to leader)
    object? Get(int nodeId, object key);               // local committed view; fails on miss
    bool Remove(int nodeId, object key);               // quorum tombstone
    bool ContainsKey(int nodeId, object key);
    int  Count(int nodeId);
    void Partition(params int[] ids);                  // isolate this set from the rest
    void Heal();
    void PromoteLeader(int nodeId);
    void Settle();                                     // anti-entropy to convergence
    int  LeaderId { get; }
}
```
Everything else (transport, node classes, versioning internals) is the agent's free
internal design.

## Replication semantics (the contract)

1. **Quorum commit** = strict majority of all nodes, counting the leader. Below
   quorum → no commit, no local apply, write fails.
2. **Tombstones** — deletes replicate as versioned tombstones so anti-entropy can't
   resurrect a key on a stale node.
3. **Anti-entropy** (`Settle`) — higher `(epoch, seq)` wins across connected nodes,
   tombstones included; converges all connected nodes.
4. **Failover** — `PromoteLeader` bumps `epoch`; later writes supersede lower-epoch data.
5. **Serialization** — replicated values go through `TypeRegistry`; an unregistered
   type fails the write.

## Scenario suite (each = one fact; observable assertions, no exception-type pinning)

1. Replicate & converge — write → `Settle` → all nodes read it.
2. Quorum commit (minority down) — partition 1/3; write commits; healed node catches up.
3. Quorum reject (majority lost) — leader in minority; write rejected; no node (incl.
   leader) shows it.
4. No-resurrection — delete commits while a node is partitioned; heal+`Settle`; stale
   node stays deleted.
5. Anti-entropy catch-up — partitioned follower misses many writes; heal+`Settle` →
   converges.
6. Failover ordering — `PromoteLeader` (epoch bump); new writes supersede stale data.
7. Conflict resolution — concurrent writes both sides of a partition; heal+`Settle` →
   deterministic `(epoch,seq)` winner everywhere.
8. Replication serialization — unregistered custom type → write rejected; registered →
   round-trips.
9. **No-local-apply-on-reject** (top contamination discriminator) — write to the leader
   while it's stuck in a minority partition returns failure AND leaves the leader's own
   local state unchanged (no read-your-write on the leader). Punishes "just do Raft".
10. **Epoch beats higher seq** — promote a new leader (epoch++) while the old leader
    still holds a higher `seq` from the previous epoch; `Settle()`; assert the new-epoch
    value wins despite its lower `seq`. Forces reading the `(epoch,seq)` tie-break rule.

## Novelty check (2026-06-24)
Result: **LOW** contamination risk. Zero exact-symbol matches on GitHub; closest public
work (MIT 6.824 KV labs) is Go + full Raft and omits this design's static leader,
manual epoch failover, `Settle()` anti-entropy, and no-local-apply-on-reject rule.
Hardening applied: scenarios 9 and 10 above; keep exact API names (zero public
footprint); keep `Partition`/`Heal` framing distinct from 6.824 `labrpc`.

## Authorship split (AAI policy)

**3P (Claude) builds — none agent-visible:**
- `solution/solve.sh` (reference cluster implementation / oracle)
- `tests/` (scenario suite + `test_outputs.py`, fresh `/tmp` grading project pattern)
- Dockerfile **recipe** (build steps; delete the impl file the agent must write)
- `task.toml`, task `README.md`

**Participant authors — agent-visible content (same rule class as instruction.md):**
- `instruction.md` (distributed spec/semantics)
- Seam starter file(s): `IReplicatedKvCluster` interface + `DistributedKv.CreateCluster`
  stub (shipped so the agent's names match the harness exactly)
- Kept/edited `TypeRegistry.cs`, rewritten project `README.md` design spec, demo

**Coordination point:** `solve.sh` and the tests compile against the *same* interface
the participant ships — the seam signature above must match verbatim.

## Calibration outlook
Genuinely hard: quorum-reject-without-local-apply, no-resurrection, failover ordering,
and conflict convergence are classic traps. Good differentiation potential; calibrate
after building.

## Risks
1. **Novelty** — leader+quorum KV is far less memorized than Raft, but run the novelty
   checker before investing.
2. **Scope vs agent timeout** (1800s) — large implementation; monitor.
3. **Determinism** — handled by synchronous-quorum + `Settle` anti-entropy.

## Key decisions (approach selection)
- In-process over multi-container/localhost — determinism, no Harbor networking limits.
- Static leader + quorum over full Raft election — gradeable + novel (Raft is memorized).
- Slim core over keep-everything — buildable within agent timeout; focus on consensus.
- Thin driver seam + scenario tests over a large pinned API — test behavior, not signatures.
