Scenario
A SaaS platform fronts multiple third-party cloud API providers offering the same capability but with different latency, cost, error rate, region placement, capacity, and static health. Tenants share the fleet under individual spend budgets. To deliver the best user experience you must build an abstract routing layer that selects an ordered failover chain per request while respecting capacity exhaustion, SLA tiers, priority, tenant budgets, health, and region diversity.

Implement this layer as a command-line tool called router.

CLI Interface
router --config <PATH> --requests <PATH>

Cluster Configuration
The --config flag points to a JSON document:

{
  "strategy": "latency",
  "max_replicas": 2,
  "tenant_budgets": {"acme": 0.05},
  "providers": [
    {
      "id": "aws-us-east",
      "region": "us-east",
      "latency_ms": 45,
      "cost_per_1k": 0.012,
      "error_rate": 0.005,
      "capacity_rps": 1200,
      "status": "up",
      "health": 0.95
    }
  ]
}

strategy — one of "latency", "cost", "balanced". Determines base scoring weights.
max_replicas — integer >=1, default 1. Number of distinct providers to return per request ordered by preference.
tenant_budgets — optional mapping tenant id to total USD budget across the request file. If omitted tenants have unlimited budget. Budgets are enforced per request in order.
providers[].id — unique non-empty string.
providers[].region — string region name, e.g. us-east, eu-west, ap-south.
providers[].latency_ms — non-negative integer base latency.
providers[].cost_per_1k — non-negative float USD per 1000 requests.
providers[].error_rate — float in [0,1].
providers[].capacity_rps — integer >=0 total requests this provider can serve across the whole file. Capacity is consumed in request order.
providers[].status — "up" or "down". Only up eligible.
providers[].health — float in (0,1] static health score. 1 is fully healthy. Must be >0 to be eligible. Health does not change during the run.

Request Input
The --requests flag points to newline-delimited JSON, one object per line:

{"id":"r1","user_region":"us-east","sla_ms":100,"priority":"high","tenant":"acme"}

Fields:
id — request identifier for ordering.
user_region — caller region string.
sla_ms — optional integer max acceptable latency.
priority — optional "high", "normal", "low". Default normal. Adjusts scoring weights to favor latency or cost.
tenant — optional string tenant id, default "default". Used for budget enforcement.
payload_kb — optional ignored.

Blank lines are ignored. Whitespace-only lines are ignored. Trailing newline does not create an extra request.

Routing Logic Stateful Per Request In Order
Maintain per-provider remaining capacity initialized from capacity_rps, and per-tenant remaining budget initialized from tenant_budgets or infinite. Process requests sequentially.

For each request:
1. Eligible providers: status up, remaining capacity >0, health >0, and tenant budget sufficient to afford the provider's per-request cost. Per-request cost = cost_per_1k / 1000.
2. If sla_ms present, keep providers where latency_ms <= sla_ms.
3. If no eligible provider remains, output [] and mark degraded, continue without consuming resources.
4. Compute base weights by strategy:
   latency:  w_lat=1.0, w_cost=100.0, w_err=10000.0
   cost:     w_lat=0.1, w_cost=1000.0, w_err=10000.0
   balanced: w_lat=1.0, w_cost=500.0, w_err=10000.0
   Apply priority multiplier: high increases the latency weight and decreases the cost weight, low does the opposite, normal unchanged.
5. Apply a region affinity bonus to effective latency based on provider region relative to user region. An exact region match receives the strongest bonus, the same continent a moderate bonus, otherwise no bonus. Continent grouping is implied by region naming conventions.
6. Compute score = ( effective_latency * w_lat + cost_per_1k * w_cost + error_rate * w_err ) / health . Lower is better.
7. Sort eligible providers by score ascending, with deterministic tie-breaking for reproducible output. Tie-breaking considers health, cost, latency, and identifier in a stable order.
8. Region-diverse selection up to max_replicas: first pass in sorted order picks providers whose region has not yet been chosen to maximize spread; second pass fills remaining slots with the next best providers regardless of region, preserving sorted order within each pass. The result is an ordered list of distinct provider ids, length <= max_replicas.
9. Emit the chosen list as a JSON array of provider ids in selection order. If its length < max_replicas mark degraded.
10. Consume resources: decrement the remaining capacity of the first provider in the list (the primary) by 1, and deduct that provider's per-request cost from the tenant budget. If the list is empty, consume nothing.

Output
For every request in input order emit one line to stdout: a JSON array of selected provider ids, e.g. ["aws-us-east","gcp-us-central"] or []. Use compact JSON encoding (Python json.dumps with separators ",",":").

Exit Codes
0 — Every request received exactly max_replicas providers.
1 — Degraded — at least one request received fewer than max_replicas.
2 — Invalid input — config unreadable or violates specification (invalid JSON, unknown strategy, max_replicas<1, missing providers array, duplicate/empty id, negative latency, negative cost, error_rate outside [0,1], negative capacity, health not in (0,1], unrecognized status, unrecognized priority in a request), or requests file unreadable or contains invalid JSON. No output produced.

Constraints
Python 3 only, standard library only.
Place implementation under /app/src with package unified_routing exposing class UnifiedRouter, and provide executable `router` in PATH.
No network access.

Deliverable
Running `router --config /path/to/config.json --requests /path/to/requests.jsonl` produces output as specified with tenant budget enforcement, region affinity, capacity tracking, priority-weighted scoring, static-health scoring, deterministic tie-breaking, and region-diverse replica ordering.
