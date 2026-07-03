# Post-Incident Review — INC-4471

**Title:** Regional loss of `ap-south` and the failover it triggered
**Severity:** SEV2 (no customer impact; capacity held)
**Status:** Resolved — filed as a reference example for the capacity standard.

## Summary

`ap-south` dropped out for 11 minutes after a power event. Traffic failed over to
the surviving regions and everything held, but the review board asked us to write
up the arithmetic because two on-call engineers disagreed about how the load
*should* have been redistributed. The numbers below are now the canonical worked
example referenced by the capacity policy (Section 4).

## Fleet at the time of the incident

Capacity and demand were pulled straight from each team's dashboard, so — as
usual — the units are all over the place. Normalizing everything to rps:

| Region     | Reported capacity   | Reported demand         | Capacity (rps) | Demand (rps) |
|------------|---------------------|-------------------------|----------------|--------------|
| `us-east`  | `capacity_rps: 100` | `"5100 rpm"`            | 100            | 85           |
| `eu-west`  | `{value:0.3,kqps}`  | `demand_rps: 100`       | 300            | 100          |
| `ap-south` | `"6000 rpm"`        | `{value:60, unit:rps}`  | 100            | 60           |

Recall `1 kqps = 1000 rps` and `rps = rpm / 60`, applied per field.

## What happened when `ap-south` failed

`ap-south` was serving **60 rps**. That 60 had to land on the two survivors,
`us-east` and `eu-west`.

The usable ceiling is 90% of installed capacity, so before the failover each
survivor's headroom was:

- `us-east`: usable `90`, demand `85` → **headroom 5**
- `eu-west`: usable `270`, demand `100` → **headroom 170**

The naive move — and what the first engineer proposed — is to split the 60 purely
in proportion to installed capacity (100 : 300), i.e. 15 to `us-east` and 45 to
`eu-west`. **That is wrong:** it would push `us-east` to `85 + 15 = 100 rps`, which
is 100% of installed — well past the 90% limit.

The correct redistribution respects the ceiling. `us-east` can only take its 5 rps
of headroom, so it fills to exactly `90` and stops there. The remaining
`60 − 5 = 55` rps then spills onto `eu-west` (the only survivor with headroom
left), taking it to `100 + 55 = 155 rps` (well under its 270 usable). Final state:

- `us-east`: `90 rps` (at its 90% ceiling — safe, not a violation)
- `eu-west`: `155 rps`

So across `us-east`'s worst surviving failure it peaks at `90 rps`; its DR buffer
— the extra capacity above steady-state demand needed to stay at/under 90% — is
`90 / 0.9 − 85 = 15 rps`.

## The counterfactual the board cared about

The second engineer asked: what if `eu-west` had been the one to fail? `eu-west`
was carrying **100 rps**, and the only survivors would have been `us-east` and
`ap-south`, whose combined headroom was just `5 + 30 = 35 rps`. There is nowhere to
put the other `65 rps`. That case would **overflow** — the fleet could not absorb
the loss of `eu-west` within the 90% limit. (It didn't happen; `ap-south` failed,
not `eu-west`.) This is exactly the "overflow" condition in the policy: a region's
failure overflows when the other regions' combined headroom is less than that
region's demand.

## Follow-ups

- [done] Add `ap-south` PDU redundancy.
- [done] Codify this arithmetic as the reference example for the capacity tool.
- [wontfix] Auto-normalize dashboard units at source — owning teams declined.
