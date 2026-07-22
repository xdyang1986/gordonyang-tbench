# Build the hierarchical broker allocator with min, priority and multi-batch credit

Implement a Go program at `/app/main.go` that is a **multi-batch hierarchical broker allocator** with **minimum guarantees**, **priority** and **credit-decay weighted fair share**. This is the full combination of all hardening options, targeting hard-but-passable with reduced test count, not 0/5.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap   (G lines, groups 0..G-1)
S
gid prio min weight cap       (S lines, subs 0..S-1)
```

- `T` batches (≥1), each load ≥0, up to 1e12
- Groups: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total across batches
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total
- Input may contain blank lines and extra spaces - parse robustly (trim, skip blanks).

Robustness (explicit for fair grading):
- Min may exceed cap or rem cap or rem load - capped to feasible min(min, cap, rem)
- If load insufficient for all mins, higher priority gets min first, tie lower original index
- Effective group cap: group's remaining allocation cannot exceed sum of remaining caps of its members. Effective = min(group remaining cap, sum member remaining caps). If group has no members, effective 0.
- If gid out of range, subscriber gets 0 and does not crash
- Large numbers up to 1e12, also large weights/credits where rem*credit would overflow 64-bit, must be handled with 128-bit or safe decomposition (explicit 64-bit safety requirement)
- Zero caps/loads/mins handled, deterministic tie-breaking lower index wins

## Output format

`T` lines, each line `S` comma-separated ints in input order for that batch, no spaces. Cumulative allocations never exceed caps.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Persistent state

- `group_total` and `sub_total` cumulative, initially 0
- `group_credit = group_weight`, `sub_credit = sub_weight` initially, persistent across batches

## Allocation per batch

For each batch with load L:

1. Compute remaining caps: `g_rem = g_cap - group_total`, `s_rem = s_cap - sub_total`, sum member remaining caps per group, effective remaining group cap = min(g_rem, sum_member_rem).

2. Group level: allocate L to groups using min+priority+credit-decay primitive with group priorities/mins/weights/caps=effective remaining caps and persistent group credits.

3. Per group: for each group g with batch allocation gl, collect its subscribers in input order, allocate gl to them using same primitive with subscriber remaining caps, mins, priorities, weights, and persistent sub credits.

4. Update totals and persistent credits: for any entity active at batch start (remaining cap >0), if it received any messages in this batch (including min), its persistent credit decays to floor(credit/2)+1, otherwise grows by weight. With correct decay, credit never reaches 0, but implement fallback efficiently.

5. Output per-batch per-sub allocations as CSV.

### Primitive allocate (min + priority + credit-decay) - explicit but not paste-ready

Min phase: sort by priority descending, tie by original index, walk that order giving each its min capped to remaining cap and remaining load.

Weighted phase: multi-round loop with temporary credits copy. Each round, active set has remaining capacity. Total credit of active set. If total zero, efficient round-robin fallback in input order must be used, implemented with bulk cycles for large remaining loads, not O(load) one-by-one. Otherwise proportional share is floor(remaining * credit / total) capped to remaining capacity, but must be computed without 64-bit overflow (remaining up to 1e12, credit up to large where remaining*credit would overflow signed 64-bit, so use 128-bit multiply or safe decomposition). If no progress due to integer division, give one to active with highest temporary credit tie lowest index. After each round, temporary credits: served decays to floor(credit/2)+1, unserved grows by weight.

This exact decay formula `credit/2+1` is required and uniquely determines output.

## Examples

### Example 1 - hierarchical basic

Input:
```
1
16
2
10 0 5 10
5 0 3 10
4
0 10 0 5 6
0 5 0 3 9
1 5 0 4 3
1 1 0 1 12
```
Output:
```
6,4,3,3
```

### Example 2 - min and priority

Input:
```
1
9
2
0 0 5 10
0 0 6 10
3
0 10 2 5 10
0 5 1 6 10
1 1 0 6 1
```
Output:
```
4,4,1
```

### Example 3 - multi-batch persistent credit

Input:
```
2
6
6
2
0 0 4 11
0 0 1 6
3
0 5 0 4 11
0 1 0 1 6
1 10 0 2 5
```
Output:
```
4,1,1
4,1,1
```

### Example 4 - large weight overflow (64-bit safety)

Input:
```
1
1000000000000
1
0 0 1000000000000 1000000000000
2
0 0 0 1000000000000 500000000000
0 0 0 1000000000000 500000000000
```
Here remaining*credit = 1e12 * 1e12 = 1e24 > 9e18 overflows int64, must be handled with 128-bit.

Output:
```
500000000000,500000000000
```

### Example 5 - large credit overflow

Input:
```
1
3
1
0 0 1 10
2
0 0 0 4000000000000000000 10
0 0 0 4000000000000000000 10
```
remaining*credit = 3 * 4e18 = 1.2e19 > 2^63-1.

Output:
```
2,1
```
