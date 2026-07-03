# Global Traffic & Capacity Policy (Reliability Standards, rev. 13)

  This document is the authoritative reliability standard for multi-region
  services. It supersedes rev. 12 and the regional addenda circulated last quarter.
  Questions to #traffic-eng. Sections 1 and 7 are retained for audit completeness
  and are not required reading for the capacity calculation.

  ## 1. Service-level objectives (informational)

  Every tier-1 service commits to a p99 latency SLO of 250 ms at the edge, an
  availability SLO of 99.95% per calendar month, and an error budget burned by 5xx
  responses and by requests shed under load. These SLOs are tracked separately by
  the SRE on-call rota and are **not** inputs to the capacity math below.

  ## 2. Reporting units (read carefully)

  Teams report capacity and demand in whatever unit their dashboards default to.
  The capacity tool must accept, **per field**, any of the following and convert
  everything to **requests per second (rps)**, the canonical unit for all output:

  - a number under a unit-suffixed key — `capacity_rps`, `capacity_kqps`,
    `capacity_rpm` (and likewise `demand_rps`, etc.);
  - an object `{"value": <number>, "unit": "<unit>"}`;
  - a string `"<number> <unit>"`.

  Recognized units and their conversion to rps:

  - `rps` — requests per second (identity).
  - `kqps` — thousands of queries per second; one query is one request here, so
    `1 kqps = 1000 rps`.
  - `rpm` — requests per minute, i.e. `rps = rpm / 60`.

  A field may appear in exactly one of the three forms. A field given in two forms
  at once, a bare number with no unit, an unrecognized unit, or a missing
  capacity/demand is a malformed report (Section 6). Capacity and demand of the
  **same** region are reported independently and frequently differ in unit.

  ## 3. The failure envelope

  Capacity plans must withstand the simultaneous loss of up to **`maxFailures`**
  regions, a positive integer supplied at the top level of the report alongside
  `regions`. We therefore reason about **every** set of initially-failing regions
  whose size is between 1 and `maxFailures` inclusive. `maxFailures` must be at
  least 1 and at most one less than the number of regions (there must always be at
  least one survivor at the outset).

  ## 4. Redistribution, the safety limit, and cascades

  A region is **safe** only while its load stays at or below 90% of its installed
  capacity; we call `0.9 × installed` its *usable* capacity. The limit is applied
  strictly — a region at exactly 90% is safe, only *strictly above* 90% is a
  violation.

  When a set of regions is down, the demand that was theirs is carried by the
  regions still alive, **in proportion to installed capacity and with no ceiling**:
  an alive region `i` ends up carrying

  ```
  load_i = demand_i + (sum of demand over all down regions) × capacity_i
                      ────────────────────────────────────────────────────
                                (sum of capacity over all alive regions)
  ```

  If that load pushes an alive region *strictly above* its usable capacity, that
  region is itself overwhelmed and **fails too** — a cascade. All regions that are
  over the limit fail together, their demand joins the pool, and the load is
  recomputed over the regions that remain; this repeats until either every alive
  region is within its usable capacity (the cascade has settled) or nothing is left
  (a total collapse). A worked example is in the INC-4471 post-incident review.

  ## 5. Reporting contract

  The tool reads one JSON report from standard input —
  `{"maxFailures": <int>, "regions": [ <region>, ... ]}` — and writes one JSON
  object to standard output with **all loads and capacities in rps**:

  ```
  {
    "maxFailures": <int>,
    "resilient": <bool>,
    "capacityShortfall": <number>,
    "worstScenario": {"failed": [name,...], "collapsed": [name,...], "cascadeRounds": <int>},
    "regions": [
      {"name", "capacity", "demand", "worstIncoming", "utilizationPct", "violates", "drBuffer"}
    ]
  }
  ```

  Per region, considering the region's own steady state and the **immediate**
  post-failure load (before any cascade) over every failing set of size 1..
  `maxFailures` that does not contain the region:

  - `worstIncoming` — the highest such load the region reaches (at least its own
    demand).
  - `utilizationPct` — `100 × worstIncoming / capacity`.
  - `violates` — true iff `utilizationPct` is strictly greater than 90 (i.e. some
    failure within the envelope would overwhelm this region).
  - `drBuffer` — `worstIncoming / 0.9 − demand`, the extra capacity above
    steady-state demand that would hold the region at 90% under its worst
    immediate load.

  Region order follows the input. The aggregate fields:

  - `resilient` — true iff no region violates (no failure within the envelope
    overwhelms anyone, so no cascade is possible).
  - `capacityShortfall` — the total extra installed capacity the fleet lacks, in
    rps: sum over regions of `max(0, worstIncoming / 0.9 − capacity)`. Zero exactly
    when the fleet is resilient.
  - `worstScenario` — the failing set (of size 1..`maxFailures`) whose cascade ends
    with the most regions down; ties broken by the **fewest** initially-failing
    regions, then by the set that is lexicographically smallest in input order.
    `failed` is that initial set, `collapsed` is every region down once the cascade
    settles (both in input order), and `cascadeRounds` is the number of additional
    failure waves the cascade produced (0 if the initial failure overwhelms no one).
    When the fleet is resilient, report `{"failed": [], "collapsed": [],
    "cascadeRounds": 0}`.

  Floating-point outputs are compared to a tolerance of 1e-6.

  ## 6. Invalid reports

  A report is invalid if it is not valid JSON, is missing `maxFailures` or has one
  outside `1..(regions−1)`, has fewer than two regions, or (after converting to
  rps) has a non-positive capacity, a negative demand, a demand greater than
  capacity, a duplicate region name, or a capacity/demand that is missing or
  malformed per Section 2. On any invalid report the tool exits non-zero and prints
  no JSON.

  ## 7. Change management (informational)

  Capacity plans are reviewed quarterly. Emergency capacity grants require VP
  approval and are exempt from the 90% limit for up to 72 hours; the exemption is
  operational only and does not change the calculation above.
