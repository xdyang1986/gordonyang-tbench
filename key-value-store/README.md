# codimango/key-value-store

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
  that project-references the agent's library and runs the canonical 14-fact
  suite, parsing the `.trx` so each fact is its own pytest case. The grading
  project lives outside `/app`, so nothing the agent edits under `/app/tests`
  can influence the result. `[verifier].environment_mode = "separate"` keeps the
  oracle/tests out of the agent's container during its run.
- **Oracle:** `solution/solve.sh` writes the reference `KeyValueStore.cs`.

## Completion Rates

Out of K trials each (calibration target: Avocado or Opus must pass ≥1 and fail ≥1
out of 5).

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 14/14 facts pass; 1/1 and 3/3 trials pass (harness, plus local offline `--network none`) |
| Sonnet 4.6 | **0/5 passed** (mean 0.000) — informational only |
| Opus 4.6 | **3/5 passed** (mean 0.600) |
| Avocado | **4/5 passed** (mean 0.800) |

> Calibration target met: **both** Opus 4.6 (3/5) and Avocado (4/5) pass ≥1 and
> fail ≥1 of 5. Oracle validated via the harness and locally (`docker build` +
> grading project, `--network none`). Sonnet rates are informational and not part
> of validation.

## Model Analysis

Every trial across all three models **compiled** against the hidden suite (the
public API is pinned in `instruction.md`), so all failures are **behavioral**.

### Opus 4.6 — 3/5 passed
- Trials 1, 4, 5 passed all 14 facts.
- Trials 2 and 3 each failed the **same 6** facts (passed the other 8):
  `Snapshot_then_Load_reproduces_primitive_data`,
  `Snapshot_then_Load_round_trips_custom_key_and_value_types`,
  `Null_values_round_trip`, `Snapshot_overwrites_previous_snapshot`,
  `Load_replaces_existing_contents`, `Load_throws_for_unregistered_type` — all via
  `System.IO.DirectoryNotFoundException` (see dominant mode below).

### Avocado — 4/5 passed
- 4 trials passed all 14 facts.
- 1 trial failed exactly one fact: `Snapshot_throws_for_unregistered_value_type`.
  This implementation validated the type registry **eagerly in `Set()`** instead
  of **lazily in `Snapshot()`**, so the unregistered-`Person` `Set` threw before
  the test reached `Assert.Throws(() => store.Snapshot(...))`. A real lazy-vs-eager
  design decision; the canonical contract defers the check to persist time.

### Sonnet 4.6 — 0/5 passed (informational)
- All 5 trials compiled and passed the same 8 CRUD/registry facts, and all 5
  failed the **same 6** file-writing facts via `DirectoryNotFoundException` — the
  identical gap that sank Opus's two failures, just hit on every trial.

### Dominant failure mode (across all models)
Of the failures observed, **7 of 8 failing trials** (Opus 2/2 + Sonnet 5/5) fail
on one and the same reasoning gap: **a robust "write a snapshot to a path" must
create the parent directory as part of the atomic temp-file + move write.** The
suite snapshots to a nested path (`<temp>/kvdb-tests/<guid>.json`), so an
implementation that writes the `.tmp` file without `Directory.CreateDirectory`
throws `DirectoryNotFoundException` on every fact that writes-then-reads. The one
remaining failing trial (Avocado) is the distinct lazy-vs-eager validation gap
above. Both are genuine reasoning/robustness gaps, not task-setup issues: the spec
is to write a consistent snapshot to an arbitrary path, the reference solution
handles it (`Directory.CreateDirectory` + temp+move; lazy validation at snapshot
time), and stronger models get it right (Opus 3/5, Avocado 4/5) — so it is clearly
solvable from the provided context.

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
