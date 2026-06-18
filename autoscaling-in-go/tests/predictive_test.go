package autoscaler

import (
	"context"
	"testing"
	"time"
)

// Hidden tests for the predictive (seasonal) scale-up feature.
//
// Contract under test:
//   - Config.Forecast (Forecaster), Config.PredictionLookahead, Config.PredictionStep
//   - predictiveFloor(now, f, lookahead, step, targetCPU, min, max) int
//   - engine wires it as a scale-UP-only floor: desired = max(desired, floor)
// Prediction must never scale the fleet in; scale-down stays reactive.

// constForecast predicts a constant load at all times.
type constForecast float64

func (c constForecast) PredictedLoad(time.Time) float64 { return float64(c) }

// spikeForecast predicts hi during [hiStart, hiEnd) of each period (measured
// from epoch) and lo otherwise — a stand-in for a daily/weekly pattern.
type spikeForecast struct {
	epoch          time.Time
	lo, hi         float64
	hiStart, hiEnd time.Duration
	period         time.Duration // 0 == non-repeating
}

func (s spikeForecast) PredictedLoad(t time.Time) float64 {
	d := t.Sub(s.epoch)
	if s.period > 0 {
		d %= s.period
		if d < 0 {
			d += s.period
		}
	}
	if d >= s.hiStart && d < s.hiEnd {
		return s.hi
	}
	return s.lo
}

// pfMetrics derives CPU from realized load (== the forecast, realized) over the
// live fleet — a closed loop where prediction must act before load arrives.
type pfMetrics struct {
	fleet *clFleet
	f     Forecaster
	clk   *FakeClock
}

func (m *pfMetrics) CPUUtilization(context.Context) (float64, error) {
	load := m.f.PredictedLoad(m.clk.Now())
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

const t0 = 1_700_000_000

// --- Unit tests for the pure predictiveFloor function ---

func TestPredictiveFloorDisabled(t *testing.T) {
	now := time.Unix(t0, 0)
	const minR, maxR = 1, 20
	// When prediction is disabled, the predictive floor must impose NO upward
	// pressure: the engine only ever uses it to RAISE desired, and desired is
	// already >= MinReplicas. So any value <= MinReplicas is a correct "no-op"
	// (0 is the canonical no-floor; returning MinReplicas is equally valid). A
	// value above MinReplicas would wrongly scale the fleet out while off.
	cases := []struct {
		name            string
		f               Forecaster
		lookahead, step time.Duration
	}{
		{"nil forecaster", nil, time.Hour, time.Minute},
		{"lookahead<=0", constForecast(12), 0, time.Minute},
		{"step<=0", constForecast(12), time.Hour, 0},
	}
	for _, c := range cases {
		if got := predictiveFloor(now, c.f, c.lookahead, c.step, 0.6, minR, maxR); got > minR {
			t.Fatalf("%s: disabled prediction must impose no floor (<= %d), got %d", c.name, minR, got)
		}
	}
}

func TestPredictiveFloorConstant(t *testing.T) {
	now := time.Unix(t0, 0)
	// load 12 at target 0.6 -> ceil(20) = 20.
	if got := predictiveFloor(now, constForecast(12), 30*time.Minute, 5*time.Minute, 0.6, 1, 50); got != 20 {
		t.Fatalf("expected floor 20, got %d", got)
	}
}

func TestPredictiveFloorExactInteger(t *testing.T) {
	now := time.Unix(t0, 0)
	// load 1.2 at target 0.6 == exactly 2 replicas; must not over-provision to 3.
	if got := predictiveFloor(now, constForecast(1.2), 10*time.Minute, 5*time.Minute, 0.6, 1, 50); got != 2 {
		t.Fatalf("exact integer must not round up: expected 2, got %d", got)
	}
}

func TestPredictiveFloorPeakOverWindow(t *testing.T) {
	now := time.Unix(t0, 0)
	// Spike sits in the MIDDLE of the window: lo at the start and at the horizon,
	// hi only at interior samples. An impl that samples only the endpoints would
	// miss it and return 2; the correct peak-over-window impl returns 20.
	f := spikeForecast{epoch: now, lo: 1.2, hi: 12.0, hiStart: 10 * time.Minute, hiEnd: 15 * time.Minute}
	got := predictiveFloor(now, f, 30*time.Minute, 5*time.Minute, 0.6, 1, 50)
	if got != 20 {
		t.Fatalf("must size for the interior peak (20), got %d", got)
	}
}

func TestPredictiveFloorClampMax(t *testing.T) {
	now := time.Unix(t0, 0)
	if got := predictiveFloor(now, constForecast(100), 10*time.Minute, 5*time.Minute, 0.6, 1, 8); got != 8 {
		t.Fatalf("floor must clamp to MaxReplicas=8, got %d", got)
	}
}

// --- Behavioral tests through the Engine ---

func newPredEngine(t *testing.T, cfg Config, start time.Time) (*Engine, *clFleet, *FakeClock) {
	t.Helper()
	clk := NewFakeClock(start)
	fleet := &clFleet{replicas: 2}
	m := &pfMetrics{fleet: fleet, f: cfg.Forecast, clk: clk}
	e, err := New(cfg, m, fleet, WithClock(clk))
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return e, fleet, clk
}

// TestPredictivePreScalesAheadOfSpike: with a daily forecast whose peak begins
// at phase 1h, and a 30m lookahead, the fleet must scale out at phase 30m —
// BEFORE the real load arrives — purely from the prediction.
func TestPredictivePreScalesAheadOfSpike(t *testing.T) {
	epoch := time.Unix(t0, 0)
	f := spikeForecast{epoch: epoch, lo: 1.2, hi: 12.0, hiStart: time.Hour, hiEnd: 3 * time.Hour, period: 24 * time.Hour}

	cfg := DefaultConfig()
	cfg.TargetCPU = 0.60
	cfg.MinReplicas, cfg.MaxReplicas = 1, 20
	cfg.Forecast = f
	cfg.PredictionLookahead = 30 * time.Minute
	cfg.PredictionStep = 5 * time.Minute

	e, fleet, clk := newPredEngine(t, cfg, epoch)

	for i := 0; i <= 6; i++ { // phases 0,5,...,30 minutes
		phase := time.Duration(i) * 5 * time.Minute
		d, err := e.Tick(context.Background())
		if err != nil {
			t.Fatalf("phase %v: %v", phase, err)
		}
		if phase < 30*time.Minute {
			if fleet.replicas > 3 {
				t.Fatalf("prediction fired too early at phase %v: fleet=%d", phase, fleet.replicas)
			}
		} else { // phase == 30m: lookahead reaches the 1h peak, real load still lo
			if fleet.replicas != 20 {
				t.Fatalf("expected pre-scale to 20 at phase 30m, got %d (actual cpu %.2f)", fleet.replicas, d.CPUUtilization)
			}
			if d.CPUUtilization > 0.7 {
				t.Fatalf("setup: scale-up at phase 30m should be predictive, not reactive (cpu %.2f)", d.CPUUtilization)
			}
		}
		clk.Advance(5 * time.Minute)
	}
}

// TestPredictiveOffByDefault: same scenario with no forecaster must NOT
// pre-scale — the fleet stays at the reactive level through phase 30m.
func TestPredictiveOffByDefault(t *testing.T) {
	epoch := time.Unix(t0, 0)
	f := spikeForecast{epoch: epoch, lo: 1.2, hi: 12.0, hiStart: time.Hour, hiEnd: 3 * time.Hour, period: 24 * time.Hour}

	cfg := DefaultConfig()
	cfg.TargetCPU = 0.60
	cfg.MinReplicas, cfg.MaxReplicas = 1, 20
	// Forecast left nil (default) -> prediction disabled. Drive the SAME realized
	// load so only the predictive wiring differs.
	clk := NewFakeClock(epoch)
	fleet := &clFleet{replicas: 2}
	m := &pfMetrics{fleet: fleet, f: f, clk: clk}
	e, err := New(cfg, m, fleet, WithClock(clk))
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	for i := 0; i <= 6; i++ {
		if _, err := e.Tick(context.Background()); err != nil {
			t.Fatalf("tick %d: %v", i, err)
		}
		if fleet.replicas > 3 {
			t.Fatalf("prediction disabled but fleet pre-scaled to %d at phase %v", fleet.replicas, time.Duration(i)*5*time.Minute)
		}
		clk.Advance(5 * time.Minute)
	}
}

// TestPredictiveNeverScalesDown: a forecaster predicting LOW load must never
// pull the fleet in — prediction is a scale-up floor only; scale-down stays
// governed by actual CPU.
func TestPredictiveNeverScalesDown(t *testing.T) {
	cfg := DefaultConfig()
	cfg.TargetCPU = 0.60
	cfg.MinReplicas, cfg.MaxReplicas = 1, 50
	cfg.Forecast = constForecast(1.2) // predicts only ~2 replicas' worth
	cfg.PredictionLookahead = 30 * time.Minute
	cfg.PredictionStep = 5 * time.Minute

	// Actual CPU exactly at target -> reactive path holds at 10.
	m := &fakeMetrics{cpu: 0.60}
	s := &fakeScaler{replicas: 10}
	clk := NewFakeClock(time.Unix(t0, 0))
	e, err := New(cfg, m, s, WithClock(clk))
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	d, err := e.Tick(context.Background())
	if err != nil {
		t.Fatalf("Tick: %v", err)
	}
	if s.replicas != 10 {
		t.Fatalf("low forecast must not scale the fleet down; fleet=%d (%s)", s.replicas, d.Reason)
	}
}
