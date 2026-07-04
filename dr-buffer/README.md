# codimango/dr-buffer

## Description

A **debugging** task. The container ships a complete, working-but-buggy Go
program at `/app/main.go` — a disaster-recovery capacity planner — and the agent
must **localize and fix its defects in place** (no rewrite, no written spec to
rewrite from). Correct behavior is defined by the shipped source minus its bugs,
plus a bug report (`instruction.md`) with two concrete failing repros and their
expected outputs.

The program reads a JSON report on **stdin**
(`{"maxFailures": K, "regions": [...]}`) and prints a JSON summary on **stdout**.
Its logic is intentionally non-trivial:

- **Mixed-unit, multi-encoding input** — each region's `capacity`/`demand` may be
  a unit-suffixed number (`capacity_rps`/`capacity_kqps`/`capacity_rpm`), a
  `{value,unit}` object, or a `"<number> <unit>"` string, and units differ per
  field (`rps`, `kqps`=1000 rps, `rpm`=rps/60); all normalized to rps.
- **Failure envelope** — every failing set of size `1..maxFailures` is considered
  (bitmask subset enumeration).
- **Uncapped proportional redistribution + cascade** — a down region's demand is
  spread over survivors proportional to installed capacity; a survivor pushed
  strictly above its 90% usable capacity is itself overwhelmed and fails, and the
  load is recomputed over what remains, until the cascade settles or the fleet
  collapses.
- **Outputs** — per region `worstIncoming` / `utilizationPct` / `violates` /
  `drBuffer`, plus the fleet's `resilient` flag, total `capacityShortfall`, and
  the `worstScenario` (initial failures, collapsed set, cascade rounds) with
  deterministic tie-breaks.

The two shipped defects are subtle and localized:

1. the cascade overload test uses `>=` (with a flipped `eps`), so a region at
   *exactly* its 90% usable capacity is wrongly treated as overwhelmed — spurious
   cascades — instead of a strict `>`; and
2. `capacityShortfall` drops its `max(0, .)` guard, so regions with slack drag
   the total negative (even a fully-resilient fleet reports a negative shortfall).

Both are discoverable from the two failing repros in `instruction.md` combined
with the code's own (accurate) comments, but require reading and understanding the
cascade + shortfall logic to fix without breaking the many other behaviors.

## Completion Rates

Cloud validation (codimango), commit `7332e1f` — **Validation Passed**, k=5.

| Check / Model | Result |
|---|---|
| Structural checks | PASS (9/9) |
| Oracle | 3/3 |
| Avocado (validation-gate) | **4/5** — not trivial |
| opus (claude-opus-4-6) | **5/5** |
| gpt-5.5 | **5/5** |
| AI assessment | Accept (0 Critical / 0 High) |
| Contamination | MEDIUM |
| Provenance | clean |

**Primary differentiator:** the difficulty is *debug-in-place*, not greenfield
implementation. Earlier iterations of this task as a "write it from a spec" CLI
were solved by every model (avocado 5/5) no matter how elaborate the algorithm —
a precise, deterministic spec is exactly what frontier models are strongest at.
Removing the spec and requiring the agent to find two subtle boundary/guard bugs
in existing code is what moved avocado off a perfect score while keeping the task
solvable by stronger models.

## Anti-Cheating Analysis

- **Hardcoded outputs:** The verifier rebuilds the agent's Go source and drives
  the compiled binary on many fresh inline inputs (single- and multi-failure
  cascades, exact-90% boundaries, resilient fleets, mixed unit/encoding
  combinations, and a full validation battery), asserting exact numerics at
  `TOL=1e-6` and exact `worstScenario` sets — there is no fixed output to print.
- **Overfitting to the repros:** Fixing only the two example cases without fixing
  the underlying bugs fails the hidden suite, which covers many more scenarios
  than the bug report shows by example.
- **Modifying test files:** No test files ship in the container; the suite runs
  only at verify time, outside `/app`, and the agent cannot see or alter it.
- **Rewriting instead of debugging:** There is no written specification in the
  container to rewrite from — behavior is defined by the shipped source, so the
  agent must actually read and repair it. The reference `solve.sh` (the corrected
  program) is not agent-visible.
