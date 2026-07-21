# Build the hierarchical broker allocator - ultimate complex with implicit robustness

Implement a Go program at `/app/main.go` that simulates a **multi-batch hierarchical broker** with **minimum guarantees**, **priority** and **credit-decay weighted fair share**. This task is intentionally hard: core allocation is described, but many edge cases are only implied and will be checked by hidden corner-case tests. Exact formulas for credit decay are given to avoid ambiguity, but effective caps, min capping, tie-breaking, blank-line handling, invalid gids, and large-scale efficiency are left as sensible implicit requirements.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap   (G lines, groups 0..G-1, may have blank lines)
S
gid prio min weight cap       (S lines, subs 0..S-1, may have extra spaces)
```

- `T` batches (≥1), each load ≥0, up to 1e12
- Groups: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total across batches
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total
- Input may contain blank lines and extra spaces - you must parse robustly (trim, skip blanks, split whitespace). This is implicit.
- If gid out of range or group has no members, allocation should be 0 for those subs and not crash (implicit).
- If min > cap or min > remaining cap or min > remaining load, min must be capped sensibly to what is feasible (implicit).
- If group has no members, its effective cap is 0 (implicit).
- Large numbers up to 1e12 must be handled efficiently (O(n log n) or O(n * rounds), not O(load)), 64-bit safe (implicit performance requirement).

## Output format

`T` lines, each line `S` comma-separated ints in input order for that batch, no spaces. Cumulative allocations never exceed caps. For `S==0` output empty lines.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Persistent state

- `group_total[g]` and `sub_total[s]` cumulative, initially 0
- `group_credit[g] = group_weight[g]`, `sub_credit[s] = sub_weight[s]` initially, persistent across batches.

## Allocation description (core, but some edge details implicit)

**Effective group caps:** A group's remaining allocation cannot exceed what its members can still take. So effective remaining cap for group g is limited by sum of remaining caps of its members. Think about what that means for groups with no members.

**Per batch with load L:**

1. Compute remaining caps: `g_rem = g_cap - group_total`, `s_rem = s_cap - sub_total` (floor 0), `sum_member_rem[g] = sum s_rem for subs in g`, `eff_g_rem[g] = min(g_rem[g], sum_member_rem[g])`.

2. **Group level:** Allocate L to groups using min+priority+credit-decay:

   - Min phase: sort groups by priority descending, tie by index ascending. Allocate each group's min capped to `min(min, eff_g_rem, remaining load)` in that order until load exhausted. If load insufficient for all mins, higher priority gets its min first (implicit priority handling).
   - Weighted phase: remaining load after min phase allocated by credit-decay fair share using remaining effective caps, with persistent group credits.

3. **Per group:** For each group g with batch allocation `gl`, collect its subscribers (input order). Allocate `gl` to them using same min+priority+credit-decay with subscriber remaining caps, mins, priorities, weights, and persistent sub credits.

4. Update totals and credits: after each batch, for any entity that was active (remaining cap >0 at batch start), if it got >0 in this batch (including min), its credit decays as `credit = credit/2 + 1`, else it grows as `credit += weight`. Credits never negative.

5. Output per-batch per-sub allocations as CSV.

**Credit-decay primitive details (explicit to avoid ambiguity):**

- Proportional share: `(rem * credit) // total` integer division, capped to remaining cap
- Progress guarantee: if no progress (used==0), give 1 to active with highest credit, tie lowest index
- Fallback when total credit 0: must make progress - round-robin in input order, 1 by 1, but must be efficient for large remaining (bulk cycles: full cycles + partial, not O(rem) one-by-one) - this case never happens with correct `credit/2+1` decay (credit stays ≥1), but implement efficiently for robustness.
- Decay formula is exactly `credit/2 + 1` when served, `+weight` when idle - no alternative formula is acceptable.

**Determinism:** All tie-breaking must be deterministic, lowest index wins, and stable across batches.

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

### Example 2 - min and priority (min capped implicitly)

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
Here group1 effective cap is 1, load 9 → group alloc 8,1, within group0 min phase gives 2,1 (priority), weighted gives remaining.

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

### Example 4 - edge: min > cap

Input:
```
1
5
1
0 0 1 10
1
0 10 10 1 2
```
Sub min10 cap2 → min capped to2.

Output:
```
2
```

### Example 5 - blank lines and spaces (implicit robustness)

Input:
```
1

10

1
  0   0  1  10

2

0  0  0  1  5
  0 0 0 1 5

```
Output:
```
5,5
```

There are additional hidden corner cases testing implicit requirements: empty groups, invalid gid, zero caps, large 1e12, RR fallback, priority ties, T up to 100, etc. Handle sensibly.
