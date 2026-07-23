# Build the hierarchical broker allocator with dynamic weights, global rebalancing, min, priority, rate limits and persistent credit

Implement a Go program at `/app/main.go` that fairly distributes messages across subscribers grouped into groups, with per-batch minimum guarantees, priority, per-batch rate limits, dynamic weight evolution, global rebalancing of unused capacity, and persistent credit-based fairness across batches. The allocator must also report final cumulative totals and final credits.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap g_rate   (G lines, groups 0..G-1 in input order)
S
gid prio min weight cap rate         (S lines, subs 0..S-1 in input order)
```

- `T` batches (≥1), each load ≥0, up to 1e12 (you may assume non-negative for this task; robust handling of zero is required)
- Groups: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited)
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited)
- Input may contain blank lines and extra spaces - parse robustly.

## Output format

Exactly `T+4` lines, no spaces, no trailing spaces:

- First `T` lines: each line `S` comma-separated ints in input order for that batch (per-subscriber batch allocation). Cumulative allocations never exceed caps.
- Line `T+1`: `G` comma-separated ints per-group cumulative totals after all batches.
- Line `T+2`: `S` comma-separated ints per-subscriber cumulative totals.
- Line `T+3`: `G` comma-separated ints per-group final persistent credits after all batches.
- Line `T+4`: `S` comma-separated ints per-subscriber final persistent credits.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective group caps (necessary for feasibility):** Per-member effective per-batch cap is `min(remaining total cap, per-batch rate if rate>0)`. Sum those per group as `sum_member_eff`. Effective remaining group cap is `min(group remaining total cap, sum_member_eff, group per-batch rate if rate>0, 0 if group has no members)`. If gid out of range, subscriber gets 0 and does not contribute. This is legitimate necessary spec.

- **I/O, persistence and 64-bit safety (necessary):** `T` loads, `G` groups, `S` subs, `T+4` lines output CSV in input order, state (cumulative totals, credits, weights) persists across batches. Credits start at weight, weights start at input weight. Loads, caps, weights and products like `remaining * credit` can be `1e12*1e12=1e24` and `3*4e18=1.2e19`, which overflow signed 64-bit. Proportional shares must be computed without 64-bit overflow using 128-bit techniques (e.g., `math/bits.Mul64`/`Div64` or equivalent mulDiv). This is necessary for overflow cases.

- **Min-phase deterministic order (necessary):** Minimums are allocated in priority order: higher priority first, tie by original input order (lower index wins). Each min allocation is capped to `min(min, effective cap remaining for that entity, load remaining, and also per-batch rate if rate>0)`. If load insufficient for all mins, higher priority gets its min first. If min>cap or min>rate, capped. Zero caps and rates produce zero allocation.

- **Weighted fair-share deterministic loop (necessary for byte-exact output):** After mins, remaining load is distributed in a multi-round loop. This exact loop is necessary to remove leftover/rounding ambiguity.

  - Maintain per-entity temporary credits, initialized to the persistent credits at start of batch.
  - Active set = entities where `allocated_in_weighted_phase < effective_remaining_cap_after_mins`.
  - Let `rem` be remaining load for this level, `total = sum(temp_credit[active])`.
  - If `total == 0`: (should never occur with correct decay because credits stay ≥1, but required for robustness/termination) perform bulk round-robin: find `minRem = min_{active} (cap - alloc)`, `cycles = min(minRem, rem // len(active))`. If cycles>0 allocate to all active. Then allocate 1 by 1 in input order while rem>0.
  - Else, for each active, compute proportional share `share = floor(rem * temp_credit / total)` using overflow-safe 128-bit mulDiv, capped to `cap - alloc`. Sum shares to `used`.
  - If `used == 0`: progress guarantee – select entity with highest temp_credit, tie by lowest original index, give it 1.
  - Subtract `used` from `rem`.
  - Update temporary credits for this round: if entity received >0 in this round then `temp_credit = floor(temp_credit/2)+1`, else `temp_credit += weight` where weight is current persistent weight at start of batch (not yet evolved).
  - Repeat until `rem==0` or no active.

- **Dynamic weight evolution (necessary for multi-batch fairness):** In addition to credits, persistent weights evolve deterministically:

  - `weight` starts at input weight.
  - After each batch, for any entity whose effective cap at batch start was >0:
    - If its total allocation in batch (mins+weighted) >0: `weight = max(1, floor(weight * 0.9))`
    - Else: `weight = weight + 1`
  - This exact formula is necessary for determinism; weight affects future credit growth (when not receiving, credit grows by weight).

- **Persistent credit update (necessary):** After each batch, for any entity whose effective cap at batch start was >0:
  - If allocation >0: `credit = floor(credit/2)+1`
  - Else: `credit = credit + weight_old` where `weight_old` is weight at start of batch before evolution.
  Credits stay ≥1 for correct implementation.

- **Global rebalancing loop (necessary for feasibility):** For each batch with load `L`:

  ```
  remaining = L
  group_batch = 0 per group, sub_batch = 0 per sub for this batch
  loop:
    recompute remaining effective caps for this batch:
      g_rem = group_cap - group_total - group_batch
      s_rem = sub_cap - sub_total - sub_batch
      s_eff = min(s_rem, rate if rate>0 else s_rem)
      sum_member_eff per group = sum s_eff
      eff_g_rem = min(g_rem, sum_member_eff, rate if rate>0 else g_rem), 0 if no members
      eff_g_iter = eff_g_rem (already remaining)
      s_eff_iter = s_eff (already remaining after sub_batch)
    if sum eff_g_iter ==0: break
    allocate remaining via primitive allocate_batch(remaining, groups with caps eff_g_iter) -> group_iter
    for each group with group_iter>0:
      allocate group_iter[g] to its members via primitive allocate_batch(group_iter[g], member caps s_eff_iter)
      actually_allocated = sum member allocs for that group
      group_batch[g] += actually_allocated
      sub_batch[subs in group] += member allocs
      remaining -= actually_allocated
    if no progress (actually_allocated total ==0): break
  discard any remaining unallocated load
  ```

  After loop, `group_total += group_batch`, `sub_total += sub_batch`. Then evolve credits and weights. This loop ensures unused capacity due to member caps is returned and reallocated.

- **Hierarchical order (necessary):** For each batch, first allocate to groups using effective caps and the above primitive+rebalancing, then per group to its subscribers in input order. Group-then-member order is required.

- **Determinism and efficiency (necessary):** All tie-breaking deterministic by original input order (lower wins), stable across batches. Must handle 1e12 scale and overflow cases efficiently.

## Fairness properties (requires engineering judgement)

- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Effective caps incorporate rate limits.

- **Credit-decay + dynamic weight intuition:** Credits decay when served, grow by weight when not. Weights decay 10% when served else grow +1, making long-starved entities heavier. Combined yields fair share with memory, but exact formulas above are pinned for determinism.

- **Conservation:** Cumulative totals never exceed caps, per-batch never exceeds effective caps.

## Examples (all matching oracle, no hedging)

### Example 1 - hierarchical basic, rate unlimited (T+4 lines)

Input:
```
1
16
2
10 0 5 10 0
5 0 3 10 0
4
0 10 0 5 6 0
0 5 0 3 9 0
1 5 0 4 3 0
1 1 0 1 12 0
```
Output:
```
6,4,3,3
10,6
6,4,3,3
3,2
3,2,3,1
```
Explanation: group totals 10,6, sub totals 6,4,3,3, final credits groups 3,2 subs 3,2,3,1.

### Example 2 - min and priority with rate limiting

Input:
```
1
9
2
0 0 5 10 0
0 0 6 10 0
3
0 10 2 5 10 2
0 5 1 6 10 10
1 1 0 6 1 0
```
Output:
```
2,6,1
8,1
2,6,1
3,4
3,4,4
```
Group totals 8,1 (effective caps limited by sum member eff and rate), sub totals same as batch, final credits reflect decay.

### Example 3 - multi-batch persistent credit + dynamic weight

Input:
```
2
6
6
2
0 0 4 11 0
0 0 1 6 0
3
0 5 0 4 11 0
0 1 0 1 6 0
1 10 0 2 5 0
```
Output:
```
4,1,1
4,1,1
10,2
8,2,2
2,1
2,1,2
```

### Example 4 - large weight overflow (64-bit safety)

Input:
```
1
1000000000000
1
0 0 1000000000000 1000000000000 0
2
0 0 0 1000000000000 500000000000 0
0 0 0 1000000000000 500000000000 0
```
Output:
```
500000000000,500000000000
1000000000000
500000000000,500000000000
500000000001
500000000001,500000000001
```
Group total 1e12, sub totals same as batch, final credits 500000000001 each (overflow-safe mulDiv required because 1e12*1e12=1e24).

### Example 5 - blank lines robust

Input:
```
1

16

2
10 0 5 10 0
5 0 3 10 0
4
0 10 0 5 6 0
  0 5 0 3 9 0
1 5 0 4 3 0
1 1 0 1 12 0

```
Output:
```
6,4,3,3
10,6
6,4,3,3
3,2
3,2,3,1
```
