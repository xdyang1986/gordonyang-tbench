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
- **Grading:** `tests/test_outputs.py` builds a *fresh* xUnit project under `/tmp`
  that project-references the agent's library and runs the canonical 10-scenario
  suite, parsing the `.trx` so each scenario is its own pytest case. The grading
  project lives outside `/app`; the harness injects `tests/` only at verification
  time (after the agent's run), so the agent never sees them. Test-only frameworks
  restore from NuGet at verification time (`allow_internet = true`) — none ship in
  the image.
- **Oracle:** `solution/solve.sh` writes the reference cluster (synchronous quorum,
  tombstones, `(epoch,seq)` versioning, `Settle` anti-entropy, manual failover).

## Completion Rates

Out of K=5 trials each (calibration target: Avocado or Opus must pass ≥1 and fail ≥1
out of 5), graded against the 10-scenario suite.

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 10/10 scenarios pass; 1/1 and 3/3 trials pass (deterministic) |
| Sonnet 4.6 | **0/5 passed** (mean 0.000) — informational only |
| Opus 4.6 | **2/5 passed** (mean 0.400) |
| Avocado | **5/5 passed** (mean 1.000) |

> Calibration target met via **Opus 4.6 (2/5)** — passes ≥1 and fails ≥1 of 5.
> Avocado solves the task (5/5), confirming it is cleanly solvable from the spec.
> All failures are genuine behavioral gaps — no crashes, no plan-mode dropouts; the
> oracle is deterministic (3/3).

> Novelty check: **LOW** contamination risk (see `docs/plans/`). The design omits the
> canonical, heavily-memorized Raft machinery (timeout election, replicated log,
> RequestVote/AppendEntries) in favor of a static leader, manual epoch failover, and
> `Settle` anti-entropy — so a memorized Raft/Dynamo solution cannot pass.

## Model Analysis

Every trial compiled and ran (one Sonnet trial compiled but failed all scenarios at
runtime — see below), so all failures are **behavioral**, not setup/harness artifacts.

### Opus 4.6 — 2/5 passed
- 2 trials passed all 10 scenarios.
- 3 trials failed **only** `Unregistered_value_type_is_rejected_registered_round_trips`:
  the implementation does not enforce the **registry allow-list on the write path** —
  an unregistered value type is committed instead of the write being rejected
  (`Set` should return `false`). Every hard distributed scenario — quorum
  commit/reject, no-local-apply, tombstone no-resurrection, anti-entropy catch-up,
  failover ordering, epoch-beats-seq, conflict resolution — **passed** in all trials.

### Avocado — 5/5 passed
- Passed all 10 scenarios in every trial, including the allow-list case. Confirms the
  task is solvable from the provided spec.

### Sonnet 4.6 — 0/5 passed (informational)
- 4 trials failed **only** the allow-list scenario (the same gap as Opus).
- 1 trial compiled but failed **all 10** scenarios — a pervasive correctness bug
  (the cluster never replicates/commits correctly), i.e. a weaker attempt that didn't
  realize the contract.

### Dominant failure mode (across all models)
**Registry allow-list enforcement on writes** — Opus 3/3 failing trials + Sonnet 4/5
trials. The spec requires replicated values to be on the allow-list (instruction
point 5) and writes to fail via a `bool` return (points 6 & 8); an unregistered value
must therefore be **rejected** (`Set` → `false`). Models implement the consensus
machinery correctly but skip enforcing this gate on the replication path. It is a
genuine spec-adherence/reasoning gap, not a task-setup issue: the reference solution
enforces it (an allow-list check before the quorum write), and the harder consensus
scenarios are solved by frontier models — so the task is clearly solvable, and the
differentiation is a specific, clean correctness detail rather than a crash or
ambiguity. (Note: enforcing on the *replication* path, not just locally, is the
subtlety — the value must be rejected before it can be committed to other nodes.)

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
