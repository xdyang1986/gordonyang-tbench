Build an autoscaling feature in Go from scratch at /app that decides how many instances (replicas) a fleet should run based on average CPU utilization.

Main requirements:

1. Scale up and down based on the CPU usage. The desired replica count that brings the average CPU to target is round up of current * cpu / target. When |cpu/target - 1| <= tolerance, don't scale up or down.
2. Don't scale down if there is a transient drop in CPU.
3. Scale down should not be too fast: per tick, remove at most current * max_scale_down_frac (floor) replicas, but always at least 1 when scaling in.
4. You should respect min and max constraints while doing the scaling.
5. It should support predictive scale up based on previous data and pre-scale up if needed, scale down is not needed. The feature is off by default. And only pre-scale on a clear sustained upward trend.
6. Support the cooldown for scale up/down: when a scale up happened, suppress further scaleup for up_cooldown seconds, the same to the scale down behavior (down_cooldown) second. It's also optional, 0 means disable.

Input:

The first line is the configuration, it's constructed as space separated key-values, more details as below for each key:

    "target": target average CPU (fraction). range (0,1]
    "min": minimum replicas.
    "max": maximum replicas.
    "tolerance": dead-band around target. range: [0,1)
    "down_window": scale-down stabilization window, seconds.
    "max_scale_down_frac": max fraction of fleet removable per tick;
    "tick": seconds between ticks.
    "start": initial replica count.
    "predict_lookahead": optional, if omitted or 0, means disable. Otherwise, it's the look-ahead seconds.
    "up_cooldown": minimum seconds between scale ups. 0 means disable.
    "down_cooldown": minimum seconds between scale dows. 0 means disable.

Every remaining non-blank line is one CPU sample per tick, each a single float in [0,1] giving the measured average CPU at that tick. Tick i (0-based) means the time of i * tick seconds. If there are blank lines, please ignore them.
If the input is not valid, exit with a non-zero status.

Write the output to stdout: a header line, then one line per input sample, in CSV with this format: tick,cpu,replicas,action -- tick is a 0-based sample index and cpu is the input sample echoed, formatted to exactly 2 decimals. replicas is the fleet size after this tick's decision and action has three values: up/down/none (up means increase, down means decrease and none means unchanged).
