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
*connected* nodes. A model that pattern-matches a single-leader "replicate to all
reachable" design passes the easy convergence cases but fails the quorum-reject,
no-resurrection, failover-ordering, and epoch-vs-seq scenarios.

## How it is built / graded

- **Starter code:** the `KeyValueDb` library project with only the `TypeRegistry`
  helper (the serialization allow-list). The agent writes the cluster from scratch
  under `src/KeyValueDb/`. The agent never sees the test suite.
- **Grading:** `tests/test_outputs.py` builds a *fresh* .NET **console** grading
  program under `/tmp` that project-references the agent's library and runs the
  canonical 11-scenario suite, printing one `SCENARIO <name> PASS/FAIL` line each
  (parsed so every scenario is its own pytest case). The grader is SDK-only (no test
  framework, no external packages), so it builds and runs **fully offline** — no
  NuGet access is needed at verification time. The grading project lives outside
  `/app`; the harness injects `tests/` only at verification time (after the agent's
  run), so the agent never sees them.
- **Oracle:** `solution/solve.sh` writes the reference cluster (synchronous quorum,
  tombstones, `(epoch,seq)` versioning, `Settle` anti-entropy, manual failover).

## Completion Rates

Out of K=5 trials each (calibration target: Avocado or Opus must pass ≥1 and fail ≥1
out of 5), graded against the 11-scenario suite.

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 11/11 scenarios pass; 1/1 and 3/3 trials pass (deterministic; also verified offline via `docker run --network none`) |
| Sonnet 4.6 | **0/5 passed** (mean 0.000) — informational only |
| Opus 4.6 | **3/5 passed** (mean 0.600) |
| Avocado | **4/5 passed** (mean 0.800) |

> Calibration target met by **both** Opus 4.6 (1/5) and Avocado (4/5) — each passes
> ≥1 and fails ≥1 of 5. All failures are genuine behavioral gaps — no crashes, no
> plan-mode dropouts; the oracle is deterministic (3/3).

> Novelty check: **LOW** contamination risk (see `docs/plans/`). The design omits the
> canonical, heavily-memorized Raft machinery (timeout election, replicated log,
> RequestVote/AppendEntries) in favor of a static leader, manual epoch failover, and
> `Settle` anti-entropy — so a memorized Raft/Dynamo solution cannot pass.

## Model Analysis

Every trial compiled and ran (one Sonnet trial compiled but failed all scenarios at
runtime — see below), so all failures are **behavioral**, not setup/harness artifacts.

### Opus 4.6 — 3/5 passed
- 3 trials passed all 11 scenarios.
- 2 trials failed **only** `Unregistered_value_type_is_rejected_registered_round_trips`:
  the implementation does not enforce the **registry allow-list on the write path** —
  an unregistered value type is committed instead of the write being rejected
  (`Set` should return `false`). Every hard distributed scenario — quorum
  commit/reject, no-local-apply, follower forwarding, tombstone no-resurrection,
  anti-entropy catch-up, failover ordering, epoch-beats-seq, conflict resolution —
  **passed** in these trials.

### Avocado — 4/5 passed
- 4 trials passed all 11 scenarios.
- 1 trial failed **only** the allow-list scenario (the same gap as Opus). Confirms the
  task is solvable from the spec while still exposing the dominant gap.

### Sonnet 4.6 — 0/5 passed (informational)
- 3 trials failed **only** the allow-list scenario — the consensus machinery was
  implemented correctly, but the allow-list gate was skipped.
- 2 trials compiled but failed **all 11** scenarios — a pervasive correctness bug
  (the cluster never replicates/commits correctly), i.e. weaker attempts at the contract.

### Dominant failure mode (across all models)
**Registry allow-list enforcement on the replication path** — Opus 2 failing trials +
Avocado 1 + Sonnet 3. The spec requires replicated values to be on the allow-list
(instruction rule 5) and writes to fail via a `bool` return (rules 6 & 8); an
unregistered value must therefore be **rejected** (`Set` → `false`) *before* it can be
committed to other nodes. Models implement the consensus machinery correctly but skip
enforcing this gate on the write/replication path. It is a genuine
spec-adherence/reasoning gap, not a task-setup issue: the reference solution enforces
it (an allow-list check before the quorum write), and the harder consensus scenarios
are solved by frontier models — so the task is clearly solvable, and the
differentiation is a specific, clean correctness detail rather than a crash or
ambiguity. The remaining surface (quorum-reject with no-local-apply, tombstone
no-resurrection, `(epoch,seq)` conflict resolution where higher epoch beats higher
seq, follower forwarding) is exercised by the suite and occasionally trips weaker
attempts (one Opus trial failed everything).

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
