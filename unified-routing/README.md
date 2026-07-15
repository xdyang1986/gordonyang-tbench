# codimango/unified-routing

## Task Overview

Build, from scratch in Go (standard library only), a **concurrency-safe** routing library `router` (a
package — no CLI, no main). The agent starts with an empty `/app/src` and must export exactly:

```go
type Provider struct { ID string; Capacity int }
func New(providers []Provider) *Router
func (r *Router) Route() (string, bool)
func (r *Router) WaitRoute(ctx context.Context) (string, bool, error)
func (r *Router) Remaining(id string) int
func (r *Router) Snapshot() map[string]int
func (r *Router) Release(id string) bool
func (r *Router) AddProvider(p Provider) bool
func (r *Router) RemoveProvider(id string) bool
func (r *Router) BatchRoute(n int) ([]string,bool)
func (r *Router) Close() bool
```

Behavior implied by names and typical concurrent resource router semantics. No exhaustive edge-case table in instruction — agent must infer correct return values for empty nil duplicate negative zero batch close races context races add remove races snapshot independence idempotence capacity caps replacement semantics waiter fairness spurious wakeups linearizability.

Core requirement: under heavy mixed concurrency capacity never exceeded negative or lost, sum invariant holds, no data races no deadlocks no lost wakeups snapshot consistent readers scale.

## What makes this hard

Instruction is deliberately minimal — no hint about critical section, cond var protocol, wakeup targets, snapshot copy strategy, close state machine, return booleans for edge cases, tie-break policy details beyond names, or performance expectations. Agent must discover from first principles or fail hidden tests.

Extreme implicit dimensions:
- **Blocking WaitRoute with context and spurious wakeups**: must implement sync.Cond correctly, loop on predicate, helper goroutine broadcasting on ctx.Done to avoid lost wakeup, no busy spin, handle spurious wakeups from unrelated Add with 0 capacity or Remove, handle cancel-before-call race, handle close race with capacity existing, handle fairness under many waiters.
- **Snapshot consistency and independence**: must return deep copy under RLock, reflect point-in-time atomic state not torn across map iteration, must not contain removed keys after Remove, must reflect added keys after Add, must be independent so mutating returned map does not affect router nor future snapshots.
- **Close lifecycle extreme**: idempotent Close returns true once then false, concurrent Close callers exactly one true, wakes all waiters, after close Route false Batch false Add new false Release false WaitRoute unblocks false nil immediately even with capacity, Add existing returns true but still no assignment allowed, Remaining and Snapshot still work.
- **Reader scalability implicit**: Remaining and Snapshot must use RLock allowing concurrent readers; test runs 256 readers with writers under race detector expecting completion, penalizing global exclusive mutex misuse though wall-clock check removed to avoid flake still stresses race detector.
- **Dynamic membership races**: AddProvider duplicate last-wins order stability, negative capacity treated as zero, replacing discards old remaining and updates orig cap, Remove non-existent false, Add after close semantics, Remove during WaitRoute must not panic.
- **Batch edge cases implicit**: Batch 0 and negative return empty slice true not nil false; Batch after close nil false; Batch atomic all-or-nothing with no partial observable state even under concurrent Snapshot sampling.
- **Release cap enforcement**: Release beyond original capacity returns false no change no wake; Release unknown false; Release after close false.
- **WaitRoute fairness and lost wakeup**: 30 waiters test ensures broadcast wakes all eventually, no starvation, no deadlock on spurious wakeup from Add 0 or Remove.
- **Context race**: WaitRoute with context already cancelled before call must return promptly with error, not block; WaitRoute with cancel racing Add must resolve to one valid outcome without deadlock or panic or goroutine leak.
- **New edge cases**: nil and empty slice work, duplicate IDs last wins, negative capacity zero, input slice deep copied not aliased, order deterministic.
- **34 hidden subtests run 20 times each under race detector** — fluke pass probability astronomically low for partial lock, missed broadcast, torn snapshot, non-idempotent close, wrong return booleans, non-atomic batch, unsynchronized map, missing copy, wrong tie-break, busy spin, deadlock, or performance regression.

Getting correct cond var protocol with context helper goroutine, snapshot deep copy under RLock, close idempotence with broadcast, wakeup on exactly Release/Add>0/Close not on irrelevant ops but loop tolerant to spurious, atomic batch under write lock, copy-on-New, last-wins duplicate handling, negative→0, largest-remaining tie-break lex smallest, RWMutex read/write split, no deadlock under mixed workload including spurious wakeup test, fairness test, close race test, context race test — all at once from minimal spec — is what model must reason about.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): copy agent package, drop hidden Go test, run `go test -race -count=20 -timeout 600s`.
  Hidden tests now 34 subtests covering:
  * New copies input, duplicate last wins, negative→0, empty nil handling
  * Basic ordering largest-remaining tie-break single thread, BatchRoute order determinism
  * Release/Add/Remove semantics cap enforcement return bools, Add replace resets remaining, negative capacity, remove nonexist false
  * Snapshot independent deep copy, consistent under concurrent mutation, after remove/add reflects correct keys, no torn state
  * Close idempotent, concurrent Close exactly one true, wakes waiters, post-close semantics for Route Batch Add Release WaitRoute
  * WaitRoute basic wake on Add, wake on Release, cancel returns error promptly, cancel before call immediate, close unblocks, after close with capacity returns false nil immediately, spurious wakeup tolerance via Add 0 and Remove, fairness under many waiters, context race with Add
  * Batch atomicity concurrent Batch50 vs Route, Batch edge 0 negative, Batch after close, Batch partial visibility stress via Snapshot sampling
  * Concurrent NoOverspend 128 goros 5×2000 ×5 attempts exact totals
  * Concurrent Tight 256 goros 100 cap ×10 attempts exact 100
  * Mixed workload with Route Remaining Snapshot Release Add Remove Batch WaitRoute Close context, intermediate bounds, final strict invariant
  * Performance readers 256 concurrent Snapshot+Remaining with writers under race detector stress
  * Heap performance 1000 providers 5000 Route ops under race
  * Stdlib-only, go build
- **Reference solution** (`solution/solve.sh`): stdlib-only router using `sync.RWMutex` + `sync.Cond`, maps remaining orig, sorted ids slice rebuilt on membership change, copy-on-New, selectLocked scanning largest remaining tie lex, RLock for Remaining Snapshot returning deep copy, Lock for mutations with Broadcast on Release Add>0 Close and context cancel helper, WaitRoute loop checking closed ctx Err capacity, Close idempotent broadcast, AddProvider bool returns false only for new after close else true and updates, Remove bool, BatchRoute all-or-nothing under lock with rollback on fail, Route non-blocking check closed.
- **Environment**: ubuntu:24.04 + golang-go; /app/src empty.

## Completion Rates

Latest validation run — **passing**: oracle 5/5 (Docker, reliable), avocado (metacode) 2/5, opus 2/5.
Difficulty is graded and genuine — both models consistently miss post-close semantics
(Route/WaitRoute/BatchRoute after Close), the empty/zero batch return, and close/add races (real logic
failures under `go test -race -count=20`, not timing flakes). The core selection rule
(largest-remaining, lexicographic tie-break) and duplicate last-wins are stated explicitly so those are
not the source of failure; the concurrency lifecycle edges are what separate a correct implementation
from a plausible one.

## Model Analysis

Difficulty now spans ultra-vague specification inference + concurrency design + condition variable protocol with context helper goroutine + snapshot deep copy consistency + close lifecycle state machine with idempotence and concurrent callers + spurious wakeup tolerance + fairness under many waiters + atomic batch edge cases + duplicate last-wins order stability + negative capacity handling + empty nil handling + performance RWMutex split. Model must synthesize correct behavior for 20+ implicit edge return values never spelled out in instruction, purely from method names and typical semantics, then implement deadlock-free wakeup-correct linearizable code passing 34 hidden checks under race detector repeated 20 times.

## Anti-Cheating Analysis

- Hardcoded outputs impossible: grading runs real package under race detector with randomized scheduling, blocking wakeup timing including spurious wakeups, context cancellation races, close races, snapshot consistency checks, exact invariants, return boolean checks for edge cases.
- Hidden test not shipped; instruction deliberately minimal to prevent overfitting to explicit edge table.
- Grader copies agent package and adds hidden tests.
- Bypassing fails on race, deadlock timeout/hang, wakeup hang, torn snapshot mismatch, close race wrong boolean, context leak/hang, spurious wakeup mishandling, batch atomicity, snapshot independence, or edge return mismatch.
