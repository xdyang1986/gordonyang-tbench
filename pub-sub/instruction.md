# Build the hierarchical broker allocator with min, priority and multi-batch credit

Implement a Go program at `/app/main.go` that simulates a **multi-batch hierarchical broker** with **minimum guarantees**, **priority** and **credit-decay weighted fair share**. The spec below is fully explicit and uniquely determines output - no alternative coherent interpretation should be considered correct.

## Input format

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap   (G lines, groups 0..G-1 in order)
S
gid prio min weight cap       (S lines, subs 0..S-1 in input order)
```

- `T` batches (≥1)
- `load_i` ≥0
- `G` groups (≥1)
- Group: priority int (higher = higher), min ≥0 per-batch, weight ≥1, cap ≥0 total across all batches
- `S` subscribers (≥1)
- Subscriber: gid ideally in [0,G-1], priority int, min ≥0 per-batch, weight ≥1, cap ≥0 total
- Input may contain blank lines and extra spaces - you must handle robustly: trim spaces, skip blank lines, split by whitespace.

Robustness (explicit):
- If `gid` out of range [0,G-1] or group has no members, that subscriber gets 0 allocation, does not crash, and does not affect other groups' effective caps beyond its cap not counting.
- If `min > cap` or `min > remaining cap`, min is capped to `min(min, cap, remaining load)` - i.e., min cannot exceed what is feasible.
- If `min > remaining load`, allocate by priority order (higher priority first, tie lower index) until load exhausted.
- Group with no members has effective cap 0.
- Large numbers up to 1e12 may appear - must be O(n log n) or O(n * rounds) where rounds bounded by caps, not O(load). Use 64-bit.

## Output format

`T` lines, each line `S` comma-separated ints in input order, allocation per sub for that batch. No spaces. Each batch allocation respects remaining caps. Cumulative allocations never exceed caps. If `S==0`, output empty line per batch (but spec says S≥1).

Build: `cd /app && go build -o /app/allocator .` Stdlib only.

## State

- `group_total[g]` cumulative, initially 0
- `sub_total[s]` cumulative, initially 0
- `group_credit[g] = group_weight[g]` initially
- `sub_credit[s] = sub_weight[s]` initially
- Credits persist across batches, updated after each batch.

## Primitive allocate_batch

This primitive is used at both levels. Given `load`, arrays `prio`, `mins`, `weights`, `caps` (remaining caps for this batch), `credits` (mutable, persistent), returns `batch_alloc`. Exact pseudocode:

```
batch = [0]*n
if n==0 or load<=0: update credits for active items (cap>0: if batch[i]>0 decay else boost) and return batch

# min phase - priority order
order = sort indices by prio descending, tie by idx ascending
rem = load
for i in order:
  if rem==0: break
  if caps[i]<=0: continue
  give = mins[i]
  if give > caps[i]: give = caps[i]
  if give > rem: give = rem
  batch[i] += give
  rem -= give

# remaining caps after min
rem_cap = [caps[i] - batch[i] for i]

# weighted phase - credit-decay multi-round
alloc_w = [0]*n
credit_tmp = credits copy
rem_w = rem
while rem_w > 0:
  active = [i | alloc_w[i] < rem_cap[i]]  # input order
  if empty: break
  total = sum credit_tmp[i] for i in active
  if total == 0:
    # RR fallback - must be efficient for large rem_w, deterministic input order
    # Efficient version: bulk cycles + partial
    while rem_w > 0:
      cur_active = [i for i in active if alloc_w[i] < rem_cap[i]]
      if empty: break
      # full cycles possible
      min_rem = min(rem_cap[i]-alloc_w[i] for i in cur_active)
      cycles = min(min_rem, rem_w // len(cur_active))
      if cycles > 0:
        for i in cur_active:
          alloc_w[i] += cycles
        rem_w -= cycles * len(cur_active)
      # partial one-by-one for remainder < len(cur_active)
      made=False
      for i in cur_active:
        if rem_w==0: break
        if alloc_w[i] < rem_cap[i]:
          alloc_w[i]+=1
          rem_w-=1
          made=True
      if not made:
        break
    break
  delta=[0]*n
  used=0
  for i in active: # input order
    share = (rem_w * credit_tmp[i]) // total
    if share > rem_cap[i] - alloc_w[i]:
      share = rem_cap[i] - alloc_w[i]
    alloc_w[i]+=share
    delta[i]=share
    used+=share
  if used==0:
    best = active[0] with max credit_tmp, tie lowest index
    alloc_w[best]+=1
    delta[best]=1
    used=1
  rem_w -= used
  for i in active:
    if delta[i]>0:
      credit_tmp[i] = credit_tmp[i]//2 + 1
    else:
      credit_tmp[i] += weights[i]

for i: batch[i] += alloc_w[i]

# credit update for next batch - based on total batch including min
for i in range(n):
  if caps[i] > 0: # was active at start of batch (remaining cap >0)
    if batch[i] > 0:
      credits[i] = credits[i]//2 + 1
    else:
      credits[i] += weights[i]

return batch
```

This primitive uniquely determines output: no alternative decay (must be `/2+1` and `+weight`) is considered correct.

## Hierarchical multi-batch

For each batch t with load L=loads[t]:

1. Remaining caps: `g_rem_cap[g] = g_cap[g] - group_total[g]`, `s_rem_cap[s] = s_cap[s] - sub_total[s]` (floor at 0)

2. Sum member remaining caps per group: `sum_member_rem[g] = sum s_rem_cap[s] for s where gid==g and 0<=gid<G`

3. Effective remaining group cap: `eff_g_rem[g] = min(g_rem_cap[g], sum_member_rem[g])`. If group has no members, sum=0 → eff=0.

4. Group-level: `group_batch = allocate_batch(L, group_prio, group_min, group_weight, eff_g_rem, group_credit)`

5. Per group: collect subscribers indices `idxs` where gid==g in input order. If empty, skip. Let `gl = group_batch[g]`. Build per-member arrays in order of `idxs`: `m_prio`, `m_min`, `m_weight`, `m_cap = s_rem_cap[idx]`, `m_credit = sub_credit[idx]`. `sub_alloc_in_group = allocate_batch(gl, m_prio, m_min, m_weight, m_cap, m_credit)`. Scatter back to global `sub_batch[global_idx] = sub_alloc`.

6. Update: `group_total[g] += group_batch[g]`, `sub_total[s] += sub_batch[s]`, credits already updated inside allocate_batch.

7. Output line: `sub_batch` for all S subs as CSV in input order.

Total allocated per batch = min(batch load, sum eff remaining caps). Per-group sum respects effective caps. Per-sub respects caps. Deterministic.

## Examples

### Example 1 - basic hierarchical with min=0

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
Here group1 effective cap 1 (only one sub cap1). Group alloc: load9, eff caps 10 and1 total11 → group alloc 8,1. Within group0 load8 with mins 2,1 priority10 vs5: min phase gives 2 and1 (rem5), weighted: credits 5 and6 total11, 5*5/11=2, 5*6/11=2 → 4,3? Actually with caps remaining after min: caps 8 and9, credits after min? No credit_tmp starts same, but min phase does not affect credit_tmp. So 5*5/11=2, 5*6/11=2 used4 rem1 best credit6 → sub1 gets1 total 4,4. Output 4,4,1.

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

### Example 4 - edge: min > cap (min capped)

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

### Example 5 - blank lines and spaces robust

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
