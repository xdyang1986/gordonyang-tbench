# codimango/key-value-store-with-snapshot (distributed)

## Description

The agent implements, from scratch in C# (.NET 8), an **in-process distributed
key/value store** with **leader + quorum** replication. The cluster is N replica
nodes wired by a test-controlled in-memory transport (no sockets/timers/threads on
the critical path → deterministic grading). The public contract — the
`IReplicatedKvCluster` interface and the `DistributedKv.CreateCluster` factory — is
pinned in `instruction.md`; the agent reconstructs it and the hidden scenario suite
compiles against it.

The store must get a pile of distributed-systems details simultaneously right:
**synchronous quorum writes** (commit only on a strict majority, counting the
leader); a **rejected write applies nowhere — not even on the leader** (no
read-your-write on a leader stuck in a minority partition); **versioned tombstones**
so a delete is never resurrected by a stale replica during sync; an **`(epoch, seq)`
conflict rule** where a higher epoch beats a higher seq; **manual, epoch-bumping
failover**; and **`Settle()` anti-entropy** that converges only the currently
*connected* nodes. On top of the consensus core it adds two harder capabilities:
**`Snapshot`/`Restore`** (serialize a node's committed state — values, tombstones, and
`(epoch,seq)` versions, with types carried by registry name — and rebuild it elsewhere)
and **`QuorumGet`** (a linearizable read that needs a reachable majority and **read-repairs**
lagging nodes). A model that pattern-matches a single-leader "replicate to all reachable"
design passes the easy convergence cases but fails the quorum-reject, no-resurrection,
failover-ordering, epoch-vs-seq, snapshot, and quorum-read scenarios.

## How it is built / graded

- **Starter code:** the `KeyValueDb` library project with only the `TypeRegistry`
  helper (the serialization allow-list). The agent writes the cluster from scratch
  under `src/KeyValueDb/`. The agent never sees the test suite.
- **Grading:** `tests/test_outputs.py` builds a *fresh* .NET **console** grading
  program under `/tmp` that project-references the agent's library and runs the
  canonical 32-scenario suite, printing one `SCENARIO <name> PASS/FAIL` line each
  (parsed so every scenario is its own pytest case). The grader is SDK-only (no test
  framework, no external packages), so it builds and runs **fully offline** — no
  NuGet access is needed at verification time. The grading project lives outside
  `/app`; the harness injects `tests/` only at verification time (after the agent's
  run), so the agent never sees them.
- **Oracle:** `solution/solve.sh` writes the reference cluster (synchronous quorum,
  tombstones, `(epoch,seq)` versioning, `Settle` anti-entropy, manual failover).

## Completion Rates

Commit `36c046d`, 32-scenario suite. Platform validation **passes**: balance check
*"avocado not trivial and ≥1 agent solved."* Oracle 3/3, AI assessment **Accept**
(0 Crit / 0 High / 1 Med / 1 Low), contamination MEDIUM, novelty LOW.

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 3/3 (deterministic; full suite verified **32/32** offline via `docker run --network none`) |
| gpt-5.5 | 5/5 — solves it |
| Avocado | **3/5** — genuine mix (solvable but non-trivial) |
| Opus 4.6 | mixed (≥1 fail) |

The task is solvable (gpt-5.5 5/5, oracle 3/3) yet non-trivial (avocado 3/5), so it
discriminates **without any unstated traps** — every observed failure is on a behavior the
rules state explicitly.

## Why models fail (current)

Every observed failure is on a **stated** requirement — a genuine implementation/reasoning gap,
not a spec/test-alignment guess. The dominant failure modes (from avocado's failing trials):

- **Quorum read requires a reachable majority (rule 11).** A model returns a possibly-stale local
  value instead of throwing when no majority is reachable —
  `Quorum_read_fails_without_a_majority`, `Quorum_read_majority_threshold_in_four_node_cluster`.
- **Read-repair (rule 11).** `QuorumGet` must bring reachable lagging nodes up to the newest version
  (including propagating a tombstone), observable without a `Settle()`; models return the right
  value but skip the repair — `Quorum_read_of_deleted_key_throws_and_propagates_tombstone`.
- **Snapshot round-trip of custom registered types (rule 10).** Models build a primitives-only
  snapshot serializer that throws on a registered custom type instead of round-tripping it —
  `Snapshot_restore_round_trips_custom_registered_type`, `Restore_rejects_unregistered_type`.

The core consensus machinery (quorum commit/reject, no-local-apply, tombstone no-resurrection,
`(epoch,seq)` resolution where higher epoch beats higher seq, manual failover, `Settle`
anti-entropy) is implemented correctly by the strong models — the differentiation comes from the
harder snapshot and quorum-read capabilities layered on top.

> Novelty check: **LOW** recall risk (see `.review/novelty-report_*.md`). The design omits the
> canonical, heavily-memorized Raft machinery (timeout election, replicated log,
> RequestVote/AppendEntries) in favor of a static leader, manual epoch failover, and
> `Settle` anti-entropy — so a memorized Raft/Dynamo solution cannot pass.

## Anti-Cheating Analysis

- **Hardcoded outputs:** grading runs a behavioral scenario suite (quorum
  commit/reject, partition/heal convergence, tombstone no-resurrection, failover
  ordering) against the compiled library — there is no fixed output string to print.
- **Overfitting to visible tests:** the agent never sees the suite. The harness
  injects it under `/tests/grading/` only at verification time, and grading happens
  in a fresh `/tmp` project the agent cannot reach.
- **Modifying test files:** the grader ignores `/app/tests`; it constructs its own
  project from the hidden scenario file and only project-references the agent's
  library, so edits under `/app` can't alter the assertions.
- **Bypassing the intended solution path:** only the observable behavior of the
  cluster through its public contract is graded. The agent must actually implement
  quorum commit, tombstones, `(epoch,seq)` resolution, and `Settle` anti-entropy to
  pass; the exact API names (`IReplicatedKvCluster`, `PromoteLeader`, `Settle`) have
  zero public footprint, so a verbatim match would itself be a red flag.
