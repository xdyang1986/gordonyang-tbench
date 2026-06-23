# codimango/key-value-store-with-snapshot

## Description

The agent is given a real C# (.NET 8) library, `KeyValueDb`, with one file removed:
`src/KeyValueDb/KeyValueStore.cs`. The supporting types (`TypeRegistry`,
`SnapshotModels`), the project README (the design spec), the runnable demo, and the
`.csproj`/solution all remain. The agent must re-implement `KeyValueStore` so that
the library behaves as specified: a single in-memory store that holds
**heterogeneous key and value types** (`int`, `string`, `Guid`, composite
`record` keys, …), is **thread-safe**, and can take a **consistent, atomic JSON
snapshot to disk** and **load it back** through a type allow-list.

It tests whether the model can reconstruct a non-trivial public contract from a
spec rather than from a stub, and get a pile of subtle details simultaneously
right: serializing each value by its *runtime* type (not `object`), the exact
on-disk schema implied by `SnapshotModels` (`Version`, per-entry `KeyType` /
`Key` / `ValueType` / `Value`, `null` values encoded as `null` type+value),
rejecting unregistered types on both save and load, version checking, atomic
temp-file-then-move writes, the correct exception types
(`KeyNotFoundException`, `InvalidOperationException`, `InvalidDataException`,
`FileNotFoundException`), and concurrency that survives parallel writers. A naive
approach (e.g. `Dictionary` + `JsonSerializer.Serialize(_data)`) compiles and
passes the easy CRUD cases but fails the snapshot round-trips, the type-safety
cases, and the concurrency case.

## How it is built / graded

- **Starter code:** the participant's `KeyValueDb` repo, with `KeyValueStore.cs`
  deleted (Dockerfile build step). The agent never sees the test suite.
- **Grading:** `tests/test_outputs.py` builds a *fresh* xUnit project under `/tmp`
  that project-references the agent's library and runs the canonical 24-fact
  suite (CRUD + type-safety, snapshot/load round-trips incl. a 50k-entry
  large-snapshot, an append-only write-ahead log with replay/compaction/truncated-
  tail recovery, and per-entry TTL), parsing the `.trx` so each fact is its own
  pytest case. The grading
  project lives outside `/app`, so nothing the agent edits under `/app/tests`
  can influence the result. `[verifier].environment_mode = "separate"` keeps the
  oracle/tests out of the agent's container during its run.
- **Oracle:** `solution/solve.sh` writes the reference `KeyValueStore.cs`.

## Completion Rates

Out of K=5 trials each (calibration target: Avocado or Opus must pass ≥1 and fail
≥1 out of 5), graded against the 24-fact suite.

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 24/24 facts pass; 1/1 and 3/3 trials pass (harness, plus local offline `--network none`) |
| Sonnet 4.6 | **0/5 passed** (mean 0.000) — informational only |
| Opus 4.6 | **1/5 passed** (mean 0.200) |
| Avocado | **4/5 passed** (mean 0.800) |

> Calibration target met: **both** Opus 4.6 (1/5) and Avocado (4/5) pass ≥1 and
> fail ≥1 of 5. Oracle validated via the harness and locally (`docker build` +
> grading project, `--network none`). Sonnet rates are informational and not part
> of validation.

## Model Analysis

Every trial across all three models **compiled** against the hidden suite (the
public API is pinned in `instruction.md`), so all failures are **behavioral**. All
trials in this run also actually implemented the file — see the note on plan mode
below.

### Opus 4.6 — 1/5 passed
- 1 trial passed all 24 facts.
- The other 4 trials each failed the **same 2** facts (passed the other 22):
  `Get_missing_key_throws` and `Ttl_entry_expires_after_clock_advances`. Root
  cause is a single contract error: `Get` returns `null` for a missing (or
  expired) key instead of throwing `KeyNotFoundException` — e.g.
  `if (_data.TryGetValue(key, out var e) && !IsExpired(e)) return e.Value; return null;`.
  The expired-key TTL assertion fails for the same reason (it expects `Get` to
  throw once the clock passes the entry's expiry).

### Avocado — 4/5 passed
- 4 trials passed all 24 facts.
- 1 trial failed exactly one fact: `Compact_rewrites_log_to_live_state` — the
  append-only log was not rewritten to one record per live key after `Compact()`.

### Sonnet 4.6 — 0/5 passed (informational)
- 4/5 trials failed the **same** `Get_missing_key_throws` +
  `Ttl_entry_expires_after_clock_advances` pair as Opus (the `Get`-returns-`null`
  contract gap).
- 1/5 trial instead failed `Load_throws_for_unregistered_type` plus both
  compaction facts (`Compact_rewrites_log_to_live_state`,
  `Compact_keeps_log_open_for_further_appends`).

### Dominant failure mode (across all models)
The dominant reasoning gap is the **`Get` exception contract**: `Get` must throw
`KeyNotFoundException` for a key that is absent or has expired, while the separate
`TryGet` API is the non-throwing path. Models conflate the nullable `object?`
return (which exists because `null` is a *storable value* — see
`Null_values_round_trip`) with "missing ⇒ return null", and so never throw. This
accounts for **8 of the 9 failing trials** (Opus 4 + Sonnet 4). The secondary gap
is **log compaction** (`Compact` must rewrite the open log to one record per live
key and leave it usable for further appends): Avocado's single failure and one
Sonnet trial. Both are genuine reasoning/robustness gaps, not task-setup issues —
the standard .NET idiom (indexer/`Get` throws, `TryGetValue`/`TryGet` doesn't) is
discoverable from the API list, the reference solution implements both correctly,
and stronger configurations get them right (Avocado 4/5) — so the task is clearly
solvable from the provided context.

> **Plan mode (resolved).** An earlier calibration of this task saw 2/5 Opus
> trials score 0 because the agent drafted a plan, called `ExitPlanMode` to request
> approval, and halted without ever writing `KeyValueStore.cs` — a harness artifact,
> not a reasoning gap. `instruction.md` now instructs the agent to implement the
> files directly without stopping for plan approval; in the run above **every**
> trial across all three models produced an implementation, so all reported
> failures are genuine behavioral gaps.

Other reasoning surface the task exercises (handled correctly by the passing
trials but failure-prone in general): serializing each value by its *runtime* type
to match the `SnapshotModels` schema; encoding `null` values as
`ValueType=null`/`Value=null`; the exact `TypeRegistry` exception contract on
save/load; the snapshot `Version` check; and thread-safe concurrent writes.

## Anti-Cheating Analysis

- **Hardcoded outputs:** grading runs a behavioral xUnit suite (round-trips,
  concurrency, exception types) against the compiled library — there is no fixed
  output string to print.
- **Overfitting to visible tests:** the agent never sees the test suite. It is
  staged only into the separate verifier (`environment_mode = "separate"`) under
  `/tests/grading/`, and graded in a `/tmp` project the agent cannot reach.
- **Modifying test files:** the grader ignores `/app/tests` entirely; it
  constructs its own project from the hidden canonical test file and only
  project-references the agent's library, so edits under `/app` can't alter the
  assertions.
- **Bypassing the intended solution path:** the only thing graded is the
  observable behavior of `KeyValueStore` through its public API; the agent must
  actually implement the store (snapshot format, type allow-list, concurrency) to
  pass. Deleting `KeyValueStore.cs` (rather than stubbing it) means there is no
  partial scaffold to game.
