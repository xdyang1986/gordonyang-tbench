# Global Traffic & Capacity Policy (Reliability Standards, rev. 12)

This document is the authoritative reliability standard for multi-region
services. It supersedes the regional addenda circulated last quarter. Questions
to #traffic-eng. Nothing in Sections 1 or 6 is required reading for the capacity
calculation, but they are retained for audit completeness.

## 1. Service-level objectives (informational)

Every tier-1 service commits to a p99 latency SLO of 250 ms measured at the edge,
an availability SLO of 99.95% per calendar month, and an error budget that is
burned by 5xx responses and by requests shed under load. Latency and error-rate
SLOs are tracked in the reliability dashboard and are **not** an input to the
capacity math below; they are governed separately by the SRE on-call rota.

## 2. Reporting units (read carefully)

Teams have historically reported capacity and demand in whatever unit their
dashboards defaulted to, and we have given up on normalizing at the source. The
capacity tool must therefore accept, **per field**, any of the following and
convert everything to **requests per second (rps)**, which is the canonical unit
for all reporting:

- a number under a unit-suffixed key — `capacity_rps`, `capacity_kqps`,
  `capacity_rpm` (and likewise `demand_rps`, etc.);
- an object `{"value": <number>, "unit": "<unit>"}`;
- a string `"<number> <unit>"`.

Recognized units and their conversion to rps:

- `rps` — requests per second (identity).
- `kqps` — thousands of queries per second. For these services one query is one
  request, so `1 kqps = 1000 rps`.
- `rpm` — requests per minute, i.e. `rps = rpm / 60`.

A field may appear in exactly one of the three forms; a field given in two forms
at once, a bare number with no unit, an unrecognized unit, or a missing
capacity/demand is a malformed report (see Section 5). Capacity and demand of the
**same** region are reported independently and frequently differ in unit — do not
assume a region uses one unit throughout.

## 3. Headroom and the safety limit

A region is considered **safe** only while its load stays at or below 90% of its
installed capacity. We call `0.9 × installed` the region's *usable* capacity; the
difference between usable capacity and current demand is its *headroom*. The 90%
figure is a hard reliability limit (it leaves margin for GC pauses, deploy
rollbacks, and measurement error) and is applied strictly: a region sitting at
*exactly* 90% is safe, and only a region strictly above 90% is in violation.

## 4. Single-region failover redistribution

We plan for the loss of **at most one region at a time**. When a region fails,
its steady-state demand must be taken up by the survivors, subject to all of the
following (together these determine the extra load on each survivor uniquely):

- No survivor may be driven above its usable capacity (Section 3).
- The load is shared in proportion to installed capacity: any two survivors that
  are not filled to their usable capacity take on extra load in the same ratio as
  their installed capacities. A survivor sitting at its usable capacity takes no
  further load, and the load it can no longer accept is shared among the
  survivors that still have headroom (again in proportion to installed capacity).
- As much of the failed region's demand is placed as the survivors can hold. If
  the survivors' combined headroom cannot hold all of it, the failover
  **overflows** and the demand cannot be fully absorbed.

Worked numbers illustrating this rule are in the INC-4471 post-incident review.

## 5. Reporting contract

The capacity tool reads one JSON report from standard input:
`{"regions": [ <region>, ... ]}`, each region carrying a `name` and a capacity
and demand encoded as in Section 2. It writes one JSON object to standard output
with **all quantities expressed in rps** and regions in input order:

```
{
  "anyViolation": <bool>,
  "anyOverflow": <bool>,
  "regions": [
    {"name", "capacity", "demand",
     "worstIncoming", "utilizationPct", "violates", "drBuffer", "overflowOnFailure"}
  ]
}
```

For each region, over the region's own steady state (no failure) and every
single-region failure it survives:

- `worstIncoming` — the highest total load the region reaches (at least its own
  demand). Because absorbed load is capped at usable capacity, a region exceeds
  90% only when its own steady-state demand already does.
- `utilizationPct` — `100 × worstIncoming / capacity`.
- `violates` — true iff `utilizationPct` is strictly greater than 90.
- `drBuffer` — the DR buffer, `worstIncoming / 0.9 − demand`: the extra capacity
  above steady-state demand needed to keep the region at or under 90% at its
  worst.
- `overflowOnFailure` — true iff the failure of *this* region overflows, i.e. the
  other regions' combined headroom is less than this region's demand.

`anyViolation` / `anyOverflow` are true iff any region violates / any single
failure overflows. Floating-point outputs are compared to a tolerance of 1e-6.

A report is invalid if it is not valid JSON, has fewer than two regions, or (after
converting to rps) has a non-positive capacity, a negative demand, a demand
greater than capacity, a duplicate region name, or a capacity/demand that is
missing or malformed per Section 2. On any invalid report the tool must exit with
a non-zero status and produce no JSON.

## 6. Change management (informational)

Capacity plans are reviewed quarterly. Emergency capacity grants require VP
approval and are exempt from the 90% limit for up to 72 hours. This exemption is
operational only and does not change the calculation defined above.
