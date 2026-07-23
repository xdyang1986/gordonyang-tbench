# Build the hierarchical broker allocator with min, priority, rate limits and persistent credit

Implement a Go program at `/app/main.go` that fairly distributes messages across subscribers grouped into groups, with per-batch minimum guarantees, priority, per-batch rate limits, and persistent credit-based fairness across batches.

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

- `T` batches (≥1), each load ≥0, up to 1e12
- Groups: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited)
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited)
- Input may contain blank lines and extra spaces - parse robustly.

## Output format

Exactly `T` lines, each line `S` comma-separated ints in input order for that batch, no spaces. Cumulative allocations never exceed caps.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective group caps (necessary for feasibility):** A group's remaining allocation in a batch cannot exceed what its members can still take in that batch. Per-member effective per-batch cap is `min(remaining total cap, per-batch rate if rate>0)`. Sum those per group. Effective remaining group cap is `min(group remaining total cap, sum of members' effective per-batch caps, group per-batch rate if rate>0, 0 if group has no members)`. If gid out of range, subscriber gets 0 and does not contribute. This is legitimate necessary spec.

- **I/O, persistence and 64-bit safety (necessary):** T loads, G groups, S subs, T lines output CSV in input order, state (cumulative totals and credits) persists across batches, credits start at weight. Loads, caps, weights and products like `remaining * credit` can be `1e12*1e12=1e24` and `3*4e18=1.2e19`, which overflow signed 64-bit. Proportional shares must be computed without 64-bit overflow using 128-bit techniques (e.g., `math/bits.Mul64`/`Div64` or equivalent mulDiv). This is necessary for overflow cases.

- **Min-phase deterministic order (necessary):** Minimums are allocated in priority order: higher priority first, tie by original input order (lower index wins). Each min allocation is capped to `min(min, effective cap remaining for that entity, load remaining)`. If load insufficient for all mins, higher priority gets its min first. If min>cap, capped. Zero caps and rates produce zero allocation. This ordering is necessary for determinism.

- **Weighted fair-share deterministic loop (necessary for byte-exact output):** After mins, remaining load is distributed in a multi-round loop. This exact loop is necessary to remove leftover/rounding ambiguity and make the expected CSV byte-exact.

  - Maintain per-entity temporary credits, initialized to the persistent credits at start of batch.
  - Active set = entities where `allocated_in_weighted_phase < effective_remaining_cap_after_mins` (i.e., still can take).
  - Let `rem` be remaining load for this level (groups, or per-group member groups), `total = sum(temp_credit[active])`.
  - If `total == 0`: (should never occur with correct decay because credits stay ≥1, but required for robustness and termination) perform bulk round-robin: find `minRem = min_{active} (cap - alloc)`, `cycles = min(minRem, rem // len(active))`. If cycles>0 allocate it to all active. Then allocate 1 by 1 in input order while rem>0 and active remains. This ensures progress.
  - Else, for each active, compute proportional share `share = floor(rem * temp_credit / total)` using overflow-safe 128-bit mulDiv, capped to `cap - alloc`. Sum shares to `used`.
  - If `used == 0`: progress guarantee – select entity with highest temp_credit, tie by lowest original index, give it 1 (delta=1, used=1). This avoids starvation when flooring yields 0.
  - Subtract `used` from `rem`.
  - Update temporary credits for this round: if entity received >0 in this round (`delta>0`) then `temp_credit = floor(temp_credit/2)+1`, else `temp_credit += weight` (weight is per-entity weight at that level).
  - Repeat until `rem==0` or no active.
  - Persistent credit update at end of batch (including mins): if entity's total allocation in batch (mins+weighted) >0 and its effective cap at batch start was >0, then `persistent = floor(persistent/2)+1`, else `persistent += weight`. This exact recurrence `floor(c/2)+1` if received else `+weight` is necessary for multi-batch determinism and keeps credits ≥1.

  This loop pins leftover/rounding distribution and ensures deterministic tie-breaking by original index. Implementing this specific round structure is required; high-level idea of "proportional fair" alone would leave ambiguity.

- **Hierarchical order (necessary):** For each batch, first allocate to groups using effective caps and the above primitive, then per group allocate its granted load to its subscribers using the same primitive in input order. Group-then-member order is required.

- **Determinism and efficiency (necessary):** All tie-breaking deterministic by original input order (lower wins), stable across batches. Must handle 1e12 scale and overflow efficiently.

## Fairness properties (requires engineering judgement)

- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Effective caps incorporate rate limits as described above.

- **Credit-decay intuition:** Credits reward entities that did not receive messages by growing with weight, and decay those that did receive via `floor(c/2)+1`. Combined with proportional sharing this yields weighted fair share with persistent memory across batches, but exact decay formula and loop are pinned above for determinism.

- **Conservation:** Cumulative allocations never exceed total caps, per-batch allocations never exceed effective caps (which already include rate limits and member feasibility).

## Examples (all matching oracle, no hedging)

### Example 1 - hierarchical basic, rate unlimited

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
```

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
```

### Example 3 - multi-batch persistent credit

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
```

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
```
