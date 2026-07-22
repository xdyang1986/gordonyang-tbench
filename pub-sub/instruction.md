# Build the broker allocator with min, priority and multi-batch credit-decay

Implement a Go program at `/app/main.go` that is a **multi-batch broker allocator** with **minimum guarantees**, **priority** and **credit-decay weighted fair share**.

## Input format

- First line: T, number of batches (≥1)
- Next T lines: load_1 .. load_T, each ≥0, up to 1e12
- Next line: S, number of subscribers (≥1)
- Next S lines: each subscriber has `priority min weight cap` - priority int (higher = higher), min ≥0 per-batch minimum, weight ≥1, cap ≥0 total across all batches. Input order is original order.
- Input may contain blank lines and extra spaces - you must parse robustly by trimming and skipping empty lines.

Robustness you must handle (explicit for fair grading): if min exceeds cap or remaining cap or remaining load, cap it to feasible value; large numbers up to 1e12 must be handled efficiently without iterating per message (use 64-bit); zero caps/loads/mins must be handled; priority tie-breaking deterministic by original index (lower wins).

## Output format

Exactly T lines, each line S comma-separated integers in input order for that batch, no spaces. Cumulative allocations never exceed caps. For S==0 output empty lines.

Build: `cd /app && go build -o /app/allocator .` Standard library only.

## Persistent state

- Total allocated per subscriber, initially 0, cumulative across batches.
- Credit per subscriber, initially = weight, persistent across batches.

## Allocation per batch

For each batch with load L, consider remaining caps (cap - total). Allocation has two phases.

**Min phase:** Sort subscribers by priority descending, tie by original index. Walk that order, giving each subscriber its per-batch minimum, but limited to both its remaining cap and remaining load. If load is insufficient to satisfy all mins, higher priority goes first. If min is larger than remaining cap, give only remaining cap.

**Weighted phase:** After min phase, allocate remaining load using a multi-round fair-share loop with persistent credits:

- Each round, active subscribers are those still with remaining capacity in this phase.
- Compute total credit of active subscribers. If total is zero, you must still make progress with an efficient round-robin fallback in original input order, allocating one by one conceptually but implemented efficiently via bulk cycles for large remaining loads, not one-by-one O(load).
- Otherwise each active subscriber receives a proportional share based on its credit relative to total, using integer division floor(remaining * credit / total), limited to its remaining capacity.
- If no progress is made due to integer division, give one message to the active subscriber with highest current temporary credit, tie by lowest original index.
- After each round, update temporary credits: subscribers that received messages in this round have their temporary credit decayed to floor(credit/2)+1, those that received nothing have it increased by their weight.
- Repeat until remaining load zero or no active subscribers.

After weighted phase, merge min and weighted allocations into batch allocation. Then update persistent credits for next batch: any subscriber that was active at batch start (remaining cap >0 before batch) and received >0 in this batch (including min) has its persistent credit decayed to floor(credit/2)+1, otherwise boosted by weight. This exact decay formula is required; with it credits never reach zero, so zero-credit fallback is only for robustness but must be efficient.

Update cumulative totals and output batch as CSV.

Total allocated per batch equals min(batch load, sum remaining caps). Deterministic.

## Examples

### Example 1 - weighted without min

Input:
```
1
10
2
0 0 3 100
0 0 1 100
```
Output:
```
8,2
```

### Example 2 - min and priority

Input:
```
1
9
3
10 2 5 10
5 1 6 10
1 0 6 1
```
Load 9, subs have mins 2 and1, priorities. Reference output `4,4,1`.

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
3
5 0 4 11
1 0 1 6
10 0 2 5
```
First batch with credits 4,1,2 allocates 4,0,2. After batch credits become 3,1,2 etc. Second batch load6 allocates 3,1,2.

Output:
```
4,0,2
3,1,2
```

### Example 4 - min > cap (min capped)

Input:
```
1
5
1
10 10 1 2
```
Min10 cap2 → capped to2.

Output:
```
2
```

### Example 5 - blank lines and spaces robust

Input:
```
1

10

2

  0   0  3  100
  0 0 1 100

```
Output:
```
8,2
```
