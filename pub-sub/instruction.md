# Build the ultimate hierarchical broker allocator with burst, cost, dynamic weights, priority aging, negative rebalancing

Implement a Go program at `/app/main.go` that fairly distributes messages across subscribers grouped into groups, handling per-batch minimum guarantees, priority with aging, per-batch rate limits with one-time burst, per-message cost factor, dynamic weight evolution, global rebalancing, negative loads (deallocation), and persistent credit-based fairness.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap g_rate g_burst   (G lines, groups 0..G-1)
S
gid prio min weight cap rate burst cost     (S lines, subs 0..S-1)
```

- `T` batches ≥1, each load may be positive, zero, or negative (negative = deallocation, returns capacity) up to ±1e12.
- Groups: priority (higher = higher), min ≥0 per-batch min, weight ≥1, cap ≥0 total cost, rate ≥0 per-batch max count (0 = unlimited), burst ≥0 one-time extra count to exceed rate, not replenished.
- Subscribers: gid ideally in [0,G-1], prio, min, weight, cap (total cost), rate (max count per batch, 0 unlimited), burst, cost ≥1 per-message cost factor (each allocated message consumes cost from cap).
- Input may contain blank lines and extra spaces - parse robustly. Must also accept older formats: groups with 5 fields (burst 0) and subs with 6 fields (burst 0 cost 1) or 7 fields (burst given cost 1) for backward compatibility, but you should implement full 6/8.

## Output format

Exactly `T+8` lines, no spaces:

- First `T` lines: per-sub batch allocation counts (may be negative for deallocation) as S CSV in input order.
- `T+1`: per-group cumulative cost totals after all batches as G CSV.
- `T+2`: per-sub cumulative cost totals as S CSV (each total = sum count*cost).
- `T+3`: per-group final persistent credits G CSV.
- `T+4`: per-sub final persistent credits S CSV.
- `T+5`: per-group final burst remaining G CSV.
- `T+6`: per-sub final burst remaining S CSV.
- `T+7`: per-group final weights G CSV.
- `T+8`: per-sub final weights S CSV.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective caps with burst and cost (necessary):** Per-member remaining cost `sRemCost = cap - totalCost`, remaining count `sRemCount = floor(sRemCost / cost)`. Effective count including rate+burst: `sEffCount = min(sRemCount, rate+burst_rem if rate>0 else sRemCount)`. Sum per group `sumMemberEff`. Group remaining cost `gRemCost = cap - totalCost`, min cost in group `minCost = min cost among members`. `gRemCount = floor(gRemCost / minCost)` if has members else 0. Effective group count `effG = min(gRemCount, sumMemberEff, rate+burst_rem if rate>0 else gRemCount, 0 if no members)`. If gid out of range, sub gets 0 and not counted from group totals. An out-of-range gid sub is excluded from all allocation, group totals, AND credit/weight/streak/burst updates — its final credit, weight, and burst_rem equal their initial values (weight, weight, burst) for all batches. This persistence rule for out-of-range gid is necessary for determinism and matches `test_invalid_gid` expecting 1,1 for credit/weight.

- **Backward-compat parsing (necessary):** Must accept older formats: 5-field groups `prio min weight cap rate` behave as `burst=0`, 6-field subs `gid prio min weight cap rate` behave as `burst=0 cost=1`, 7-field subs `gid prio min weight cap rate burst` behave as `cost=1`. Legacy formats must produce identical full T+8 output to their full-field equivalents (e.g., a 5-field group line `0 0 1 10 0` behaves exactly as `0 0 1 10 0 0`, a 6-field sub `0 10 0 1 5 0` as `0 10 0 1 5 0 0 1`, a 7-field sub `0 10 0 1 5 0 0` as `0 10 0 1 5 0 0 1`). All examples include one legacy case (Example 9). This rule is necessary for robustness and is tested via raw short-format inputs.

- **I/O, persistence, 64-bit safety (necessary):** State persists across batches: totals (cost), credits start weight, weights start weight, burst_rem start burst, aging streaks 0. Products `rem*credit` can be 1e12*1e12=1e24 and 3*4e18=1.2e19 overflow signed 64-bit, need 128-bit mulDiv via `math/bits`.

- **Priority aging (necessary):** Effective priority = base prio + `streak//2` where streak = consecutive batches where entity was eligible (eff>0 at batch start or total>0 for deallocation) but got 0 allocation. If allocation !=0 streak resets to 0 else increments. For eligible but not served for 2 batches, priority effectively +1, etc. This is necessary for determinism.

- **Min-phase order (necessary):** Sort by effective priority desc tie original idx asc. Each min capped to `min(min, effCap, rem)`.

- **Weighted loop (necessary):** After mins, multi-round:
  - Temp credits = persistent credits at batch start.
  - Active = `alloc < effCap`.
  - `total = sum temp credits`.
  - If total==0: bulk RR fallback: `minRem = min cap-alloc`, `cycles = min(minRem, rem//len(active))` then 1-by-1 input order.
  - Else share = `floor(rem*credit/total)` via mulDiv capped, `used=sum`. If `used==0`: give 1 to highest temp credit tie lowest idx.
  - Update temp credits: if delta>0 `temp=temp/2+1` else `temp+=weight` (weight = persistent weight at batch start).
  - Repeat until rem==0 or no active.

- **Dynamic weight (necessary):** After each batch, if eligible and allocation!=0: `weight = max(1, floor(weight*0.9))` (weight*9/10), else if eligible and allocation==0: `weight = weight+1`. Exact.

- **Credit update (necessary):** If eligible and allocation!=0: `credit=credit/2+1` else if eligible and allocation==0: `credit=credit+weight_old`.

- **Burst consumption (necessary):** After positive batch, if rate>0 and batchCount > rate: `excess = batchCount - rate`, consume `min(excess, burst_rem)`, `burst_rem -= consumed`. Burst not replenished.

- **Global rebalancing (necessary):** For positive load L: `remaining=L, groupBatch=0, subBatch=0, first=true`. Loop up to 10 iter while `remaining>0`:
  - Recompute remaining cost and count caps: `gRemCostIter = cap - totalCost - sum(subBatch*cost for group)`, `sRemCostIter = cap - totalCost - subBatch*cost`, `sEffCountIter = min(remCost/cost, rate+burst_rem - subBatch if rate>0)`, `sumMemberEffIter`, `effGCountIter = min(gRemCost/minCost, sumMemberEffIter, rate+burst_rem - groupBatch)`.
  - If sum eff==0 break.
  - `groupItems` caps effG, mins = groupMin if first else 0, credits, weights, priority effective.
  - `groupIter = primitive(remaining, groupItems)`
  - For each group with gl>0: members idxs, `mItems` caps sEffIter, mins = subMin if first else 0, allocate `subIter = primitive(gl, mItems)`, sumSub, `groupBatch+=sumSub`, `subBatch+=subIter`, `remaining-=sumSub`.
  - first=false, if no progress break.

- **Negative loads (necessary):** If load<0, N=-load to deallocate. Order groups by effective priority desc tie idx, within each group members by priority desc. Deallocate by count, respecting `subTotalCost/cost + subBatch` and `groupTotalCost` remaining, never below 0. Burst unaffected. Credit/weight counts deallocation as activity (alloc!=0).

- **Hierarchical order (necessary):** Groups then per-group members, input order deterministic.

## Examples (matching oracle with new fields, no hedging)

### Example 1 - basic with burst 0 cost 1 (T+8)

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
10,6
6,4,3,3
3,2
3,2,3,1
0,0
0,0,0,0
4,2
4,2,3,1
```

### Example 2 - min, priority, rate with burst

Input:
```
1
9
2
0 0 5 10 0 0
0 0 6 10 0 0
3
0 10 2 5 10 2 0 1
0 5 1 6 10 10 0 1
1 1 0 6 1 0 0 1
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
4,5
4,5,5
```

### Example 3 - multi-batch dynamic weight + aging

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
10,2
8,2,2
2,1
2,1,2
0,0
0,0,0
2,1
2,1,1
```

### Example 4 - burst exceeds rate

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
5
3,2
1
1,1
0
2,0
1
1,1
```
Group rate2 burst3 allows up to 5, sub0 rate2 burst3 allows 3 (excess1 consumes burst), final burst rem group0, sub 2,0.

### Example 5 - negative deallocation

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
2
0,2
2
2,2
0
0,0
1
1,1
```

### Example 6 - cost factor

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
2,1
9
4,5
1
1,1
0
0,0
1
1,1
```
Explanation: caps are cost caps 10 each, cost 2 and 5. s0 rem cost 10 => remCount floor(10/2)=5, s1 floor(10/5)=2, effective sum 7, load5, fair share 2,1 counts, cost totals 4 and 5.

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
1000000000000
500000000000,500000000000
500000000001
500000000001,500000000001
0
0,0
900000000000
900000000000,900000000000
```

### Example 8 - blank lines robust

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
10,6
6,4,3,3
3,2
3,2,3,1
0,0
0,0,0,0
4,2
4,2,3,1
```

### Example 9 - legacy format (5-field groups, 6-field subs) with defaults

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
10
5,5
1
1,1
0
0,0
1
1,1
```
This tests backward-compat: 5-field groups are parsed as burst 0, 6-field subs as burst 0 cost 1, so same as explicit `0 0 1 10 0 0` and `0 10 0 1 5 0 0 1`. All T+8 lines still emitted.
