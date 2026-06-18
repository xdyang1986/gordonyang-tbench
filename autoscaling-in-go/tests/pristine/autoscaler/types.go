package autoscaler

import (
	"context"
	"fmt"
	"time"
)

// MetricSource provides the signal the autoscaler reacts to: the average CPU
// utilization across the currently running instances, as a fraction in [0,1]
// (0.6 == 60%). Implementations might read from a metrics backend, a cloud
// provider API, or — in the simulation harness — a synthetic workload.
type MetricSource interface {
	CPUUtilization(ctx context.Context) (float64, error)
}

// Scaler is the actuator the autoscaler drives. It reports the current instance
// count and applies a new desired count. Implementations might call a cloud
// Auto Scaling Group, a Kubernetes deployment, or an in-memory fleet.
type Scaler interface {
	CurrentReplicas(ctx context.Context) (int, error)
	SetReplicas(ctx context.Context, n int) error
}

// Forecaster predicts future offered load.
// PredictedLoad(t) returns anticipated offered load at t, in "instances'-worth of work"
// (replicas needed == ceil(load / TargetCPU)); it encodes the daily/weekly cycle.
type Forecaster interface {
	PredictedLoad(t time.Time) float64
}

// Config defines the autoscaling policy.
type Config struct {
	// TargetCPU is the desired average CPU utilization, a fraction in (0,1].
	// The controller adds/removes instances to drive utilization toward this.
	TargetCPU float64

	// MinReplicas and MaxReplicas bound the instance count. MinReplicas must be
	// >= 1 and <= MaxReplicas.
	MinReplicas int
	MaxReplicas int

	// Tolerance is a symmetric dead band around TargetCPU (e.g. 0.10 == 10%).
	// Utilization ratios within [1-Tolerance, 1+Tolerance] produce no change,
	// preventing thrash near the target.
	Tolerance float64

	// ScaleUpStabilizationWindow delays scale-up by holding the minimum
	// recommendation seen over this trailing window. Default 0 == react to
	// spikes immediately.
	ScaleUpStabilizationWindow time.Duration

	// ScaleDownStabilizationWindow protects against transient utilization
	// drops: scale-down uses the *maximum* recommendation over this trailing
	// window, so the fleet only shrinks once low demand has persisted for the
	// whole window. This is the primary defense against flapping on dips.
	ScaleDownStabilizationWindow time.Duration

	// MaxScaleDownFraction caps how much of the current fleet may be removed in
	// a single step, as a proportion in [0,1] (e.g. 0.25 == at most 25% per
	// step). This is an additional, rate-based guard on top of the
	// stabilization window for extra-conservative scale-in: it spreads a large
	// drop across several steps so capacity is reclaimed gradually. At least one
	// instance can always be removed when scaling down, so the fleet still
	// converges. 0 disables the limit (single-step scale-down). Scale-up is
	// never rate-limited, so spikes are still handled immediately.
	MaxScaleDownFraction float64

	// Forecast is an optional predictor of future offered load, in
	// "instances'-worth of work". Replicas needed == ceil(load / TargetCPU).
	// nil disables prediction.
	Forecast Forecaster

	// PredictionLookahead is how far into the future to scan for predicted load.
	PredictionLookahead time.Duration

	// PredictionStep is the sampling interval when scanning the lookahead window.
	PredictionStep time.Duration
}

// DefaultConfig returns a sensible starting policy: target 60% CPU, scale up on
// spikes immediately, and require 5 minutes of sustained low demand before
// scaling down.
func DefaultConfig() Config {
	return Config{
		TargetCPU:                    0.60,
		MinReplicas:                  1,
		MaxReplicas:                  100,
		Tolerance:                    0.10,
		ScaleUpStabilizationWindow:   0,
		ScaleDownStabilizationWindow: 5 * time.Minute,
		MaxScaleDownFraction:         0, // off by default; opt in for gradual scale-in
	}
}

// Validate reports configuration errors.
func (c Config) Validate() error {
	if c.TargetCPU <= 0 || c.TargetCPU > 1 {
		return fmt.Errorf("autoscaler: TargetCPU must be in (0,1], got %v", c.TargetCPU)
	}
	if c.MinReplicas < 1 {
		return fmt.Errorf("autoscaler: MinReplicas must be >= 1, got %d", c.MinReplicas)
	}
	if c.MaxReplicas < c.MinReplicas {
		return fmt.Errorf("autoscaler: MaxReplicas (%d) must be >= MinReplicas (%d)", c.MaxReplicas, c.MinReplicas)
	}
	if c.Tolerance < 0 || c.Tolerance >= 1 {
		return fmt.Errorf("autoscaler: Tolerance must be in [0,1), got %v", c.Tolerance)
	}
	if c.ScaleUpStabilizationWindow < 0 {
		return fmt.Errorf("autoscaler: ScaleUpStabilizationWindow must be >= 0")
	}
	if c.ScaleDownStabilizationWindow < 0 {
		return fmt.Errorf("autoscaler: ScaleDownStabilizationWindow must be >= 0")
	}
	if c.MaxScaleDownFraction < 0 || c.MaxScaleDownFraction > 1 {
		return fmt.Errorf("autoscaler: MaxScaleDownFraction must be in [0,1], got %v", c.MaxScaleDownFraction)
	}
	return nil
}

// recommendation is one timestamped raw replica recommendation retained for
// the stabilization windows.
type recommendation struct {
	atUnixNano int64
	replicas   int
}
