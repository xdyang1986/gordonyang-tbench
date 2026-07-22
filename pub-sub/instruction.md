# Build the hierarchical broker allocator with min, priority, rate limits and credit

Implement a Go program at `/app/main.go` that fairly distributes messages across weighted, capacity-limited subscribers grouped into groups, with per-batch minimum guarantees, priority, per-batch rate limits, and persistent credit-based fairness across batches.

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

- **Effective group caps:** A group's remaining allocation in a batch cannot exceed what its members can still take in that batch. So effective remaining cap for a group is limited by sum of effective per-batch caps of its members plus its own rate limit. If a group has no members, its effective cap is 0. If a subscriber's gid is out of range, it gets 0 and does not contribute.
- **I/O and persistence:** `T` loads, `G` groups, `S` subs as described, T lines output CSV in input order, state (cumulative totals and credits) persists across batches, credits start at weight.
- **64-bit safety:** Loads, caps, weights, and intermediate products like remaining * credit can be up to 1e12*1e12=1e24 and 3*4e18=1.2e19, which overflow signed 64-bit. You must compute proportional shares without 64-bit overflow.

## Fairness properties (require engineering judgement, not prescribed as paste-ready code)

- **Min guarantees + Priority:** Each batch must first satisfy per-entity minimums. If load insufficient for all mins, higher priority entities are satisfied first, tie by original input order. Minimums that exceed feasible caps, rate limits, or remaining load are sensibly capped.

- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Per-member effective per-batch cap is limited by both remaining total cap and rate limit. Group effective cap also limited by its own rate.

- **Credit-decay weighted fair share:** After mins, remaining load is distributed fairly based on persistent credits. Credits start at weight and evolve across batches to avoid starvation: entities that received messages have their credit decayed (with some memory but plus a small constant to keep it ≥1), idle entities have credit increased by weight. The exact decay formula that matches all examples and tests is uniquely determined - you must deduce it from the examples and the requirement to avoid zero credit. No alternative decay is acceptable.

- **Proportional share and progress:** Weighted distribution is proportional to credit, using integer division and respecting remaining effective caps. Implementation must guarantee progress even when integer division yields zero, and must remain efficient for large remaining loads (not iterating per message). If total credit somehow becomes zero, a deterministic round-robin fallback in original input order must be used, efficiently via bulk cycles.

- **Determinism:** All tie-breaking deterministic by original index, lower wins, stable across batches.

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
Sub0 has per-batch rate 2, so its effective per-batch cap is 2.

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
Here remaining*credit = 1e12*1e12=1e24 > 2^63-1 overflows int64, needs 128-bit handling.

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
0 0 1 10 0
2
0 0 0 4000000000000000000 10 0
0 0 0 4000000000000000000 10 0
```
remaining*credit = 3*4e18=1.2e19 > 9e18.

Output:
```
2,1
```
