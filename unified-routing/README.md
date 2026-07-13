# codimango/unified-routing

Stateful provider-routing CLI (`router --config --requests`) that returns an ordered, region-diverse failover chain (up to `max_replicas`) per request. Selection respects per-file capacity exhaustion, tenant spend budgets, SLA filtering, priority-adjusted strategy weights, region affinity (exact / same-continent / none), static per-provider health as a scoring divisor, and deterministic multi-level tie-breaking. Output is one compact JSON array of provider ids per request; exit codes signal full success (0), degraded (1), or invalid input (2).
