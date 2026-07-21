# Build the hierarchical broker allocator - complex with implicit edge handling

Implement a Go program at `/app/main.go` that simulates a **multi-batch hierarchical broker**. This task intentionally leaves some edge handling implicit - you must handle them sensibly and tests will check corner cases not fully described.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap   (G lines, groups 0..G-1)
S
gid prio min weight cap       (S lines, subs 0..S-1, gid in [0,G-1] ideally)
```

- `T` batches (≥1), each load ≥0
- Group: priority (int, higher = higher), min (≥0) per-batch minimum, weight (≥1), cap (≥0) total across all batches
- Subscriber: group id, priority, min, weight, cap (total)
- Input may contain blank lines and extra spaces - handle robustly.

## Output format

`T` lines, each line `S` comma-separated ints in input order, allocation per sub for that batch. No spaces. Each batch allocation respects remaining caps. Cumulative allocations never exceed caps.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Core allocation logic (two levels, stateful)

Maintain persistent state across batches:

- `group_total`, `sub_total` cumulative, initially 0
- `group_credit = group_weight`, `sub_credit = sub_weight` initially, persistent and updated each batch.

Per batch with load L:

**Effective group caps (implicit):** A group's remaining allocation cannot exceed what its members can still take. So effective remaining cap for group g should be limited by sum of remaining caps of its members. If a group has no members, its effective cap is 0. Think about what sensible effective cap means.

**Group level:** Allocate L to groups (using effective caps) with min + priority + credit-decay:

- Min phase: allocate per-group mins respecting remaining caps and remaining load. If not enough load to satisfy all mins, higher priority groups get their min first. What if min > cap? Sensible capping.
- Weighted phase: remaining load allocated by credit-decay fair share (see below) using remaining caps after min phase. Credits persist across batches and are updated after each batch based on whether group got anything.

**Per-group subscriber level:** For each group g, let `gl` be its batch allocation. Allocate `gl` to its subscribers (in input order within group) using same min+priority+credit-decay logic with subscriber remaining caps, mins, priorities, weights, and persistent subscriber credits.

Update totals and proceed to next batch. Output per-batch per-sub allocations.

### Credit-decay primitive (used after min phase)

For weighted phase with `rem`, `weights`, `rem_caps`, `credits`:

- `alloc=0`, `credit_tmp=credits copy`, `rem_w=rem`
- While rem_w >0:
  - active = indices where alloc < rem_cap
  - if empty break
  - total = sum credit_tmp[active]
  - If total==0: you must still make progress - implement a sensible fallback (e.g., round-robin in input order) and break after.
  - Otherwise proportional: `share = (rem_w * credit_tmp[i]) // total` capped to `rem_cap[i]-alloc[i]`
  - If no progress (used==0): give 1 to highest credit active, tie lowest index, to guarantee progress.
  - `rem_w -= used`
  - For active: if served this round, decay credit, else boost credit (decay is halving plus small constant to avoid zero, boost is +weight - deduce exact formula from examples if needed)

Credit update for next batch (implicit): If an entity got any allocation in this batch (including min), its credit decays, else it grows. What is the exact decay formula? Look at examples and think about avoiding zero credit. The intended decay must keep some memory but not drop to zero too fast.

### Implicit robustness requirements (not fully spelled out, but tested)

- Input may have blank lines, leading/trailing spaces - parse robustly.
- `min` may exceed `cap` or remaining cap - min should be capped sensibly.
- `gid` may be out of range or group may have no members - allocation should be 0 for those subs and not crash.
- Large numbers up to 1e12 may appear - use 64-bit, O(n log n) or O(n * rounds) is ok but avoid O(load).
- Zero caps, zero loads, zero mins must be handled.
- Priority tie-breaking must be deterministic (lowest index wins) and stable across batches.
- Total credit can drain to 0 after many decays - your fallback must be deterministic and in input order.
- Effective caps must be respected at both levels - group allocation cannot exceed sum of what its members can still take.
- Credits should never go negative.

## Examples

### Example 1 - basic hierarchical

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
Here subs in group0 have mins 2 and1 with priorities 10 vs5. Load9 with effective caps 10 and1.

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
T=2. First batch credit starts at weights, second batch credits are decayed/boosted from first.

Output:
```
4,1,1
4,1,1
```

## Hints for complexity

This task combines hierarchical + min + priority + multi-batch + credit-decay. The reference solution is ~150 LOC but edge handling adds complexity. Pay attention to effective caps, min capping, priority ordering for min phase, credit update, and RR fallback - these are where corner case tests will fail if not handled sensibly.
