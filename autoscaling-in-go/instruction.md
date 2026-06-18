Build an autoscaling algorithm in the existing repo (autoscaler/algorithm.go) that decides how many instances (replicas) a fleet should run based on average CPU utilization.

Required behavior

  The autoscaler targets a configurable average CPU (default 60%) and adjusts replica count between a min and max. It must:

  - Scale up immediately when CPU spikes above target.
  - Ignore transient drops: a brief, single-sample dip in CPU must not scale the fleet in. Only scale in after low CPU has persisted for a configurable stabilization window (default 5 min).
  - Scale in gradually: when scaling down, remove at most a configurable fraction of the fleet per step (e.g. 30%), never below the minimum.
  - Stay within [min, max] replicas and avoid thrashing near the target (a tolerance dead band).
  - When the ideal replica count is an exact integer, don't round up, just use the integer. But genuine fractional needs must still round up.
  - Support predictive scaling up based on the lookahead window, don't scale down based on predictive. It should be off by default.

  The replica formula: desired = ceil(current * cpu / target), clamped to bounds, then adjusted by the stabilization window and the scale-down rate limit.

  Constraints

  - Go standard library only.
  - go build ./... must pass at /app.
  - Implement real logic, not mocks.
