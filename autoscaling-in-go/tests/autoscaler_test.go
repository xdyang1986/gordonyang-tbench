package autoscaler

import (
	"context"
	"errors"
	"testing"
	"time"
)

// fakeMetrics returns a CPU value that the test controls between ticks.
type fakeMetrics struct {
	cpu float64
	err error
}

func (f *fakeMetrics) CPUUtilization(context.Context) (float64, error) { return f.cpu, f.err }

// fakeScaler is an in-memory fleet.
type fakeScaler struct {
	replicas int
	sets     []int // history of SetReplicas calls
	setErr   error
}

func (f *fakeScaler) CurrentReplicas(context.Context) (int, error) { return f.replicas, nil }
func (f *fakeScaler) SetReplicas(_ context.Context, n int) error {
	if f.setErr != nil {
		return f.setErr
	}
	f.replicas = n
	f.sets = append(f.sets, n)
	return nil
}

func newTestEngine(t *testing.T, cfg Config, m *fakeMetrics, s *fakeScaler) (*Engine, *FakeClock) {
	t.Helper()
	clk := NewFakeClock(time.Unix(1_700_000_000, 0))
	e, err := New(cfg, m, s, WithClock(clk))
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return e, clk
}

func tick(t *testing.T, e *Engine) Decision {
	t.Helper()
	d, err := e.Tick(context.Background())
	if err != nil {
		t.Fatalf("Tick: %v", err)
	}
	return d
}

// TestScaleUpOnSpike verifies the controller reacts to a usage spike on the
// very next tick — no stabilization delay on the way up.
func TestScaleUpOnSpike(t *testing.T) {
	cfg := DefaultConfig() // target 0.60, up window 0
	cfg.MaxReplicas = 50
	m := &fakeMetrics{cpu: 0.60}
	s := &fakeScaler{replicas: 4}
	e, clk := newTestEngine(t, cfg, m, s)

	// Steady state at target: no change.
	if d := tick(t, e); d.Changed {
		t.Fatalf("expected hold at target, got change to %d (%s)", d.Desired, d.Reason)
	}

	// A tick later, spike to 100% CPU: desired = ceil(4 * 1.0/0.6) = ceil(6.67) = 7.
	clk.Advance(30 * time.Second)
	m.cpu = 1.0
	d := tick(t, e)
	if !d.Changed || d.Desired != 7 {
		t.Fatalf("expected immediate scale up to 7, got changed=%v desired=%d (%s)", d.Changed, d.Desired, d.Reason)
	}
	if s.replicas != 7 {
		t.Fatalf("fleet should be 7, got %d", s.replicas)
	}
}

// TestTransientDropHeld is the core requirement: a brief dip in CPU must NOT
// scale the fleet in. The scale-down stabilization window holds the count.
func TestTransientDropHeld(t *testing.T) {
	cfg := DefaultConfig()
	cfg.ScaleDownStabilizationWindow = 5 * time.Minute
	m := &fakeMetrics{cpu: 0.60}
	s := &fakeScaler{replicas: 10}
	e, clk := newTestEngine(t, cfg, m, s)

	// Establish a few ticks at target so history holds high recommendations.
	for i := 0; i < 3; i++ {
		tick(t, e)
		clk.Advance(30 * time.Second)
	}

	// A single transient drop to 10% CPU (raw desired would be ceil(10*0.1/0.6)=2).
	m.cpu = 0.10
	d := tick(t, e)
	if d.Changed {
		t.Fatalf("transient drop must not scale down; scaled to %d (%s)", d.Desired, d.Reason)
	}
	if s.replicas != 10 {
		t.Fatalf("fleet must stay at 10 during transient drop, got %d", s.replicas)
	}

	// CPU immediately recovers — still must be at 10.
	clk.Advance(30 * time.Second)
	m.cpu = 0.60
	if d := tick(t, e); d.Changed {
		t.Fatalf("no change expected after recovery, got %d", d.Desired)
	}
	if s.replicas != 10 {
		t.Fatalf("fleet should remain 10 after recovery, got %d", s.replicas)
	}
}

// TestSustainedDropScalesDown verifies that once low demand persists past the
// stabilization window, the fleet does shrink.
func TestSustainedDropScalesDown(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MinReplicas = 1
	cfg.ScaleDownStabilizationWindow = 5 * time.Minute
	m := &fakeMetrics{cpu: 0.60}
	s := &fakeScaler{replicas: 10}
	e, clk := newTestEngine(t, cfg, m, s)

	// One tick at target to seed a high recommendation.
	tick(t, e)

	// Now demand collapses and stays low. desired = ceil(10 * 0.1/0.6) = 2.
	m.cpu = 0.10
	clk.Advance(time.Second)
	if d := tick(t, e); d.Changed {
		t.Fatalf("should still be held just after drop, got %d", d.Desired)
	}

	// Advance past the window; the high seed recommendation ages out.
	clk.Advance(6 * time.Minute)
	d := tick(t, e)
	if !d.Changed || d.Desired != 2 {
		t.Fatalf("expected scale down to 2 after window, got changed=%v desired=%d (%s)", d.Changed, d.Desired, d.Reason)
	}
}

// TestRawDesiredNoFPOverprovision guards the epsilon fix: a utilization that is
// mathematically an exact integer multiple must not round up by one due to
// floating-point noise. 7 * (1.2/7)/0.6 == 2.0 exactly in real arithmetic.
func TestRawDesiredNoFPOverprovision(t *testing.T) {
	cpu := (1.2 / 7.0) // per-instance util such that 7 instances * cpu == 1.2 load
	got := rawDesiredReplicas(7, cpu, 0.60, 0.0, 1, 100)
	if got != 2 {
		t.Fatalf("expected 2 (no FP over-provision), got %d", got)
	}
	// A genuine fractional need still rounds up.
	if got := rawDesiredReplicas(4, 1.0, 0.60, 0.0, 1, 100); got != 7 {
		t.Fatalf("expected ceil(6.67)=7, got %d", got)
	}
}

// TestLimitScaleDownRate covers the pure per-step rate cap.
func TestLimitScaleDownRate(t *testing.T) {
	cases := []struct {
		name             string
		current, desired int
		fraction         float64
		want             int
	}{
		{"disabled passes through", 10, 2, 0, 2},
		{"scale up never limited", 4, 9, 0.25, 9},
		{"hold passes through", 5, 5, 0.5, 5},
		{"caps at fraction", 10, 2, 0.25, 8},   // remove at most 2 (25% of 10)
		{"half", 10, 2, 0.5, 5},                // remove at most 5
		{"at least one removed", 3, 1, 0.1, 2}, // floor(0.3)=0 -> forced to 1
		{"desired above floor untouched", 10, 9, 0.5, 9},
	}
	for _, c := range cases {
		if got := limitScaleDownRate(c.current, c.desired, c.fraction); got != c.want {
			t.Errorf("%s: limitScaleDownRate(%d,%d,%v)=%d, want %d", c.name, c.current, c.desired, c.fraction, got, c.want)
		}
	}
}

// TestGradualScaleDown verifies the fleet sheds capacity in bounded steps when
// MaxScaleDownFraction is set, rather than dropping all at once.
func TestGradualScaleDown(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MinReplicas = 1
	cfg.ScaleDownStabilizationWindow = 0 // isolate the rate limiter
	cfg.MaxScaleDownFraction = 0.5       // at most 50% removed per step
	m := &fakeMetrics{cpu: 0.10}         // raw desired = ceil(n*0.1/0.6) -> small
	s := &fakeScaler{replicas: 10}
	e, clk := newTestEngine(t, cfg, m, s)

	// Expect a bounded descent (never more than 50% off at once):
	// 10 -> 5 -> 3 -> 2 -> 1 (floor), rather than 10 -> 1 in a single step.
	want := []int{5, 3, 2, 1}
	for i, exp := range want {
		d := tick(t, e)
		if d.Desired != exp {
			t.Fatalf("step %d: got %d, want %d (%s)", i, d.Desired, exp, d.Reason)
		}
		clk.Advance(30 * time.Second)
	}
}

// TestToleranceDeadBand verifies no action when utilization is near target.
func TestToleranceDeadBand(t *testing.T) {
	cfg := DefaultConfig()
	cfg.TargetCPU = 0.60
	cfg.Tolerance = 0.10         // ratio within [0.9,1.1] -> hold
	m := &fakeMetrics{cpu: 0.63} // ratio 1.05, inside band
	s := &fakeScaler{replicas: 5}
	e, _ := newTestEngine(t, cfg, m, s)

	if d := tick(t, e); d.Changed {
		t.Fatalf("within tolerance should hold, got change to %d", d.Desired)
	}
}

// TestRespectsMaxReplicas verifies the upper bound is enforced on a big spike.
func TestRespectsMaxReplicas(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MaxReplicas = 8
	m := &fakeMetrics{cpu: 1.0}
	// ceil(6 * 1.0/0.6) = 10, which must be clamped down to the cap of 8.
	s := &fakeScaler{replicas: 6}
	e, _ := newTestEngine(t, cfg, m, s)

	d := tick(t, e)
	if d.Desired != 8 {
		t.Fatalf("desired should be clamped to MaxReplicas=8, got %d", d.Desired)
	}
}

// TestRespectsMinReplicas verifies the lower bound on a sustained idle fleet.
func TestRespectsMinReplicas(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MinReplicas = 2
	cfg.ScaleDownStabilizationWindow = time.Minute
	m := &fakeMetrics{cpu: 0.0}
	s := &fakeScaler{replicas: 6}
	e, clk := newTestEngine(t, cfg, m, s)

	tick(t, e)
	clk.Advance(2 * time.Minute)
	d := tick(t, e)
	if d.Desired != 2 {
		t.Fatalf("desired should clamp to MinReplicas=2, got %d", d.Desired)
	}
}

// TestRunSurvivesMetricError verifies a transient metric error is reported but
// does not stop the loop nor actuate.
func TestTickPropagatesMetricError(t *testing.T) {
	cfg := DefaultConfig()
	m := &fakeMetrics{cpu: 0.6, err: errors.New("backend down")}
	s := &fakeScaler{replicas: 3}
	e, _ := newTestEngine(t, cfg, m, s)

	if _, err := e.Tick(context.Background()); err == nil {
		t.Fatal("expected error from metric source")
	}
	if len(s.sets) != 0 {
		t.Fatal("no scaling should occur when metrics fail")
	}
}

func TestConfigValidation(t *testing.T) {
	cases := map[string]Config{
		"bad target":    {TargetCPU: 0, MinReplicas: 1, MaxReplicas: 2},
		"target >1":     {TargetCPU: 1.5, MinReplicas: 1, MaxReplicas: 2},
		"min < 1":       {TargetCPU: 0.6, MinReplicas: 0, MaxReplicas: 2},
		"max < min":     {TargetCPU: 0.6, MinReplicas: 5, MaxReplicas: 2},
		"bad tolerance": {TargetCPU: 0.6, MinReplicas: 1, MaxReplicas: 2, Tolerance: 1.0},
	}
	for name, cfg := range cases {
		if err := cfg.Validate(); err == nil {
			t.Errorf("%s: expected validation error", name)
		}
	}
	if err := DefaultConfig().Validate(); err != nil {
		t.Errorf("DefaultConfig should be valid: %v", err)
	}
}

// TestRunLoop exercises the ticker-driven loop end to end.
func TestRunLoop(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MaxReplicas = 50
	m := &fakeMetrics{cpu: 1.0}
	s := &fakeScaler{replicas: 4}
	e, _ := newTestEngine(t, cfg, m, s)

	ctx, cancel := context.WithCancel(context.Background())
	got := make(chan Decision, 1)
	go func() {
		_ = e.Run(ctx, time.Millisecond, func(d Decision) {
			if d.Changed {
				select {
				case got <- d:
				default:
				}
			}
		}, nil)
	}()

	select {
	case d := <-got:
		cancel()
		if d.Desired <= 4 {
			t.Fatalf("expected scale up in run loop, got %d", d.Desired)
		}
	case <-time.After(2 * time.Second):
		cancel()
		t.Fatal("Run loop did not produce a scaling decision")
	}
}
