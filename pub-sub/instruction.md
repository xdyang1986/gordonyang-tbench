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
- Groups: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total across batches, rate ≥0 per-batch max (0 = unlimited)
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited)
- Input may contain blank lines and extra spaces - parse robustly.

## Output format

Exactly `T` lines, each line `S` comma-separated ints in input order for that batch, no spaces. Cumulative allocations never exceed caps.

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Necessary specification

- **Effective group caps:** A group's remaining allocation in a batch cannot exceed what its members can still take in that batch. Per-member effective per-batch cap is min(remaining total cap, per-batch rate if rate>0 else remaining cap). Sum those per group to get sum of members' effective caps. Effective remaining group cap is min(group remaining total cap, sum of members' effective per-batch caps, group per-batch rate if rate>0, and 0 if group has no members). This ensures group allocation is feasible. If gid out of range, subscriber gets 0 and does not contribute.

- **I/O, persistence and 64-bit safety:** T loads, G groups, S subs as described, T lines output CSV in input order, state (cumulative totals and group/subscriber credits) persists across batches, credits start at weight. Loads, caps, weights and products like remaining * credit can be 1e12*1e12=1e24 and 3*4e18=1.2e19, which overflow signed 64-bit. Proportional shares must be computed without 64-bit overflow using 128-bit techniques.

## Fairness properties (requires engineering judgement, examples uniquely determine correct behavior)

- **Min guarantees + Priority:** Each batch must first satisfy minimums. Minimums are allocated in priority order (higher priority first, tie by original input order). If load insufficient for all mins, higher priority is satisfied first. Minimums that exceed feasible caps, rate limits, or remaining load are capped to feasible amount.

- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Effective per-batch caps incorporate rate limits as described above.

- **Credit-decay weighted fair share:** After mins, remaining load is distributed fairly based on persistent credits. Credits start at weight and evolve across batches to avoid starvation. Exact recurrence required for determinism: when an entity receives any messages in a batch (including min phase), its credit for next batch becomes floor(credit/2)+1, otherwise it grows by weight. With this formula credits stay ≥1. You must implement the surrounding multi-round fairness loop yourself to achieve proportional allocation with progress guarantee and efficient handling for large loads.

- **Weighted share and progress:** After min phase, remaining load is split in rounds proportionally to current credits, capped to remaining effective caps, computed without overflow. If a round yields no progress due to integer division, one message goes to active with highest credit, tie lowest original index. If total credit somehow becomes zero, an efficient round-robin fallback in original input order must be used, with bulk handling for large loads, not per-message iteration. With correct decay this never happens for correct implementations, but implement efficiently for robustness.

- **Hierarchical order:** For each batch, first allocate batch load to groups using effective caps, then for each group allocate its batch share to its own subscribers in input order within group. This group-then-member order is required.

- **Determinism:** All tie-breaking deterministic by original index (lower wins), stable across batches. Large numbers must be handled efficiently.

## Examples (all matching oracle)

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
Sub0 has rate 2 per batch, effective cap 2. Reference gives 2,6,1.

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
Here remaining*credit = 1e24 overflows int64, needs 128-bit.

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
