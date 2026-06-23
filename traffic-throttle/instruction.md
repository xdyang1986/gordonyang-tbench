Implement a traffic throttling package, it will control how frequency each service can read/write based on the customized healthy signal. Use the Go language.

Requirements:
1. Each service has its own configurable limits for how much traffic it may send.
2. Separate read and write limits.
3. Allow rate is controlled by the provided health signal.
4. Two ways to enforce limits. A non-blocking check (proceed now or be rejected) for shedding load, and a blocking call (wait until capacity is available, or give up if cancelled) for pacing work.
5. Brief spikes above the steady rate are tolerated up to a configured limit, while the long-run average stays
  bounded.
6. A service with no configured limits is allowed through rather than blocked, so misconfiguration doesn't take down traffic.
7. Services and their limits can be added or changed while running, and the throttler works correctly under concurrent use by many callers.
8. Each service can enforce a per-service aggregate rate limit on top of its individual per-operation limits, where the aggregate bucket is a token bucket scaled by service health. No partial consumes are allowed.

API Design:

Types
1. Op (with OpRead, OpWrite) - An enum identifying the kind of operation being throttled. Reads and writes are throttled separately per service
2. Limit - rate-limit rule, expressed as a token bucket:
  - Rate — sustained throughput in tokens/second at full health. This is the steady-state cap.
  - Burst — the bucket's capacity: the most tokens that can accumulate.
3. ServiceConfig is a struct:
  - Ops → map[Op]Limit, defining the per-service limits.
  - Total - a Limit for per-service aggregate. Zero means no limit.
4. HealthFunc - a function that returns a float64 score in [0,1] for a given service. each service can have its own health.

Options

Constructor config passed to New:
  - Configs — the service → ServiceConfig map.
  - Health — your HealthFunc. If nil, everything is treated as fully healthy.
  - PollInterval — how often a background goroutine re-reads Health into a cache. If 0, no poller runs (you call Refresh() yourself).
  - RetryInterval — when a bucket is blocked only because health is currently 0, how long Wait sleeps before re-checking. Defaults to
  100ms.
  - Now — injectable clock (func() time.Time); defaults to time.Now. Exists so tests can control time deterministically.

Functions
1. New(opts Options) *Throttler -- constructor
2. Allow(service, op) bool -- The non-blocking gate. Tries to consume 1 token. Returns true (proceed) if a token was available, false (throttle/drop) otherwise.
  Never blocks.
3. AllowN(service, op, n) bool -- Same as Allow but consumes n tokens.
4. Wait(ctx, service, op) error -- The blocking gate. Blocks until 1 token is available, then consumes it and returns nil.
5. WaitN(ctx, service, op, n) error -- Same as Wait but consumes n tokens.
6. Register(service, cfg) -- Adds or replaces a service's config at runtime.
7. Refresh() -- Forces an immediate re-evaluation of Health for every registered service, updating the cache.
8. Close() -- Stops the background poller and waits for it to exit.

Notes:
1. Implement the package as Go in the /app directory. And don't pause for a plan.
