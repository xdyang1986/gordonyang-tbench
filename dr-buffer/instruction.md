 A service is deployed across multiple regions, each with an installed capacity and a steady-state demand (QPS). If a single region fails, its demand is redistributed to the surviving regions. A region is safe only while its load stays at or below 90% of its installed capacity (usable capacity = 0.9 × installed).

A region's DR buffer is the extra capacity above its steady-state demand it must hold so that, under its worst single-region failure, it stays within the 90% threshold.

Failure model: at most one region fails at a time.

  Input
  JSON on stdin:

  ```json
  {
    "regions": [
      {"name": "R1", "capacity": 1000, "demand": 600},
      {"name": "R2", "capacity": 800,  "demand": 500},
      {"name": "R3", "capacity": 600,  "demand": 400},
      {"name": "R4", "capacity": 400,  "demand": 300}
    ]
  }
  ```

  - `capacity` > 0, `0 ≤ demand ≤ capacity`, region names unique, at least 2 regions.

    Redistribution
    When a region `f` fails, its `demand` is taken up by the surviving regions. The extra load placed on each
    survivor is whatever makes all of the following hold at once — together they determine it uniquely:

    - No survivor may be driven above its usable capacity (0.9 × installed).
    - The load is shared in proportion to installed capacity: any two survivors that are not filled to their usable
      capacity receive extra load in the same ratio as their installed capacities (`add_i / capacity_i` is equal for all
      such survivors). A survivor sitting at its usable capacity takes no further load.
    - As much of the failed region's demand is placed as the survivors can hold. If their combined spare capacity below
      the usable limit cannot hold all of it, the failure **overflows** and the demand cannot be fully absorbed.

  Computation
  For each region `f` that fails,  compute the extra load each survivor absorbs by the redistribution rule above. For each region, consider the worst single failure it survives:

  - `worstIncoming` = the highest total load the region reaches across all single failures (at least its own demand).
  - `utilizationPct` = 100 × worstIncoming / capacity.
  - `violates` = true iff utilizationPct is strictly greater than 90.
  - `drBuffer` = worstIncoming / 0.9 − demand.
 - `overflowOnFailure` = true iff the failure of region `i` itself overflows (the other regions' combined headroom is less than region `i`'s demand).

  Output
  JSON on stdout, regions in input order:

  ```json
    {
      "anyViolation": false,
      "anyOverflow": true,
      "regions": [
        {"name": "R1", "capacity": 100, "demand": 85,
         "worstIncoming": 90, "utilizationPct": 90, "violates": false, "drBuffer": 15, "overflowOnFailure": false}
      ]
    }
  ```

  `anyViolation` is true iff any region violates. `anyOverflow` is true iff any single-region failure overflows. Floating-point values are compared with a tolerance of 1e-6.

  Invalid Input
  Exit non-zero (no JSON output) for: malformed JSON, fewer than two regions, non-positive capacity, negative demand, demand greater than capacity, or
  duplicate region names.

  Build Contract
  - Language: Go
  - Module root: /app
  - Build: cd /app && go build -o /app/drbuffer .
