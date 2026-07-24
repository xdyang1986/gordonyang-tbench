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
- Groups: prio, min≥0, weight≥1, cap≥0 total cost, rate≥0 per-batch max count (0=unlimited), burst≥0 one-time extra count beyond rate.
- Subs: gid, prio, min, weight, cap total cost, rate max count per batch (0 unlimited), burst, cost≥1 per-message cost (each message consumes cost from cap).
- Blank lines and extra spaces robust. Must accept old formats: groups 5 fields burst0, subs 6 fields burst0 cost1, 7 fields burst given cost1.

## Output format

Exactly `T` lines, each line S comma-separated ints in input order for that batch (counts, may be negative for deallocation), no spaces. Cumulative cost never exceeds caps.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective caps with burst+cost:** `sRemCost = cap - totalCost`, `sRemCount = floor(sRemCost/cost)`, `sEffCount = min(sRemCount, rate+burst_rem if rate>0)`. Sum per group `sumMemberEff`. Group `gRemCost = cap - totalCost`, `minCostInGroup = min cost among members`. `gRemCount = floor(gRemCost/minCost)` if has members else 0. Effective group count `effG = min(gRemCount, sumMemberEff, rate+burst_rem if rate>0, 0 if no members)`. Invalid gid →0 and excluded from group totals AND credit/weight/burst updates (final values equal initial).

- **Backward-compat parsing:** Must accept older formats: 5-field groups behave as burst 0, 6-field subs as burst0 cost1, 7-field subs as cost1. Legacy formats must produce identical output to full-field equivalents (e.g., 5-field group `0 0 1 10 0` as `0 0 1 10 0 0`, 6-field sub `0 10 0 1 5 0` as `0 10 0 1 5 0 0 1`, 7-field sub `0 10 0 1 5 0 0` as `0 10 0 1 5 0 0 1`). Example 6 demonstrates.

- **I/O, persistence, 64-bit safety:** State persists across batches: total cost, credit start weight, weight start weight, burst_rem start burst. `rem*credit` up to 1e24 and 1.2e19 overflow, need 128-bit mulDiv via `math/bits`.

- **Min-phase order:** Priority desc tie original idx asc. Each min capped to `min(min, effCap, rem)`.

- **Weighted deterministic loop:** After mins, multi-round: temp credits = persistent credits at batch start, active = `alloc < effCap`, total = sum credits, if total==0 bulk RR fallback (minRem, cycles, 1-by-1 input order), else share `floor(rem*credit/total)` via mulDiv capped, used==0 fallback highest credit tie lowest idx, update temp credits `c/2+1` if delta>0 else `+weight`, repeat until rem==0 or no active. Persistent credit update after batch: if alloc!=0 `credit=credit/2+1` else `credit+=weight_old`.

- **Dynamic weight:** After each batch, if eligible and alloc!=0 `weight = max(1, floor(mulDiv(weight,9,10)))` else if eligible and alloc==0 `weight = weight+1`. Exact `*9/10` must be overflow-safe via mulDiv to handle large weights up to 4e18 (weight*9=3.6e19 > MaxInt64), not `weight*9/10` signed overflow.

- **Burst consumption:** After positive batch, if rate>0 and batchCount>rate, excess consumes burst_rem.

- **Global rebalancing:** For positive load, while remaining>0 up to 10 iter: recompute remaining cost caps and count caps with rate remaining `rate+burst_rem - batch`, sumMemberEff, effG, allocate groups via primitive, per-group members via primitive, return unused to remaining.

- **Negative loads:** If load<0, N=-load dealloc by priority desc groups then members, respecting totals never <0, burst unaffected, counts as activity.

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
