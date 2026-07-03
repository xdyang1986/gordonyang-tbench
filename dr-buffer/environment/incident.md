# Post-Incident Review — INC-4471

  **Title:** Loss of `core-1` cascaded into `core-2`
  **Severity:** SEV2 (held on the edge tier; no customer impact)
  **Status:** Resolved — retained as the canonical cascade example for the policy.

  ## Summary

  `core-1` dropped out after a power event. Its traffic failed over, immediately
  overwhelmed `core-2`, and `core-2` fell too before the edge tier soaked up the
  rest. Nothing else tipped over. The board asked for the arithmetic because it is
  the clearest example we have of a *cascade*, which the capacity policy (Section 4)
  now defines.

  ## Fleet at the time (maxFailures = 1)

  Capacity and demand were pulled from each team's dashboard, so the units are
  mixed. Normalizing every field to rps:

  | Region   | Reported capacity      | Reported demand          | Capacity (rps) | Demand (rps) |
  |----------|------------------------|--------------------------|----------------|--------------|
  | `core-1` | `"12000 rpm"`          | `{value:150, unit:rps}`  | 200            | 150          |
  | `core-2` | `capacity_rps: 200`    | `"9000 rpm"`             | 200            | 150          |
  | `edge`   | `{value:0.4, kqps}`    | `demand_rps: 40`         | 400            | 40           |

  Recall `1 kqps = 1000 rps` and `rps = rpm / 60`, applied per field.

  ## The cascade

  `core-1` was serving **150 rps**. It failed. Its 150 was redistributed across the
  survivors `core-2` and `edge` **in proportion to installed capacity, with no
  ceiling**:

  - `core-2`: `150 + 150 × 200 / (200 + 400) = 150 + 50 = 200 rps`
  - `edge`:   `40  + 150 × 400 / (200 + 400) = 40 + 100 = 140 rps`

  `core-2`'s usable capacity is `0.9 × 200 = 180`. At **200 rps it is strictly over
  the limit**, so `core-2` is overwhelmed and fails too — that is the cascade. Its
  demand now joins the pool. With only `edge` left, the whole `150 + 150 = 300 rps`
  lands on it:

  - `edge`: `40 + 300 × 400 / 400 = 40 + 300 = 340 rps`

  `edge`'s usable capacity is `360`, so `340` is within limit and the cascade
  settles. Final tally: `core-1` and `core-2` are down; `edge` survives. Losing one
  region collapsed two, in **one** cascade round.

  ## How this maps to the report

  - `core-2`'s worst *immediate* load (before the cascade) is `200 rps`, i.e.
    `100%` utilization — over 90%, so `core-2` **violates**. Same for `core-1`
    under the symmetric failure. `edge` never exceeds its limit, so it does not
    violate.
  - Because at least one region violates, the fleet is **not resilient**. Each core
    would need `200 / 0.9 − 200 ≈ 22.22 rps` more installed capacity to hold at 90%
    under its worst immediate load, so the fleet's `capacityShortfall` is about
    `44.44 rps` (the edge needs none).
  - The `worstScenario` here is `failed: ["core-1"]`, `collapsed:
    ["core-1", "core-2"]`, `cascadeRounds: 1`.

  ## Follow-ups

  - [done] Add `core-1` PDU redundancy and rebalance core capacity.
  - [done] Codify this cascade as the reference example for the capacity tool.
  - [wontfix] Auto-normalize dashboard units at source — owning teams declined.
