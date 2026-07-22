# Build the hierarchical broker allocator with min, priority and credit-decay

Implement a Go program at `/app/main.go` that is a **hierarchical broker allocator** with **minimum guarantees**, **priority** and **credit-decay weighted fair share**. This is harder than single-level but easier than multi-batch persistent, targeting hard-but-passable.

## Input format

```
load
G
g_prio g_min g_weight g_cap   (G lines, groups 0..G-1)
S
gid prio min weight cap       (S lines, subs 0..S-1)
```

- `load` total messages (≥0, up to 1e12)
- `G` groups (≥1)
- Group: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total
- `S` subscribers (≥1)
- Subscriber: gid ideally in [0,G-1], priority int, min ≥0, weight ≥1, cap ≥0
- Input may contain blank lines and extra spaces - parse robustly by trimming and skipping blanks.

Robustness you must handle (explicit for fair grading):
- Min may exceed cap or remaining cap or load - must be capped to feasible `min(min, cap, rem)`.
- If load insufficient for all mins, higher priority gets min first, tie lower index.
- Effective group cap: a group's allocation cannot exceed sum of caps of its members. So effective cap = min(group cap, sum member caps). If group has no members, effective 0.
- If gid out of range, subscriber gets 0 and does not crash.
- Large numbers up to 1e12 must be efficient O(n log n), not O(load), 64-bit safe.
- Zero caps/loads/mins handled.
- Deterministic tie-breaking: lower original index wins.

## Output format

Single line, S comma-separated ints in input order, allocation per subscriber, no spaces. Each respects cap, per-group sum respects effective group cap, total = min(load, sum effective caps).

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Allocation steps

1. Compute `sum_member_caps[g] = sum cap of subs in group g` (only valid gid). Effective cap `eff_cap[g] = min(group_cap[g], sum_member_caps[g])`.

2. Group level: allocate `load` to groups using allocate primitive with group priorities/mins/weights/caps=eff_cap. Credit starts = weight.

3. Per group: collect its subscribers in input order, let `gl = group_batch[g]`. Allocate `gl` to its members using same allocate primitive with subscriber prio/min/weight/cap. Credit starts = weight.

4. Scatter to global output in input order.

### Primitive allocate (min + priority + credit-decay)

Given load, arrays prio, mins, weights, caps, credits (credit starts = weight), returns batch allocation. Credits inside primitive evolve per round but final credit not needed for single-batch version (single batch only).

Min phase: sort indices by priority descending, tie by original index ascending. Walk that order, giving each its min capped to min(min, cap, remaining load).

Weighted phase (credit-decay multi-round):

- Remaining caps after min, remaining load rem.
- While rem >0:
  - Active subscribers still with remaining capacity.
  - Total credit of active. If total==0: efficient round-robin fallback in input order, but efficient for large remaining via bulk cycles (full cycles + partial), deterministic, not O(load) one-by-one.
  - Else proportional share floor(rem * credit / total) capped.
  - If no progress (used==0), give 1 to active with highest credit, tie lowest index.
  - Update temporary credits: served this round decays to floor(credit/2)+1, unserved grows by weight.

This exact decay formula `credit/2+1` is required for correctness and is explicitly given.

## Examples

### Example 1 - hierarchical basic

Input:
```
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

### Example 3 - min > cap

Input:
```
5
1
0 0 1 10
1
0 10 10 1 2
```
Output:
```
2
```
