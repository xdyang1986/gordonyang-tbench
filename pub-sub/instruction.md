# Build the hierarchical broker allocator with burst, cost, dynamic weights, negative rebalancing

Implement a Go program at `/app/main.go` that fairly distributes messages across subscribers grouped into groups, handling per-batch minimum guarantees, priority, per-batch rate limits with burst, per-message cost factor, dynamic weight evolution, global rebalancing, negative loads (deallocation), and persistent credit.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap g_rate g_burst   (G lines)
S
gid prio min weight cap rate burst cost     (S lines)
```

- `T` batches ≥1, each load may be positive, zero, or negative (negative = deallocation) up to ±1e12.
- Groups: prio, min≥0, weight≥1, cap≥0 total cost budget (count-approximated as `floor(remainingCost / minCostInGroup)` for effective group count; see Effective caps), rate≥0 per-batch max count (0=unlimited), burst≥0 one-time extra count beyond rate.
- Subs: gid, prio, min, weight, cap total cost, rate max count per batch (0 unlimited), burst, cost≥1 per-message cost (each message consumes cost from subscriber cap; subscriber cumulative cost never exceeds cap).
- Blank lines and extra spaces robust. Must accept old formats: groups 5 fields burst0, subs 6 fields burst0 cost1, 7 fields burst given cost1.

## Output format

Exactly `T` lines, each line S comma-separated ints in input order for that batch (counts, may be negative for deallocation), no spaces. Subscriber cumulative cost never exceeds caps; group caps are count-approximated via `floor(cap / minCostInGroup)` and may be exceeded in heterogeneous-cost cases.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective caps with burst+cost:** `sRemCost = cap - totalCost`, `sRemCount = floor(sRemCost/cost)`, `sEffCount = min(sRemCount, rate+burst_rem if rate>0)`. Sum per group `sumMemberEff`. Group `gRemCost = cap - totalCost`, `minCostInGroup = min cost among members`. `gRemCount = floor(gRemCost/minCost)` if has members else 0 — group caps are count-approximated as `floor(cap / minCostInGroup)` (not cost-exact; heterogeneous-cost groups may have sum(memberCost) > group cap). Effective group count `effG = min(gRemCount, sumMemberEff, rate+burst_rem if rate>0, 0 if no members)`. Invalid gid →0 and excluded from group totals AND credit/weight/burst updates (final values equal initial).

- **Backward-compat parsing:** Must accept older formats: 5-field groups behave as burst 0, 6-field subs as burst0 cost1, 7-field subs as cost1. Legacy formats must produce identical output to full-field equivalents (e.g., 5-field group `0 0 1 10 0` as `0 0 1 10 0 0`, 6-field sub `0 10 0 1 5 0` as `0 10 0 1 5 0 0 1`, 7-field sub `0 10 0 1 5 0 0` as `0 10 0 1 5 0 0 1`).

- **I/O, persistence, 64-bit safety:** State persists across batches: total cost, credit start weight, weight start weight, burst_rem start burst. `rem*credit` up to 1e24 and 1.2e19 overflow, need 128-bit mulDiv via `math/bits`.

- **Min-phase order:** Priority desc tie original idx asc. Each min capped to `min(min, effCap, rem)`.

- **Weighted deterministic loop (necessary for byte-exact):**
  - After satisfying mins, remaining load goes to a multi-round weighted phase.
  - Maintain temporary credits, initially equal to persistent credits at batch start.
  - Active set = entities where allocated in this phase < effective count cap remaining after mins.
  - Each round:
    - `total = sum(tempCredit[active])`.
    - If `total==0`: bulk round-robin fallback – find smallest remaining per active, allocate full cycles `min(minRem, rem // len(active))` to all active, then 1-by-1 in input order.
    - Else: for each active, `share = floor(rem * tempCredit / total)` using overflow-safe `mulDiv` (handles 1e24 and 1.2e19), capped to remaining cap. `used = sum(share)`.
    - If `used==0`: progress guarantee – give 1 to entity with highest temp credit, tie lowest original index.
    - Subtract used from remaining.
    - Update temp credits for this round: if delta>0, `temp = floor(temp/2)+1`, else `temp += weight` (weight at batch start).
  - Repeat until remaining==0 or no active.
  - Persistent credit update after whole batch: if total batch allocation !=0, `credit = floor(credit/2)+1`, else `credit += weight_old`.

- **Dynamic weight evolution (necessary):**
  - Weight persists across batches, starts at input weight.
  - After each batch, if entity was eligible (effective count at batch start >0) and got allocation !=0 in batch: `weight = max(1, floor(mulDiv(weight,9,10)))` – i.e., `weight*9/10` floor, overflow-safe via mulDiv, then at least 1.
  - Else if eligible and allocation==0: `weight = weight+1`.
  - This exact recurrence is required; weight affects future credit growth.

- **Burst consumption (necessary):**
  - `burst_rem` starts at input burst, is one-time extra count beyond rate, not replenished.
  - Effective per-batch count cap includes `rate+burst_rem` when rate>0.
  - After positive batch, if `batchCount > rate` and `rate>0`, `excess = batchCount - rate`, consume `min(excess, burst_rem)`, `burst_rem -= consumed`.
  - Burst allowance may be consumed across batches: if a batch uses burst to exceed rate, remaining burst for next batch is reduced.

- **Global rebalancing (necessary):**
  - For positive loads, use an outer loop up to 10 iterations while remaining>0:
    - Recompute remaining cost caps `gRemCost = groupCap - totalCost - sum(subBatch*cost)` and `sRemCost = subCap - totalCost - subBatch*cost`.
    - Convert to count: `sEffCount = floor(sRemCost/cost)` capped by `rate+burst_rem - subBatch` when rate>0.
    - `sumMemberEff` per group = sum `sEffCount`, `effG = min(gRemCount, sumMemberEff, rate+burst_rem - groupBatch)`, 0 if no members.
    - If sum eff==0 break.
    - Allocate remaining to groups via primitive, then per-group to members via same primitive.
    - If group allocation cannot be fully taken by members, leftover returns to remaining and is reallocated to other groups next iteration.
  - For example, with group caps 10 each and one group limited by member caps to 2 total, load 10 results in batch allocation `1,1,8` where group0 gets 2 total and group1 gets 8 total, demonstrating rebalancing.

- **Negative loads:** If load<0, N=-load dealloc by priority desc groups then members, respecting subscriber totals never <0, burst unaffected, counts as activity. Group cost may be exceeded due to count-approximation.

- **Hierarchical order:** Groups then per-group members.

- **Determinism:** Tie lower idx wins.

## Examples (matching oracle)

### Example 1 - basic burst0 cost1

Input:
```
1
16
2
10 0 5 10 0 0
5 0 3 10 0 0
4
0 10 0 5 6 0 0 1
0 5 0 3 9 0 0 1
1 5 0 4 3 0 0 1
1 1 0 1 12 0 0 1
```
Output:
```
6,4,3,3
```

### Example 2 - rate with burst

Input:
```
1
5
1
0 0 1 10 2 3
2
0 0 0 1 10 2 3 1
0 0 0 1 10 0 0 1
```
Output:
```
3,2
```

### Example 3 - multi-batch dynamic weight

Input:
```
2
6
6
2
0 0 4 11 0 0
0 0 1 6 0 0
3
0 5 0 4 11 0 0 1
0 1 0 1 6 0 0 1
1 10 0 2 5 0 0 1
```
Output:
```
4,1,1
4,1,1
```

### Example 4 - negative deallocation

Input:
```
2
6
-4
1
0 0 2 10 0 0
2
0 5 0 2 10 0 0 1
0 1 0 2 10 0 0 1
```
Output:
```
3,3
-3,-1
```

### Example 5 - cost factor

Input:
```
1
5
1
0 0 1 20 0 0
2
0 0 0 1 10 0 0 2
0 0 0 1 10 0 0 5
```
Output:
```
3,2
```

### Example 6 - legacy format (5-field groups, 6-field subs) with defaults

Input:
```
1
10
1
0 0 1 10 0
2
0 10 0 1 5 0
0 1 0 1 5 0
```
Output:
```
5,5
```

### Example 7 - large overflow

Input:
```
1
1000000000000
1
0 0 1000000000000 1000000000000 0 0
2
0 0 0 1000000000000 500000000000 0 0 1
0 0 0 1000000000000 500000000000 0 0 1
```
Output:
```
500000000000,500000000000
```
