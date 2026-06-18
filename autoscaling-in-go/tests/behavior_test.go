package autoscaler

import (
	"context"
	"math"
	"testing"
	"time"
)

// This is a hidden, closed-loop "simulation" test (separate from the unit
// suite). CPU utilization is a function of the LIVE fleet size, so the
// controller's own decisions feed back into the next sample — exactly like a
// real autoscaler. It asserts behavioral invariants that any correct
// implementation of the documented policy must satisfy, rather than scripting a
// fixed CPU sequence.

// clFleet is an in-memory Scaler.
type clFleet struct{ replicas int }

func (f *clFleet) CurrentReplicas(context.Context) (int, error) { return f.replicas, nil }
func (f *clFleet) SetReplicas(_ context.Context, n int) error   { f.replicas = n; return nil }

// clMetrics derives per-instance CPU from offered load spread over the live
// fleet, capped to [0,1]. unitsPerInst == 1.0 -> "load" is measured in
// instances'-worth-of-work-at-100%-CPU.
type clMetrics struct {
	fleet  *clFleet
	loadAt func(tick int) float64
	tick   int
}

func (m *clMetrics) CPUUtilization(context.Context) (float64, error) {
	load := m.loadAt(m.tick)
	if m.fleet.replicas <= 0 {
		return 1.0, nil
	}
	u := load / float64(m.fleet.replicas)
	if u > 1.0 {
		u = 1.0
	}
	if u < 0 {
		u = 0
	}
	return u, nil
}

// TestClosedLoopWorkload runs a baseline -> sustained spike -> single transient
// dip -> sustained drop workload and checks the autoscaler's defining
// behaviors.
func TestClosedLoopWorkload(t *testing.T) {
	const (
		interval = 30 * time.Second
		ticks    = 70
		dipTick  = 16 // single-sample transient dip inside the spike
		dropTick = 26 // sustained low demand begins here
		peakMin  = 14 // a correct controller reaches at least this during the spike
	)

	// Offered load schedule (instances'-worth of work).
	load := func(tick int) float64 {
		switch {
		case tick == dipTick:
			return 0.3 // transient dip: one ~30s sample, must be ignored
		case tick < 6:
			return 1.8 // baseline (~60% CPU on 3 instances)
		case tick <= 25:
			return 9.0 // sustained spike
		default:
			return 1.2 // sustained drop (safe to scale in)
		}
	}

	fleet := &clFleet{replicas: 3}
	metrics := &clMetrics{fleet: fleet, loadAt: load}

	cfg := DefaultConfig()
	cfg.TargetCPU = 0.60
	cfg.MinReplicas = 1
	cfg.MaxReplicas = 20
	cfg.Tolerance = 0.10
	cfg.ScaleUpStabilizationWindow = 0
	cfg.ScaleDownStabilizationWindow = 3 * time.Minute // == 6 ticks
	cfg.MaxScaleDownFraction = 0.30

	clk := NewFakeClock(time.Unix(1_700_000_000, 0))
	eng, err := New(cfg, metrics, fleet, WithClock(clk))
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	type sample struct {
		cpu                    float64
		before, after, rawDes  int
	}
	trace := make([]sample, ticks)

	for i := 0; i < ticks; i++ {
		metrics.tick = i
		before := fleet.replicas
		d, err := eng.Tick(context.Background())
		if err != nil {
			t.Fatalf("tick %d: %v", i, err)
		}
		trace[i] = sample{cpu: d.CPUUtilization, before: before, after: fleet.replicas, rawDes: d.RawDesired}
		clk.Advance(interval)
	}

	// Invariant 1: bounds are always respected.
	for i, s := range trace {
		if s.after < cfg.MinReplicas || s.after > cfg.MaxReplicas {
			t.Fatalf("tick %d: fleet %d out of bounds [%d,%d]", i, s.after, cfg.MinReplicas, cfg.MaxReplicas)
		}
	}

	// Invariant 2: per-step scale-DOWN never removes more than the rate cap
	// (max(1, floor(current*fraction))). Scale-up is never rate-limited.
	for i, s := range trace {
		if removed := s.before - s.after; removed > 0 {
			cap := int(math.Floor(float64(s.before) * cfg.MaxScaleDownFraction))
			if cap < 1 {
				cap = 1
			}
			if removed > cap {
				t.Fatalf("tick %d: removed %d instances (%d->%d), exceeds rate cap %d",
					i, removed, s.before, s.after, cap)
			}
		}
	}

	// Invariant 3: the spike causes a prompt scale-OUT (no down-window delay on
	// the way up). By a few ticks into the spike the fleet must be near demand.
	peak := 0
	for i := 6; i <= 15; i++ {
		if trace[i].after > peak {
			peak = trace[i].after
		}
	}
	if peak < peakMin {
		t.Fatalf("expected scale-up to >= %d during spike, peaked at %d", peakMin, peak)
	}

	// Invariant 4: the single transient dip must NOT scale the fleet in.
	if trace[dipTick].cpu >= 0.15 {
		t.Fatalf("test setup: dip CPU not low enough (%.3f)", trace[dipTick].cpu)
	}
	if trace[dipTick].after != trace[dipTick-1].after {
		t.Fatalf("transient dip scaled the fleet (%d -> %d); it must be held",
			trace[dipTick-1].after, trace[dipTick].after)
	}

	// Invariant 5: scale-IN is gated by the down-window. For the first several
	// ticks AFTER demand drops (still inside the 6-tick window), the fleet must
	// hold at the spike peak — the recent highs dominate the window.
	for i := dropTick; i < dropTick+5; i++ {
		if trace[i].after < peakMin {
			t.Fatalf("tick %d: fleet scaled in to %d during the stabilization window; "+
				"it must hold until low demand persists the full window", i, trace[i].after)
		}
	}

	// Invariant 6: once low demand has persisted well past the window, the fleet
	// converges down to roughly demand (ceil(1.2/0.6) == 2).
	final := trace[ticks-1].after
	if final > 4 {
		t.Fatalf("expected eventual scale-in to <= 4 after sustained low demand, got %d", final)
	}
}
