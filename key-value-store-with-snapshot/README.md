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
  canonical 18-scenario suite, printing one `SCENARIO <name> PASS/FAIL` line each
  (parsed so every scenario is its own pytest case). The grader is SDK-only (no test
  framework, no external packages), so it builds and runs **fully offline** — no
  NuGet access is needed at verification time. The grading project lives outside
  `/app`; the harness injects `tests/` only at verification time (after the agent's
  run), so the agent never sees them.
- **Oracle:** `solution/solve.sh` writes the reference cluster (synchronous quorum,
  tombstones, `(epoch,seq)` versioning, `Settle` anti-entropy, manual failover).

## Completion Rates

### Current — PASSING (commit `41eba0b`, 2026-06-26, 22-scenario suite)

Platform validation **passes**: balance check *"avocado not trivial and ≥1 agent solved."*
Oracle 3/3, AI assessment **Accept** (0 Crit / 0 High / 2 Med / 1 Low), contamination MEDIUM.

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 3/3 (deterministic; full suite verified **22/22** offline via `docker run --network none`) |
| gpt-5.5 | **3/5** — healthy mix (passes ≥1, fails ≥1) |
| Avocado | **0/5** — non-trivial |
| Opus 4.6 | mixed (≥1 fail) |

The task is solvable (gpt-5.5 3/5, oracle 3/3) yet non-trivial (avocado 0/5). gpt-5.5
solving it 3/5 from the high-level rule 10 confirms the snapshot/restore semantics are
derivable from the spec + the `TypeRegistry` docstring, not a guess.

<details><summary>How it got here (evolution log)</summary>

1. **Spec fix.** The original 11-scenario suite's only differentiator was *guessing* that a
   null `registry` defaults to `TypeRegistry.CreateDefault()`. Rule 5 now states this, removing
   the unstated-default trap — but that left the task too easy (avocado 5/5, gpt-5.5 5/5).
2. **+4 differentiator scenarios** (even-N quorum, `Settle()` not crossing an active partition,
   higher-epoch tombstone beats stale higher-seq value): still avocado/gpt 5/5 (Opus 4/5).
3. **+3 composed stress scenarios** (multi-key convergence; sequential failovers; higher-epoch
   write reviving a key over a tombstone): still avocado/gpt 5/5.
4. **+ `Snapshot`/`Restore` capability** (rule 10) with 4 scenarios (round-trip; tombstone+recency
   preserved; allow-list enforced on restore; post-restore writes supersede) → **calibration
   passes**: avocado 0/5, gpt-5.5 3/5.

Note on the platform gate: the "Metacode or Opus pass/fail balance" check evaluates as soon as
avocado finishes and does **not** wait for Opus to complete its trials — so an Opus-only pass/fail
mix won't flip a "too easy" verdict; the strong reference models (avocado/gpt-5.5) must not all pass.

</details>

#### Historical — old 11-scenario suite under the original ambiguous spec (for reference only)

| Model | Pass rate (k=5) — STALE |
|-------|-----------|
| Sonnet 4.6 | **0/5 passed** (mean 0.000) — informational only |
| Opus 4.6 | **3/5 passed** (mean 0.600) |
| Avocado | **4/5 passed** (mean 0.800) |

> Under the old spec the calibration target was met by both Opus 4.6 and Avocado (each
> passed ≥1 and failed ≥1 of 5), but only because the allow-list scenario turned on an
> unstated default rather than a genuine reasoning gap (see below). With the default now
> specified, that differentiation disappears.

> Novelty check: **LOW** contamination risk (see `docs/plans/`). The design omits the
> canonical, heavily-memorized Raft machinery (timeout election, replicated log,
> RequestVote/AppendEntries) in favor of a static leader, manual epoch failover, and
> `Settle` anti-entropy — so a memorized Raft/Dynamo solution cannot pass.

## Model Analysis (historical — old 11-scenario suite, original ambiguous spec)

> This section analyses the **original** runs that motivated the re-calibration. For the
> current, passing numbers see **Current — PASSING** under Completion Rates above.

Every trial compiled and ran (one Sonnet trial compiled but failed all scenarios at
runtime — see below), so all failures are **behavioral**, not setup/harness artifacts.

### Opus 4.6 — 3/5 passed
- 3 trials passed all 11 scenarios.
- 2 trials failed **only** `Unregistered_value_type_is_rejected_registered_round_trips`:
  with no registry passed, the cluster accepted an unregistered `Note` value
  (`Set` returned `true` where the grader expects `false`). Every hard distributed
  scenario — quorum commit/reject, no-local-apply, follower forwarding, tombstone
  no-resurrection, anti-entropy catch-up, failover ordering, epoch-beats-seq, conflict
  resolution — **passed** in these trials.

### Avocado — 4/5 passed
- 4 trials passed all 11 scenarios.
- 1 trial failed **only** the allow-list scenario (the same gap as Opus).

### Sonnet 4.6 — 0/5 passed (informational)
- 3 trials failed **only** the allow-list scenario — the consensus machinery was
  implemented correctly.
- 2 trials compiled but failed **all 11** scenarios — a pervasive correctness bug
  (the cluster never replicates/commits correctly), i.e. weaker attempts at the contract.

### Dominant failure mode (across all models) — an unstated default, not a reasoning gap
Across every non-degenerate trial the consensus machinery (quorum commit/reject,
no-local-apply, follower forwarding, tombstone no-resurrection, `(epoch,seq)`
resolution, manual failover, anti-entropy) was implemented **correctly** and passed.
The **only** scenario that ever distinguished pass from fail was the allow-list one,
and the observable failure (`Set` returning `true` for an unregistered `Note`) does
**not** prove the allow-list was skipped — it is equally explained by the
implementation defaulting a **null `registry` to a permissive value** instead of
`TypeRegistry.CreateDefault()`. The old `instruction.md` never said which default to
use, and the grader's allow-list scenario constructs the cluster with no registry, so
passing it hinged on *guessing* the reference's choice (`registry ??
TypeRegistry.CreateDefault()`). That was a **spec/test-alignment gap**, not a
distributed-systems reasoning gap: a model could get all the hard consensus logic right
and still fail purely on the unstated default. **Resolution:** rule 5 was amended to state
the null default explicitly. That removed the sole differentiator and made the task too
easy (confirmed: avocado 5/5, gpt-5.5 5/5), so it was **re-calibrated** — adding the
differentiator and stress scenarios plus the `Snapshot`/`Restore` capability — and the task
now **passes** calibration (avocado 0/5, gpt-5.5 3/5). See **Current — PASSING** under
Completion Rates above.

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
