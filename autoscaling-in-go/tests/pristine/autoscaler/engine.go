package autoscaler

import (
	"context"
	"time"
)

// Engine is the autoscaling control loop. It is safe for sequential use by a
// single goroutine (Run owns it); call Tick directly from one goroutine in
// tests. Construct with New.
type Engine struct {
	cfg     Config
	metrics MetricSource
	scaler  Scaler
	clock   Clock

	// history holds raw recommendations newest-last, pruned to the longer of
	// the two stabilization windows.
	history []recommendation
}

// Option customizes an Engine at construction.
type Option func(*Engine)

// WithClock injects a custom Clock (used by tests).
func WithClock(c Clock) Option {
	return func(e *Engine) { e.clock = c }
}

// New builds an Engine. It returns an error if the config is invalid.
func New(cfg Config, metrics MetricSource, scaler Scaler, opts ...Option) (*Engine, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	e := &Engine{
		cfg:     cfg,
		metrics: metrics,
		scaler:  scaler,
		clock:   realClock{},
	}
	for _, opt := range opts {
		opt(e)
	}
	return e, nil
}

// Decision captures the outcome of one control-loop iteration. It is returned
// by Tick for logging, metrics, and testing.
type Decision struct {
	At              time.Time
	CPUUtilization  float64
	CurrentReplicas int
	// RawDesired is the recommendation before stabilization.
	RawDesired int
	// Desired is the count actually applied after stabilization windows.
	Desired int
	// Changed reports whether SetReplicas was invoked.
	Changed bool
	// Reason is a short human-readable explanation.
	Reason string
}

// Tick performs one iteration: read CPU and current replicas, compute a raw
// recommendation, apply the stabilization windows, and actuate if the target
// changed. It returns the Decision made.
func (e *Engine) Tick(ctx context.Context) (Decision, error) {
	now := e.clock.Now()

	cpu, err := e.metrics.CPUUtilization(ctx)
	if err != nil {
		return Decision{}, err
	}
	current, err := e.scaler.CurrentReplicas(ctx)
	if err != nil {
		return Decision{}, err
	}

	raw := rawDesiredReplicas(current, cpu, e.cfg.TargetCPU, e.cfg.Tolerance, e.cfg.MinReplicas, e.cfg.MaxReplicas)

	// Record this sample, then prune anything older than the widest window.
	e.history = append(e.history, recommendation{atUnixNano: now.UnixNano(), replicas: raw})
	e.prune(now)

	upCutoff := now.Add(-e.cfg.ScaleUpStabilizationWindow).UnixNano()
	downCutoff := now.Add(-e.cfg.ScaleDownStabilizationWindow).UnixNano()
	stabilized := stabilize(current, raw, e.history, upCutoff, downCutoff)

	// Cap the per-step scale-in so a large drop is reclaimed gradually.
	desired := limitScaleDownRate(current, stabilized, e.cfg.MaxScaleDownFraction)

	// Predictive floor: pre-provision for forecast demand. Scale-UP only.
	if floor := predictiveFloor(now, e.cfg.Forecast, e.cfg.PredictionLookahead, e.cfg.PredictionStep, e.cfg.TargetCPU, e.cfg.MinReplicas, e.cfg.MaxReplicas); floor > desired {
		desired = floor
	}

	d := Decision{
		At:              now,
		CPUUtilization:  cpu,
		CurrentReplicas: current,
		RawDesired:      raw,
		Desired:         desired,
	}

	switch {
	case desired == current:
		d.Reason = reasonHold(raw, current)
		return d, nil
	case desired > current:
		d.Reason = "scale up: utilization above target"
	case desired > stabilized:
		d.Reason = "scale down (rate-limited): partial scale-in, capped by MaxScaleDownFraction"
	default:
		d.Reason = "scale down: low utilization sustained through stabilization window"
	}

	if err := e.scaler.SetReplicas(ctx, desired); err != nil {
		return Decision{}, err
	}
	d.Changed = true
	return d, nil
}

func reasonHold(raw, current int) string {
	switch {
	case raw > current:
		return "hold: scale-up suppressed (within tolerance or stabilization)"
	case raw < current:
		return "hold: transient drop absorbed by scale-down stabilization window"
	default:
		return "hold: utilization within tolerance"
	}
}

// prune drops recommendations older than the widest stabilization window; they
// can no longer influence any future decision.
func (e *Engine) prune(now time.Time) {
	widest := e.cfg.ScaleDownStabilizationWindow
	if e.cfg.ScaleUpStabilizationWindow > widest {
		widest = e.cfg.ScaleUpStabilizationWindow
	}
	cutoff := now.Add(-widest).UnixNano()

	keep := 0
	for _, rec := range e.history {
		if rec.atUnixNano >= cutoff {
			e.history[keep] = rec
			keep++
		}
	}
	e.history = e.history[:keep]
}

// Run executes the control loop every interval until ctx is cancelled. Each
// Decision is passed to onDecision (may be nil). A per-tick error from the
// metric source or scaler is passed to onError (may be nil) and does not stop
// the loop, so a transient backend blip cannot take the controller down.
func (e *Engine) Run(ctx context.Context, interval time.Duration, onDecision func(Decision), onError func(error)) error {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			d, err := e.Tick(ctx)
			if err != nil {
				if onError != nil {
					onError(err)
				}
				continue
			}
			if onDecision != nil {
				onDecision(d)
			}
		}
	}
}
