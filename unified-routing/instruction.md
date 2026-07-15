Scenario
A SaaS gateway routes requests across capacity-limited providers from many goroutines. Providers can be added or removed at runtime, capacity released back, batches assigned atomically, callers block waiting for capacity, snapshots taken, and router closed to drain. Implement the shared core as a Go library correct under concurrency.

Implement from scratch in Go as package (library, no CLI, no main).

Package and API
Create Go module `router` under /app/src with package `router` exporting exactly:

  package router

  import "context"

  type Provider struct { ID string; Capacity int }

  func New(providers []Provider) *Router
  func (r *Router) Route() (string, bool)
  func (r *Router) WaitRoute(ctx context.Context) (string, bool, error)
  func (r *Router) Remaining(id string) int
  func (r *Router) Snapshot() map[string]int
  func (r *Router) Release(id string) bool
  func (r *Router) AddProvider(p Provider) bool
  func (r *Router) RemoveProvider(id string) bool
  func (r *Router) BatchRoute(n int) ([]string, bool)
  func (r *Router) Close() bool

Behavior is implied by names and typical concurrent resource router semantics. New must not alias input. Route picks eligible provider deterministically under sequential use. WaitRoute blocks efficiently without busy spin and respects context and close. Snapshot is point-in-time consistent independent copy. Release restores capacity up to original limit. AddProvider adds or replaces. RemoveProvider removes. BatchRoute is atomic all-or-nothing. Close is idempotent and wakes waiters.
Route and BatchRoute select the eligible provider (remaining capacity > 0) with the largest remaining capacity, breaking ties by the lexicographically smallest id, then decrement that provider.
If New or AddProvider is given an id that already exists, the last capacity given for that id wins.
BatchRoute(n) with n <= 0 returns an empty, non-nil slice and true.
After Close, Route and BatchRoute return false; WaitRoute returns ("", false, nil).

You choose Router fields.

Concurrency Requirement
All methods safe for arbitrary concurrent use. No capacity over-spend negative or loss. No partial batch observable. No lost wakeup no deadlock no busy spin. No data races. No torn snapshot. Close linearizable. Readers scale.

Determinism
Single goroutine sequential behavior deterministic. Concurrent interleaving nondeterministic but invariants hold.

Constraints
Go standard library only. go.mod no external requires. Place under /app/src, go build ./... must succeed. Grader adds hidden _test.go and runs go test -race -count=20 -timeout 600s. No network.

Deliverable
Router package implementing API correct, race-free, deadlock-free, wakeup-correct, snapshot-consistent, performant under heavy concurrent mixed use including edge cases around duplicate ids, negative capacities, close races, context cancellation races, concurrent add remove snapshot, idempotent close, independent snapshot copy, capacity caps on release, and waiter fairness.
