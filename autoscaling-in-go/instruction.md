Build an autoscaling feature in Go from scratch at /app (stdlib only) that decides how many instances (replicas) a fleet should run based on average CPU utilization.

Required behavior:

The autoscaler targets a configurable average CPU (default 60%) and adjusts replica count between a min and max. It must:
1. Scale up immediately when CPU usage is above the target.
2. Ignore transient drops: a brief, single-sample dip in CPU must not scale the fleet in. Only scale in after low CPU has persisted for a configurable stabilization window.
3. Scale in gradually: when scaling down, remove at most a configurable fraction of the fleet per step, never below the minimum.
4. Stay within [min, max] replicas and avoid thrashing near the target (a tolerance dead band).
5. The replica formula: desired = ceil(current * cpu / target), clamped to bounds, then adjusted by the stabilization window and the scale-down rate limit.
6. Support predictive scale up, it can predict the traffic based on previous data and pre-scale up if needed, don't need to pre-scale down. By default, it's off.

Input:

The first line is the configuration: a single line of space-separated key=value pairs. Every remaining non-blank line is one CPU sample.

configuration key:
1. target: target average CPU (fraction). range (0,1]
2. min: minimum replicas. range: >= 1
3. max: maximum replicas. range >= min
4. tolerance: dead-band around target. range: [0,1)
5. down_window: scale-down stabilization window, seconds. range: >= 0
6. max_scale_down_frac: max fraction of fleet removable per tick; range (0,1]
7. tick: seconds between ticks. range: >= 1
8. start: initial replica count.
9. predict_lookahead: optional, if omitted or 0, means disable. Otherwise, it's the look-ahead seconds.

Remaining lines — one CPU sample per tick, each a single float in [0,1] giving the measured average CPU at that tick. Tick i (0-based) occurs at simulated time i * tick seconds. There may be any number of samples.
Any non-blank sample line that does not parse as a float within range ([0,1]) must cause the program to print error: <message> to stderr and exit non-zero; blank lines are ignored.

Output

Write to stdout: a header line, then one line per input sample, in CSV:
tick,cpu,replicas,action

1. tick — 0-based sample index.
2. cpu — the input sample echoed, formatted to exactly 2 decimals.
3. replicas — fleet size after this tick's decision.
4. action — exactly one of up if increased, down if decreased, none if unchanged.

The output must be deterministic.
