package throttle

import (
	"context"
	"sync"
	"time"
)

// Op identifies the kind of operation being throttled. Reads and writes are
// throttled separately per service.
type Op int

const (
	OpRead Op = iota
	OpWrite
)

// Limit is a token-bucket rate-limit rule.
type Limit struct {
	// Rate is the sustained throughput in tokens/second at full health.
	Rate float64
	// Burst is the bucket capacity: the most tokens that can accumulate.
	Burst float64
}

// ServiceConfig defines the limits for a single service: a per-op limit map and
// an optional per-service aggregate limit (Total). The zero value of Total
// (Rate == 0 && Burst == 0) means there is no aggregate limit.
//
// An operation must take tokens from BOTH its per-op bucket and the aggregate
// bucket atomically: if either is short, neither is consumed.
type ServiceConfig struct {
	Ops   map[Op]Limit
	Total Limit
}

// HealthFunc returns a health score in [0,1] for a given service.
type HealthFunc func(service string) float64

// Options is the constructor config passed to New.
type Options struct {
	Configs       map[string]ServiceConfig
	Health        HealthFunc
	PollInterval  time.Duration
	RetryInterval time.Duration
	Now           func() time.Time
}

type bucket struct {
	rate   float64
	burst  float64
	tokens float64
	last   time.Time
}

type serviceState struct {
	ops    map[Op]*bucket
	total  *bucket // nil if the service has no aggregate limit
	health float64
}

// Throttler enforces per-service, per-op token-bucket limits (plus an optional
// per-service aggregate) scaled by health.
type Throttler struct {
	mu       sync.Mutex
	configs  map[string]ServiceConfig
	services map[string]*serviceState

	healthFn HealthFunc
	now      func() time.Time
	retry    time.Duration

	pollInterval time.Duration
	stopCh       chan struct{}
	doneCh       chan struct{}
}

// New constructs a Throttler.
func New(opts Options) *Throttler {
	now := opts.Now
	if now == nil {
		now = time.Now
	}
	retry := opts.RetryInterval
	if retry <= 0 {
		retry = 100 * time.Millisecond
	}
	t := &Throttler{
		configs:      map[string]ServiceConfig{},
		services:     map[string]*serviceState{},
		healthFn:     opts.Health,
		now:          now,
		retry:        retry,
		pollInterval: opts.PollInterval,
	}
	for svc, cfg := range opts.Configs {
		t.register(svc, cfg)
	}
	t.Refresh()
	if opts.PollInterval > 0 {
		stop := make(chan struct{})
		done := make(chan struct{})
		t.stopCh = stop
		t.doneCh = done
		go t.poll(stop, done)
	}
	return t
}

func (t *Throttler) poll(stop <-chan struct{}, done chan<- struct{}) {
	defer close(done)
	tk := time.NewTicker(t.pollInterval)
	defer tk.Stop()
	for {
		select {
		case <-stop:
			return
		case <-tk.C:
			t.Refresh()
		}
	}
}

func (t *Throttler) register(svc string, cfg ServiceConfig) {
	t.mu.Lock()
	defer t.mu.Unlock()
	now := t.now()
	st := &serviceState{ops: make(map[Op]*bucket, len(cfg.Ops)), health: 1}
	if old, ok := t.services[svc]; ok {
		st.health = old.health // preserve cached health across re-registration
	}
	for op, lim := range cfg.Ops {
		st.ops[op] = &bucket{rate: lim.Rate, burst: lim.Burst, tokens: lim.Burst, last: now}
	}
	if cfg.Total.Rate != 0 || cfg.Total.Burst != 0 {
		st.total = &bucket{rate: cfg.Total.Rate, burst: cfg.Total.Burst, tokens: cfg.Total.Burst, last: now}
	}
	t.configs[svc] = cfg
	t.services[svc] = st
}

// Register adds or replaces a service's config at runtime.
func (t *Throttler) Register(svc string, cfg ServiceConfig) {
	t.register(svc, cfg)
	h := t.healthFor(svc)
	t.mu.Lock()
	if st, ok := t.services[svc]; ok {
		st.health = h
	}
	t.mu.Unlock()
}

func (t *Throttler) healthFor(svc string) float64 {
	if t.healthFn == nil {
		return 1
	}
	h := t.healthFn(svc)
	if h < 0 {
		h = 0
	}
	if h > 1 {
		h = 1
	}
	return h
}

// Refresh forces an immediate re-evaluation of Health for every registered
// service, updating the cache.
func (t *Throttler) Refresh() {
	t.mu.Lock()
	svcs := make([]string, 0, len(t.configs))
	for s := range t.configs {
		svcs = append(svcs, s)
	}
	t.mu.Unlock()

	for _, s := range svcs {
		h := t.healthFor(s)
		t.mu.Lock()
		if st, ok := t.services[s]; ok {
			st.health = h
		}
		t.mu.Unlock()
	}
}

func refill(b *bucket, now time.Time, h float64) {
	if elapsed := now.Sub(b.last).Seconds(); elapsed > 0 {
		b.tokens += elapsed * b.rate * h
		if b.tokens > b.burst {
			b.tokens = b.burst
		}
		b.last = now
	}
}

// tryConsume attempts to take n tokens from every applicable bucket (the per-op
// bucket and the aggregate, if configured) atomically. If any is short, none is
// consumed. Returns whether it succeeded, whether it is blocked because no short
// bucket can refill (health/rate 0), and otherwise how long until n tokens would
// be available in all applicable buckets.
func (t *Throttler) tryConsume(svc string, op Op, n float64) (ok bool, blocked bool, wait time.Duration) {
	t.mu.Lock()
	defer t.mu.Unlock()

	st, exists := t.services[svc]
	if !exists {
		return true, false, 0
	}

	buckets := make([]*bucket, 0, 2)
	if b := st.ops[op]; b != nil {
		buckets = append(buckets, b)
	}
	if st.total != nil {
		buckets = append(buckets, st.total)
	}
	if len(buckets) == 0 {
		return true, false, 0
	}

	h := st.health
	now := t.now()
	for _, b := range buckets {
		refill(b, now, h)
	}

	enough := true
	for _, b := range buckets {
		if b.tokens < n {
			enough = false
			break
		}
	}
	if enough {
		for _, b := range buckets {
			b.tokens -= n
		}
		return true, false, 0
	}

	// Blocked: figure out whether time can ever satisfy the shortfall.
	var maxWait time.Duration
	for _, b := range buckets {
		if b.tokens >= n {
			continue
		}
		effRate := b.rate * h
		if effRate <= 0 {
			return false, true, 0
		}
		w := time.Duration((n - b.tokens) / effRate * float64(time.Second))
		if w > maxWait {
			maxWait = w
		}
	}
	return false, false, maxWait
}

// Allow tries to consume 1 token without blocking.
func (t *Throttler) Allow(svc string, op Op) bool {
	return t.AllowN(svc, op, 1)
}

// AllowN tries to consume n tokens without blocking.
func (t *Throttler) AllowN(svc string, op Op, n int) bool {
	if n <= 0 {
		return true
	}
	ok, _, _ := t.tryConsume(svc, op, float64(n))
	return ok
}

// Wait blocks until 1 token is available (or ctx is done).
func (t *Throttler) Wait(ctx context.Context, svc string, op Op) error {
	return t.WaitN(ctx, svc, op, 1)
}

// WaitN blocks until n tokens are available in all applicable buckets, then
// consumes them.
func (t *Throttler) WaitN(ctx context.Context, svc string, op Op, n int) error {
	if n <= 0 {
		return nil
	}
	for {
		ok, blocked, wait := t.tryConsume(svc, op, float64(n))
		if ok {
			return nil
		}
		if err := ctx.Err(); err != nil {
			return err
		}

		sleep := wait
		if blocked {
			sleep = t.retry
		}
		if sleep <= 0 {
			sleep = time.Millisecond
		}

		timer := time.NewTimer(sleep)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

// Close stops the background poller and waits for it to exit. It is safe to
// call more than once.
func (t *Throttler) Close() {
	t.mu.Lock()
	stop := t.stopCh
	done := t.doneCh
	t.stopCh = nil
	t.doneCh = nil
	t.mu.Unlock()

	if stop != nil {
		close(stop)
		<-done
	}
}
