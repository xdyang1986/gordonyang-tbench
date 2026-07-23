# Build the ultimate hierarchical broker allocator with min, priority, rate, burst, dynamic weights, negative loads and rebalancing

Implement a Go program at `/app/main.go` that is a **multi-batch hierarchical broker allocator** with **minimum guarantees, priority, per-batch rate limits, burst, dynamic weight evolution, negative loads (deallocation), global rebalancing of unused capacity, and credit-decay weighted fair share**. This combines all previous hardening options to be maximally hard while remaining fully explicit for fair grading.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap g_rate g_burst   (G lines, groups 0..G-1 in input order)
S
gid prio min weight cap rate burst           (S lines, subs 0..S-1 in input order)
```

- `T` batches (≥1), each load may be positive (allocation) or negative (deallocation, returns capacity). Up to ±1e12.
- Groups: priority int (higher = higher), min ≥0 per-batch minimum, weight ≥1, cap ≥0 total across batches, rate ≥0 per-batch max (0 = unlimited), burst ≥0 one-time extra allowance that can be used to exceed rate.
- Subscribers: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total, rate ≥0 per-batch max (0 = unlimited), burst ≥0 one-time extra.
- Input may contain blank lines and extra spaces - parse robustly.

## Output format

`T+4` lines:

- First T lines: per-subscriber batch allocation for that batch (positive for alloc, negative for dealloc) as S comma-separated ints in input order, no spaces.
- Line T+1: per-group total cumulative allocation after all batches as G comma CSV.
- Line T+2: per-subscriber total cumulative allocation as S CSV.
- Line T+3: per-group final credits as G CSV.
- Line T+4: per-subscriber final credits as S CSV.

All totals respect caps (never exceed cap, never go below 0 after deallocation). Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## Persistent state

- `group_total[g]`, `sub_total[s]` cumulative, initially 0, never <0 nor >cap
- `group_credit[g] = group_weight[g]` initially, `sub_credit[s] = sub_weight[s]` initially, persistent across batches
- `group_weight[g]` and `sub_weight[s]` are dynamic: after each batch, if entity received any allocation (including min, positive), its weight becomes max(1, floor(weight*0.9)), else weight becomes weight+1. This is exact and required.
- `group_burst_rem[g] = group_burst[g]` initially, `sub_burst_rem[s] = sub_burst[s]` initially, one-time extra that is consumed when rate limit is exceeded, not replenished. When an entity's per-batch allocation would exceed its rate (if rate>0), it may use burst_rem to go up to rate+burst_rem, consuming burst_rem.

## Effective caps per batch (remaining + rate + burst + sum member)

For each batch with load L (may be negative):

If L <0: **Deallocation**: return capacity. Deallocation must respect that totals never go below 0. Deallocate from groups and subs by priority (higher priority first) up to min(-L, total) and also respecting that deallocation cannot make total negative. After deallocation, credits for deallocated entities are boosted by weight (sub_credit += weight, group_credit += weight), weights also evolve per dynamic rule (if deallocated, weight is considered served? For simplicity, deallocation counts as activity: if an entity had any deallocation in batch, its weight decays as max(1, floor(weight*0.9)), else grows). Burst not affected by deallocation.

If L ≥0: **Allocation with rebalancing loop**:

We have remaining caps: `g_rem = g_cap - group_total`, `s_rem = s_cap - sub_total`, floor 0.

Per-member effective per-batch cap considering rate and burst: `s_eff = s_rem` capped by rate+burst_rem logic during allocation (see below), but for sum we use `min(s_rem, rate+burst_rem if rate>0 else s_rem, with burst consumption considered? For effective cap computation we use rate+burst_rem as upper bound, not just rate).

Sum member effective: `sum_member_eff[g] = sum s_eff for subs in group g` (valid gid only, gid out of range ignored →0 allocation).

Effective group remaining cap per batch: `eff_g_rem[g] = min(g_rem[g], sum_member_eff[g], (group_rate[g]+group_burst_rem[g] if group_rate>0 else g_rem[g]))`. If group has no members, sum_member_eff=0 → eff=0. If rate 0, ignore rate limit (unlimited per batch) unless burst also 0? Rate 0 means unlimited per batch, so eff = min(g_rem, sum_member_eff).

Now allocation with global rebalancing loop for this batch:

```
remaining = L
group_batch_total = [0]*G for this batch (sum of allocations to group across rebalancing iterations)
sub_batch_total = [0]*S for this batch

while remaining > 0:
  # recompute remaining caps after previous iterations of this batch
  g_rem_iter = eff_g_rem[g] - group_batch_total[g] (per group remaining effective cap for this batch)
  s_rem_iter and s_eff_iter similarly for members after previous iterations within same batch
  sum_member_eff_iter per group based on s_eff_iter

  # recompute eff_g_rem_iter = min(g_rem_iter, sum_member_eff_iter, group_rate+burst_rem - already allocated in this batch? Actually rate is per batch max, so remaining rate for this batch = rate+burst_rem - already allocated in this batch for that group)

  # For simplicity, we incorporate rate limiting inside allocate_batch primitive via caps that already include rate+burst and burst consumption

  # Group level: allocate remaining via allocate_batch primitive (min+priority+credit-decay) with group prio/min/weight/cap=eff_g_rem_iter/credit
  # This returns group_batch_iter per group for this iteration
  # For each group, allocate its share to members: collect its subs, allocate group_batch_iter[g] to them via allocate_batch with member caps = s_eff_iter, etc.
  # Compute actually allocated to members in this iteration: sum of sub allocations in this iteration
  # If actually allocated ==0: break (no progress)
  # remaining -= actually allocated
  # Merge group_batch_total += group_batch_iter, sub_batch_total += sub allocations
  # Update burst consumption: for any group/sub whose per-batch allocation exceeded its rate (rate>0 and allocations > rate), consume burst_rem by excess

  # Continue loop
```

After loop, any remaining load that could not be allocated due to caps/rates is discarded (total allocated = min(batch load, sum effective caps)).

For subscriber level within each group, same rebalancing loop concept applies iteratively if needed? But since group allocation already limited to sum member effective caps, within-group should be able to allocate fully, but we still have multi-round credit-decay loop inside allocate_batch.

**Primitive allocate_batch(load, prio, min, weight, cap, rate, burst_rem, credit) → batch_alloc**

Exact steps, fully explicit for determinism:

- n = len
- batch = [0]*n
- If load<=0: update credits (active cap>0: if batch>0 decay else boost) and return batch (no allocation for non-positive load in this primitive; deallocation handled at higher level)
- Order for min phase: sort indices by priority descending, tie by original idx ascending
- Rem = load
- For i in order:
  - If rem==0 break
  - If cap[i]<=0 continue
  - Give = min[i]
  - Cap feasible: give = min(give, cap[i], (rate[i] if rate>0 else give) + burst_rem[i]?) Actually rate+burst logic: effective per-batch max for this item is rate+burst_rem if rate>0 else cap. So give = min(give, effective_max, rem). For min phase, effective_max = (rate+burst_rem if rate>0 else cap)
  - batch[i]+=give, rem-=give
  - If give > rate[i] and rate[i]>0: consume burst_rem by give-rate

- Remaining caps after min: rem_cap[i] = cap[i] - batch[i], remaining rate after min: rem_rate[i] = (rate[i] - batch[i]) if rate>0 else large? Actually rate is per-batch max, so after min allocation, remaining rate = rate - batch[i] if rate>0 else large, plus burst_rem still available.

- For weighted phase, we need effective remaining caps that incorporate rate and burst: eff_rem_cap[i] = rem_cap[i]; if rate>0: max allowed for this batch from this item is rate - min_alloc? Actually rate limits total per batch, not just weighted phase. So remaining rate after min: rate_rem = rate - batch[i] if rate>0 else large. Effective remaining for weighted phase = min(rem_cap[i], rate_rem + burst_rem[i] if rate>0 else rem_cap[i])

- Then multi-round credit-decay loop for remaining rem:

  `alloc_w=0`, `credit_tmp=credits copy`, `rem_w=rem`

  While rem_w>0:
    active = [i | alloc_w[i] < eff_rem_cap[i]] input order
    if empty break
    total = sum credit_tmp[active]
    If total==0: efficient RR fallback bulk cycles + partial input order (see previous spec) for remaining rem_w, then break
    delta=0, used=0
    For i in active: share = floor(rem_w * credit_tmp[i] / total) capped to eff_rem_cap[i]-alloc_w[i] **without 64-bit overflow using 128-bit**
    If used==0: best = active max credit_tmp tie lowest idx, give 1
    rem_w -= used
    Update credit_tmp: served→credit_tmp/2+1 else +weight

  Merge alloc_w into batch.

- Credit update for next batch (persistent): for any item where original cap>0 (was active at batch start), if batch[i]>0: credit = credit/2+1 else credit += weight

- Weight update for next batch (dynamic, exact): if batch[i]>0: weight = max(1, floor(weight*0.9)) else weight = weight+1

- Burst consumption: for any item where rate>0 and batch[i] > rate: excess = batch[i] - rate, consume burst_rem by min(excess, burst_rem), burst_rem -= consumed. If batch[i] <= rate, no burst consumed.

Return batch allocation for this primitive (per item) for this batch iteration.

This primitive is used at group level and subscriber level.

## Large-scale and overflow safety

- Loads, caps, weights up to 1e12, products like remaining*credit up to 1e24 and 3*4e18=1.2e19 overflow signed 64-bit, must use 128-bit via math/bits or big.

## Examples

### Example 1 - hierarchical basic, rate unlimited

Input:
```
1
16
2
10 0 5 10 0 0
5 0 3 10 0 0
4
0 10 0 5 6 0 0
0 5 0 3 9 0 0
1 5 0 4 3 0 0
1 1 0 1 12 0 0
```
Output:
```
6,4,3,3
15,7
10,12
5,3
6,4,3,3
```

Wait output format T+4 lines: first T lines per-sub batch, then per-group total, per-sub total, per-group final credits, per-sub final credits. For T=1 load16, after batch: per-group totals? Group0 total? Group0 gets 10, group1 gets6 → per-group total line "10,6". Per-sub total same as batch 6,4,3,3. Credits after: group credits 5→3, 3→2? Actually group weight decays: group0 weight5 served → max(1,4)=4? Wait weight dynamic: if served, weight = max(1, floor(weight*0.9)). For group0 weight5 → 4, group1 weight3→2. Credits: group0 credit5 →3, group1 credit3→2. So per-group final credits "3,2". Sub credits: sub0 w5→4 credit 5→3, sub1 w3→2 credit3→2, sub2 w4→3 credit4→3? Actually w4*0.9=3, credit4→3, sub3 w1→1? w1*0.9=0→max1=1, credit1→1. So sub final credits "3,2,3,1" and final weights? We output final credits, not weights. So per-sub final credits "3,2,3,1".

So full output 1+4=5 lines:
```
6,4,3,3
10,6
10,12? Wait per-sub total? Actually per-sub total after 1 batch is 6+4+3+3? No per-sub total is per sub: 6,4,3,3. Per-group total 10,6. Per-sub total? Actually we have per-sub total same as batch for T=1, but spec says per-sub total after all batches as S CSV: that's 6,4,3,3. Per-group total G CSV: 10,6. Per-group final credits: 3,2. Per-sub final credits: 3,2,3,1.

So output order: T batch lines (1), then per-group total, per-sub total, per-group final credits, per-sub final credits.

Let's output:
6,4,3,3
10,6
6,4,3,3
3,2
3,2,3,1
```

### Example 2 - rate limiting

Input:
```
1
9
2
0 0 5 10 0 0
0 0 6 10 0 0
3
0 10 2 5 10 2 0
0 5 1 6 10 10 0
1 1 0 6 1 0 0
```
Sub0 rate2, so effective cap 2, min2 → gets2, remaining 7, other subs get 6 and1? Actually with rate, output from oracle with rate is `2,6,1` for batch, per-group totals `8,1`, per-sub totals `2,6,1`, credits `3,2,1`, etc.

Output:
```
2,6,1
8,1
2,6,1
3,2,1
3,2,1
```

### Example 3 - multi-batch persistent credit with dynamic weights

Input:
```
2
6
6
2
0 0 4 11 0 0
0 0 1 6 0 0
3
0 5 0 4 11 0 0
0 1 0 1 6 0 0
1 10 0 2 5 0 0
```
Batch1 4,1,1, batch2 4,1,1, totals 8,2,2, group totals etc.

Output:
```
4,1,1
4,1,1
8,2
8,2,2
2,1
2,1,2
```

### Example 4 - large weight overflow

As before, 500B,500B plus totals and credits.

### Example 5 - negative load (deallocation)

Input:
```
2
10
-4
1
0 0 1 10 0 0
2
0 0 0 1 10 0 0
0 0 0 1 10 0 0
```
Batch1 load10 → 5,5, batch2 load -4 deallocates by priority: higher priority first? Both same priority, input order, so dealloc 4 from sub0? Actually dealloc returns capacity, so sub0 gets -4.

Output:
```
5,5
-4,0
6,0
1,5
...
```

There are hidden tests for all implicit robustness: blank lines, invalid gid, min>cap, zero caps, large T, rate+burst, dynamic weights, negative loads, rebalancing unused.

Handle sensibly.
