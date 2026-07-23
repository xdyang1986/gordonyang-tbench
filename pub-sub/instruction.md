# Build the ultimate hierarchical broker allocator with burst, dynamic weights, negative rebalancing, min, priority, rate limits and persistent credit

Implement a Go program at `/app/main.go` that fairly distributes messages across subscribers grouped into groups, handling per-batch minimum guarantees, priority, per-batch rate limits with one-time burst allowances, dynamic weight evolution, global rebalancing of unused capacity, negative loads (deallocation), and persistent credit-based fairness across batches. Must also report final cumulative totals, final credits and final burst remaining.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap g_rate g_burst   (G lines, groups 0..G-1 in input order)
S
gid prio min weight cap rate burst           (S lines, subs 0..S-1 in input order)
```

- `T` batches (≥1), each load may be positive, zero, or negative (negative means deallocation, returns capacity), up to ±1e12. Must handle zero correctly.
- Groups: priority int (higher = higher), min ≥0 per-batch minimum, weight ≥1, cap ≥0 total across batches, rate ≥0 per-batch max (0 = unlimited), burst ≥0 one-time extra allowance that can be used to exceed rate, not replenished.
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited), burst ≥0 one-time extra.
- Input may contain blank lines and extra spaces - parse robustly.

## Output format

Exactly `T+6` lines, no spaces, no trailing spaces, in input order:

- First `T` lines: per-subscriber batch allocation for that batch (positive for allocation, negative for deallocation) as `S` comma-separated ints.
- Line `T+1`: per-group cumulative totals after all batches as `G` CSV.
- Line `T+2`: per-subscriber cumulative totals as `S` CSV.
- Line `T+3`: per-group final persistent credits after all batches as `G` CSV.
- Line `T+4`: per-subscriber final persistent credits as `S` CSV.
- Line `T+5`: per-group final burst remaining as `G` CSV.
- Line `T+6`: per-subscriber final burst remaining as `S` CSV.

Cumulative totals never exceed caps and never go below 0 (deallocation respects totals). Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective group caps (necessary):** Per-member effective per-batch cap is `min(remaining total cap, rate+burst_rem if rate>0 else remaining)`. Sum those per group as `sum_member_eff`. Effective remaining group cap is `min(group remaining total cap, sum_member_eff, rate+burst_rem if rate>0 else group remaining, 0 if group has no members)`. If gid out of range, subscriber gets 0 and does not contribute.

- **I/O, persistence and 64-bit safety (necessary):** `T` loads, `G`, `S`, `T+6` lines output, state (totals, credits, weights, burst_rem) persists across batches. Credits start at weight, weights start at input weight, burst_rem starts at burst. Products like `remaining * credit` can be `1e12*1e12=1e24` and `3*4e18=1.2e19`, overflow signed 64-bit. Must use 128-bit mulDiv.

- **Min-phase deterministic order (necessary):** Minimums allocated in priority order: higher priority first, tie by original index. Each min capped to `min(min, effective cap remaining, load remaining)`. If load insufficient, higher priority first. If min>cap or min>rate+burst_rem, capped. Zero caps/rates produce zero.

- **Weighted fair-share deterministic loop (necessary):** After mins, remaining load distributed multi-round:

  - Temp credits initialized to persistent credits at start of batch (or start of outer iteration for global rebalancing, but spec requires persistent credits at batch start for first iteration).
  - Active = `alloc < effective_remaining_cap_after_mins`.
  - `rem` = remaining load, `total = sum(temp_credit[active])`.
  - If `total==0`: bulk RR fallback: `minRem = min cap-alloc`, `cycles = min(minRem, rem//len(active))`, allocate cycles to all active, then 1-by-1 input order.
  - Else share = `floor(rem * temp_credit / total)` via overflow-safe mulDiv capped to `cap-alloc`. `used=sum share`. If `used==0`: give 1 to highest temp_credit tie lowest idx.
  - `rem -= used`, update temp credits: if `delta>0` then `temp= temp/2+1` else `temp+=weight` (weight = current persistent weight at batch start).
  - Repeat until rem==0 or no active.

- **Dynamic weight evolution (necessary):** After each batch, for any entity whose effective cap at batch start >0 (or for deallocation, whose total before batch >0):

  - If allocation in batch !=0: `weight = max(1, floor(weight*0.9))` (integer `weight*9/10`).
  - Else: `weight = weight+1`.
  This formula is necessary for determinism.

- **Persistent credit update (necessary):** After each batch, for any eligible entity:

  - If allocation !=0: `credit = floor(credit/2)+1`
  - Else: `credit = credit + weight_old` where `weight_old` is weight at start of batch.
  Credits stay ≥1.

- **Burst consumption (necessary):** Burst_rem is one-time extra to exceed rate. For positive allocation batches, after final batch allocation per entity is determined, if `rate>0` and `batch_alloc > rate`, then `excess = batch_alloc - rate`, consume `min(excess, burst_rem)`, `burst_rem -= consumed`. Burst not replenished, not affected by deallocation. Effective cap already includes `rate+burst_rem`, so burst allows exceeding rate up to that.

- **Global rebalancing loop (necessary):** For each positive batch load L:

  ```
  remaining = L, group_batch=0, sub_batch=0, firstIter=true
  for iter 0..9 while remaining>0:
    g_rem = cap - total - group_batch
    s_rem = cap - total - sub_batch
    s_eff = min(s_rem, rate+burst_rem - sub_batch if rate>0 else s_rem) with floor 0
    sum_member_eff per group = sum s_eff
    eff_g = min(g_rem, sum_member_eff, rate+burst_rem - group_batch if rate>0 else g_rem), 0 if no members
    if sum eff_g ==0 break
    groupItems caps eff_g, mins = groupMin if firstIter else 0, credits = persistent credits, weights
    groupIter = primitive(remaining, groupItems)  (returns per-group share for this iter)
    totalThisIter=0
    for each group with groupIter>0:
      members of group, s_eff_iter = s_eff for those members
      mItems caps s_eff_iter, mins = subMin if firstIter else 0
      subIter = primitive(groupIter[g], mItems)
      sumSub = sum subIter
      group_batch[g] += sumSub
      sub_batch[member] += subIter
      totalThisIter += sumSub
    remaining -= totalThisIter
    firstIter=false
    if totalThisIter==0 break
  discard remaining
  ```

  After loop, update totals, then burst consumption, then credit/weight evolution. This ensures unused capacity due to member caps is reallocated.

- **Negative loads (deallocation) (necessary):** If load <0, let N=-load to deallocate. Deallocate by priority:

  - Order groups by priority desc tie idx.
  - For each group, remaining deallocatable = `groupTotal + groupBatch` (groupBatch negative, so this is still available to deallocate).
  - Collect its members ordered by priority desc tie idx.
  - For each member, `sPossible = subTotal + subBatch`, dealloc = `min(sPossible, remaining, groupTotal+groupBatch)`, apply negative to subBatch and groupBatch, `remaining -= dealloc`.
  - Any leftover N that cannot be deallocated due to totals already 0 is discarded (totals never <0).
  - Burst not affected.
  - Credit/weight evolution counts deallocation as activity (alloc !=0).

- **Hierarchical order (necessary):** Group level then per-group members, input order deterministic.

- **Determinism and efficiency (necessary):** Tie-breaking lower original index wins, stable, must handle 1e12 and overflow efficiently.

## Fairness properties (engineering judgement)

- Rate limiting with burst is token-bucket-like but one-time, not replenished. Effective caps already include burst.
- Dynamic weight makes long-unserved entities heavier, served lighter, plus credit decay yields fair share with memory.
- Conservation: totals respect caps and never negative.

## Examples (all matching oracle, no hedging)

### Example 1 - basic hierarchical with burst 0

Input:
```
1
16
2
10 0 5 10 0 0
5 0 3 10 0 0
4
0 10 0 5 6 0 0
0 5 0 3 9 0 0
1 5 0 4 3 0 0
1 1 0 1 12 0 0
```
Output:
```
6,4,3,3
10,6
6,4,3,3
3,2
3,2,3,1
0,0
0,0,0,0
```

### Example 2 - min, priority, rate limiting with burst

Input:
```
1
9
2
0 0 5 10 0 0
0 0 6 10 0 0
3
0 10 2 5 10 2 0
0 5 1 6 10 10 0
1 1 0 6 1 0 0
```
Output:
```
2,6,1
8,1
2,6,1
3,4
3,4,4
0,0
0,0,0
```

### Example 3 - multi-batch persistent credit + dynamic weight

Input:
```
2
6
6
2
0 0 4 11 0 0
0 0 1 6 0 0
3
0 5 0 4 11 0 0
0 1 0 1 6 0 0
1 10 0 2 5 0 0
```
Output:
```
4,1,1
4,1,1
10,2
8,2,2
2,1
2,1,2
0,0
0,0,0
```

### Example 4 - burst exceeds rate

Input:
```
1
5
1
0 0 1 10 2 3
2
0 0 0 1 10 2 3
0 0 0 1 10 0 0
```
Output:
```
3,2
5
3,2
1
1,1
0
2,0
```
Explanation: group rate 2 burst 3 allows up to 5, so load 5 fully allocated. Sub0 rate2 burst3 allows 5 but cap limited to 3 due to fair share? Actually sub0 gets 3 (2 rate +1 burst consumed), sub1 gets 2 (rate limited). Final burst remaining: group burst 3 consumed 3 (excess 3) ->0, sub0 burst 3 consumed1 ->2, sub1 burst0 ->0.

### Example 5 - negative load deallocation

Input:
```
2
6
-4
1
0 0 2 10 0 0
2
0 5 0 2 10 0 0
0 1 0 2 10 0 0
```
Output:
```
3,3
-3,-1
2
0,2
2
2,2
0
0,0
```
First batch allocates 3,3 (fair share with equal weight/credit), second deallocates -3,-1 (higher priority first deallocates more), final totals 0,2 (sub0 deallocated fully to 0, sub1 has 2 remaining), group total 2.

### Example 6 - large overflow + burst

Input:
```
1
1000000000000
1
0 0 1000000000000 1000000000000 0 0
2
0 0 0 1000000000000 500000000000 0 0
0 0 0 1000000000000 500000000000 0 0
```
Output:
```
500000000000,500000000000
1000000000000
500000000000,500000000000
500000000001
500000000001,500000000001
0
0,0
```

### Example 7 - blank lines robust

Input:
```
1

16

2
10 0 5 10 0 0
5 0 3 10 0 0
4
0 10 0 5 6 0 0
  0 5 0 3 9 0 0
1 5 0 4 3 0 0
1 1 0 1 12 0 0

```
Output:
```
6,4,3,3
10,6
6,4,3,3
3,2
3,2,3,1
0,0
0,0,0,0
```
