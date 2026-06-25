Build an autoscaling feature in Go from scratch at /app (stdlib only) that decides how many instances (replicas) a fleet should run based on average CPU utilization.

required behavior:

1. It should scale up immediately when CPU usage is above the target.
2. It ignores transient drops: a brief, single-sample dip in CPU must not scale the fleet in. Only scale in after low CPU has persisted for a configurable stabilization window.
3. It needs to scale in gradually: when scaling down, remove at most a configurable fraction of the fleet per step, never below the minimum.
4. It must stay within [min, max] replicas and avoid thrashing near the target (a tolerance dead band).
5. Replic should be calculated based on this formula: desired = ceil(current * cpu / target), and consider bounds, stabilization window and the scale-down rate limit.
6. It needs to support predictive scale up, it can predict the traffic based on previous data and pre-scale up if needed, don't need to pre-scale down. By default, it's off.

input:

The first line is the configuration: a single line of space-separated key=value pairs. Every remaining non-blank line is one CPU sample.

configuration key:
target: target average CPU (fraction). range (0,1]
min: minimum replicas.
max: maximum replicas.
tolerance: dead-band around target. range: [0,1)
down_window: scale-down stabilization window, seconds.
max_scale_down_frac: max fraction of fleet removable per tick;
tick: seconds between ticks.
start: initial replica count.
predict_lookahead: optional, if omitted or 0, means disable. Otherwise, it's the look-ahead seconds.

Remaining lines: one CPU sample per tick, each a single float in [0,1] giving the measured average CPU at that tick. Tick i (0-based) occurs at simulated time i * tick seconds. There may be any number of samples.
Any non-blank sample line that does not parse as a float within range ([0,1]) must cause the program to print error: <message> to stderr and exit non-zero; blank lines are ignored.

output:

Write to stdout: a header line, then one line per input sample, in CSV:
tick,cpu,replicas,action

tick is a 0-based sample index.
cpu is the input sample echoed, formatted to exactly 2 decimals.
replicas is the fleet size after this tick's decision.
action: it has three value: up/down/none, up means increase, down means decrease and none means unchanged.

The output must be deterministic.
