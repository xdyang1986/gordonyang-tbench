// Command autoscalesim demonstrates the CPU-utilization autoscaler against a
// scripted workload. It runs entirely in memory on a simulated clock, so it
// prints a full spike/transient-dip/sustained-drop timeline in well under a
// second. Run with: go run ./cmd/autoscalesim
package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"autoscaling/autoscaler"
)

// fleet is an in-memory Scaler.
type fleet struct{ replicas int }

func (f *fleet) CurrentReplicas(context.Context) (int, error) { return f.replicas, nil }
func (f *fleet) SetReplicas(_ context.Context, n int) error   { f.replicas = n; return nil }

// workloadMetrics models per-instance CPU as total offered load spread across
// the current fleet. Each instance saturates at unitsPerInstance of load.
type workloadMetrics struct {
	fleet        *fleet
	unitsPerInst float64
	loadAt       func(elapsed time.Duration) float64
	clock        *autoscaler.FakeClock
	start        time.Time
}

func (w *workloadMetrics) CPUUtilization(context.Context) (float64, error) {
	load := w.loadAt(w.clock.Now().Sub(w.start))
	if w.fleet.replicas <= 0 {
		return 1.0, nil
	}
	util := load / (float64(w.fleet.replicas) * w.unitsPerInst)
	if util > 1.0 {
		util = 1.0
	}
	if util < 0 {
		util = 0
	}
	return util, nil
}

func main() {
	start := time.Unix(1_700_000_000, 0)
	clk := autoscaler.NewFakeClock(start)

	f := &fleet{replicas: 3}

	// Scripted offered load over a ~22 minute window. Units are "instances'
	// worth of work at 100% CPU" (unitsPerInst == 1.0 below).
	load := func(elapsed time.Duration) float64 {
		m := elapsed.Minutes()
		switch {
		case m < 3:
			return 1.8 // baseline: ~60% CPU on 3 instances
		case m < 8:
			return 5.5 // sustained SPIKE: needs many more instances
		case m >= 10 && m < 10.5:
			return 0.3 // TRANSIENT dip (one ~30s sample) — must be ignored
		case m < 14:
			return 5.5 // spike continues right after the blip
		default:
			return 1.2 // SUSTAINED drop: safe to scale in
		}
	}

	metrics := &workloadMetrics{
		fleet:        f,
		unitsPerInst: 1.0,
		loadAt:       load,
		clock:        clk,
		start:        start,
	}

	cfg := autoscaler.DefaultConfig()
	cfg.TargetCPU = 0.60
	cfg.MinReplicas = 1
	cfg.MaxReplicas = 20
	cfg.Tolerance = 0.10
	cfg.ScaleUpStabilizationWindow = 0                 // react to spikes immediately
	cfg.ScaleDownStabilizationWindow = 3 * time.Minute // absorb transient dips
	cfg.MaxScaleDownFraction = 0.30                    // shed at most 30% of the fleet per step

	eng, err := autoscaler.New(cfg, metrics, f, autoscaler.WithClock(clk))
	if err != nil {
		panic(err)
	}

	fmt.Printf("Policy: target=%.0f%% CPU, replicas=[%d..%d], tolerance=%.0f%%, up-window=%s, down-window=%s, max-scale-down=%.0f%%/step\n\n",
		cfg.TargetCPU*100, cfg.MinReplicas, cfg.MaxReplicas, cfg.Tolerance*100,
		cfg.ScaleUpStabilizationWindow, cfg.ScaleDownStabilizationWindow, cfg.MaxScaleDownFraction*100)
	fmt.Printf("%-7s  %-6s  %-7s  %-22s  %s\n", "TIME", "CPU", "FLEET", "CPU UTILIZATION", "ACTION")
	fmt.Println(strings.Repeat("-", 88))

	const interval = 30 * time.Second
	for t := 0; t <= 44; t++ {
		d, err := eng.Tick(context.Background())
		if err != nil {
			panic(err)
		}

		action := "—"
		if d.Changed {
			arrow := "▲ up"
			if d.Desired < d.CurrentReplicas {
				arrow = "▼ down"
			}
			action = fmt.Sprintf("%s  %d → %d", arrow, d.CurrentReplicas, d.Desired)
		} else if d.RawDesired < d.CurrentReplicas {
			action = "hold (dip absorbed)"
		}

		mm := int(clk.Now().Sub(start).Minutes())
		ss := int(clk.Now().Sub(start).Seconds()) % 60
		fmt.Printf("%02d:%02d    %5.0f%%  %5d    %-22s  %s\n",
			mm, ss, d.CPUUtilization*100, f.replicas, bar(d.CPUUtilization), action)

		clk.Advance(interval)
	}
}

// bar renders a 20-cell utilization meter.
func bar(util float64) string {
	const width = 20
	filled := int(util*width + 0.5)
	if filled > width {
		filled = width
	}
	return "[" + strings.Repeat("#", filled) + strings.Repeat(".", width-filled) + "]"
}
