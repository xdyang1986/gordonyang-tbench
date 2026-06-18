Build an autoscaling feature in Go from scratch at /app (stdlib only) that decides how many instances (replicas) a fleet should run based on average CPU utilization.

Required behavior

  The autoscaler targets a configurable average CPU (default 60%) and adjusts replica count between a min and max. It must:

  - Scale up immediately when CPU spikes above target.
  - Ignore transient drops: a brief, single-sample dip in CPU must not scale the fleet in. Only scale in after low CPU has persisted for a configurable stabilization window.
  - Scale in gradually: when scaling down, remove at most a configurable fraction of the fleet per step, never below the minimum.
  - Stay within [min, max] replicas and avoid thrashing near the target (a tolerance dead band).

  The replica formula: desired = ceil(current * cpu / target), clamped to bounds, then adjusted by the stabilization window and the scale-down rate limit.

 Input

  Read the entire input from stdin.

  - Line 1 — configuration, space-separated key=value pairs (any order, all required):

  | key                 | meaning                                              | range     |
  |---------------------|------------------------------------------------------|-----------|
  | target              | target average CPU (fraction)                        | (0,1]     |
  | min                 | minimum replicas                                     | >= 1      |
  | max                 | maximum replicas                                     | >= min    |
  | tolerance           | dead-band around target                              | [0,1)     |
  | down_window         | scale-down stabilization window, seconds             | >= 0      |
  | max_scale_down_frac | max fraction of fleet removable per tick; 0 disables | [0,1]     |
  | tick                | seconds between ticks                                | >= 1      |
  | start               | initial replica count                                | [min,max] |

  - Remaining lines — one CPU sample per tick, each a single float in [0,1] giving the measured average CPU at that tick. Tick i (0-based) occurs at simulated time i * tick seconds. There may be any number of samples.

  Any non-blank sample line that does not parse as a float within range ([0,1]) must cause the program to print error: <message> to stderr and exit non-zero; blank lines are ignored.

  Output

  Write to stdout: a header line, then one line per input sample, in CSV:

  tick,cpu,replicas,action

  - tick — 0-based sample index.
  - cpu — the input sample echoed, formatted to exactly 2 decimals.
  - replicas — fleet size after this tick's decision.
  - action — exactly one of up if increased, down if decreased, none if unchanged.

  Output must be deterministic: the same input always yields byte-identical output.
