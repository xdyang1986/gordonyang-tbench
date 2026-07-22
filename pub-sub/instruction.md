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

Exactly T lines, each line S comma-separated ints in input order for that batch, no spaces. Cumulative allocations never exceed caps. For S==0 output empty lines per batch.

Build: `cd /app && go build -o /app/allocator .` Standard library only.

## Necessary specification (legitimate, not giveaway)

- **Effective group caps:** A group's remaining allocation in a batch cannot exceed what its members can still take in that batch. Per-member effective per-batch cap is min(remaining total cap, per-batch rate if rate>0). Sum those per group to get sum of members' effective caps. Effective remaining group cap is min(group remaining total cap, sum of members' effective per-batch caps, group per-batch rate if rate>0, and 0 if group has no members). This ensures group allocation is feasible for its members.

- **I/O and persistence:** T loads, G groups, S subs as described, T lines output CSV in input order, state (cumulative totals and group/subscriber credits) persists across batches, credits start at weight.

- **64-bit safety:** Loads, caps, weights, and products like remaining * credit can be 1e12*1e12=1e24 and 3*4e18=1.2e19, which overflow signed 64-bit. Proportional shares must be computed without 64-bit overflow.

## Fairness properties (requires engineering judgement, but examples uniquely determine correct behavior)

- **Min guarantees + Priority:** Each batch must first satisfy minimums. Minimums are allocated in priority order (higher priority first, tie by original input order). If load is insufficient, higher priority gets its min first, with min sensibly capped to feasible amount (min of its min, remaining cap, rate limit if non-zero, and remaining load). Zero caps and zero rates handled.

- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Effective per-batch caps already incorporate rate limits at both member and group levels as described above.

- **Credit-decay weighted fair share (exact recurrence is required for correctness and is explicitly stated to avoid ambiguity, but you must implement the surrounding fairness loop yourself):** After mins, remaining load is distributed fairly based on persistent credits that evolve across batches. For any active entity (effective remaining cap >0 at batch start) that received any messages in this batch (including min phase), its credit for next batch becomes floor(credit/2)+1, otherwise it becomes credit + weight. Credits never go negative and with this formula stay ≥1, so zero-credit total never occurs for correct implementation, but you must implement an efficient round-robin fallback in original input order if it ever does, using bulk cycles for large remaining loads, not iterating per message.

- **Weighted share:** After min phase, remaining load is split proportionally to current credits, using integer division and capped to remaining effective caps, with a guarantee of progress (if integer division yields no progress, give one to highest credit active, tie lowest index). You must decide how to structure the multi-round loop to achieve this fairly.

- **Hierarchical application order:** For each batch, you must first allocate the batch load to groups (using effective caps), then for each group allocate its batch share to its own subscribers (in input order within group). This group-then-member order is required and is explicitly stated here.

- **Determinism:** All tie-breaking deterministic by original index (lower wins), stable across batches.

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

### Example 2 - min, priority and rate limiting

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
Sub0 has rate 2 per batch, so effective cap 2. After min phase (2 to sub0, 1 to sub1) remaining 6 distributed by credit.

Output from oracle:
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
Here remaining*credit = 1e24 overflows int64, must use 128-bit.

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

There are additional hidden tests for implicit robustness: group with no members, invalid gid, zero caps, blank lines, priority ties, rate limiting, etc. Handle sensibly.
