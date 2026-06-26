Build an autoscaling feature in Go from scratch at /app that decides how many instances (replicas) a fleet should run based on average CPU utilization.

Requirements:

1. Scale up and down based on the CPU usage. The desired replica count that brings the average CPU to target is round up of current * cpu / target. When |cpu/target - 1| <= tolerance, don't scale up or down.
2. Don't scale down if there is a transient drop in CPU.
3. Scale down should not be too fast: per tick, remove at most current * max_scale_down_frac (floor) replicas, but always at least 1 when scaling in.
4. You should consider min and max constraints while doing the scaling.
5. Support predictive scale up, the prediction is based on previous data and pre-scale up if needed, don't based on the prediction to do the scale down. The feature is off by default. And only pre-scale on a clear sustained upward trend.

Input: The first line is the configuration: a single line of space-separated key=value pairs. Every remaining non-blank line is one CPU sample.

configuration key looks like this:
    "target": target average CPU (fraction). range (0,1]
    "min": minimum replicas.
    "max": maximum replicas.
    "tolerance": dead-band around target. range: [0,1)
    "down_window": scale-down stabilization window, seconds.
    "max_scale_down_frac": max fraction of fleet removable per tick;
    "tick": seconds between ticks.
    "start": initial replica count.
    "predict_lookahead": optional, if omitted or 0, means disable. Otherwise, it's the look-ahead seconds.

Remaining lines: one CPU sample per tick, each a single float in [0,1] giving the measured average CPU at that tick. Tick i (0-based) means the time of i * tick seconds. If there is blank lines, please ignore them.
If the input is not valid, exit with a non-zero status.

output:

    Write to stdout: a header line, then one line per input sample, in CSV with this format: tick,cpu,replicas,action -- tick is a 0-based sample index and cpu is the input sample echoed, formatted to exactly 2 decimals. replicas is the fleet size after this tick's decision and action has three value: up/down/none, up means increase, down means decrease and none means unchanged.

The output must be deterministic.
