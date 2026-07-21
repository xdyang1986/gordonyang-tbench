# Build the hierarchical broker allocator with min, priority and multi-batch credit

Implement a Go program at `/app/main.go` that is a **multi-batch hierarchical fan-out allocator** with **minimum guarantees**, **priority** and **credit-decay weighted fair share**.

## Input format

```
T
load_1
load_2
...
load_T
G
g_priority_0 g_min_0 g_weight_0 g_cap_0
...
g_priority_{G-1} g_min_{G-1} g_weight_{G-1} g_cap_{G-1}
S
group_id_0 priority_0 min_0 weight_0 cap_0
...
group_id_{S-1} priority_{S-1} min_{S-1} weight_{S-1} cap_{S-1}
```

- `T` number of batches (≥1)
- `load_i` messages in batch i (≥0)
- `G` number of groups (≥1)
- Each group line: priority (int, higher = higher), min (≥0) per-batch minimum, weight (≥1), cap (≥0) total cap across all batches
- `S` number of subscribers (≥1)
- Each subscriber line: group_id in [0,G-1], priority (int), min (≥0) per-batch minimum, weight (≥1), cap (≥0) total cap

## Output format

`T` lines, each line comma-separated allocation per subscriber in input order (S integers) for that batch. Each allocation respects remaining caps, and allocations are cumulative across batches never exceeding caps.

Build: `cd /app && go build -o /app/allocator .` Standard library only.

## State

- `group_total[g]` cumulative allocated to group, initially 0
- `sub_total[s]` cumulative allocated to subscriber, initially 0
- `group_credit[g] = group_weight[g]` initially
- `sub_credit[s] = sub_weight[s]` initially

## Primitive allocate_with_min_priority

Used at both levels. Given `load`, items each with `priority`, `min`, `weight`, `cap` (remaining cap for this batch), `credit`, and `index` (input order), returns `batch_alloc`:

```
# min phase - priority order
order = sort indices by priority descending, tie by index ascending
rem = load
batch_alloc = [0]*n
for i in order:
  if rem==0: break
  give = min(min[i], cap[i], rem)
  batch_alloc[i] += give
  rem -= give

# weighted phase - credit-decay
# remaining caps after min phase
rem_cap = [cap[i] - batch_alloc[i] for i]

# active = i where rem_cap[i] > 0
# Use credit-decay allocator for remaining load rem:

alloc_w = [0]*n
credit_tmp = credit copy
rem_w = rem
while rem_w > 0:
  active = [i for i in range(n) if alloc_w[i] < rem_cap[i]]
  if empty: break
  total = sum credit_tmp[i] for i in active
  if total == 0:
    while rem_w > 0:
      made=False
      for i in active in order (input order):
        if rem_w==0: break
        if alloc_w[i] < rem_cap[i]:
          alloc_w[i]+=1
          rem_w-=1
          made=True
      if not made: break
    break
  delta=[0]*n
  used=0
  for i in active:
    share = (rem_w * credit_tmp[i]) // total
    share = min(share, rem_cap[i]-alloc_w[i])
    alloc_w[i]+=share
    delta[i]=share
    used+=share
  if used==0:
    best = active[0] max credit tie lowest index
    alloc_w[best]+=1
    delta[best]=1
    used=1
  rem_w -= used
  for i in active:
    if delta[i]>0:
      credit_tmp[i] = credit_tmp[i]//2 + 1
    else:
      credit_tmp[i] += weight[i]

# merge
for i in range(n):
  batch_alloc[i] += alloc_w[i]

# credit update for next batch (based on total batch_alloc >0)
for i in range(n):
  if cap[i] > 0: # was active at start (remaining cap >0)
    if batch_alloc[i] > 0:
      credit[i] = credit[i]//2 + 1
    else:
      credit[i] += weight[i]

return batch_alloc
```

Note: credit update uses final `batch_alloc` including min phase: if item got any messages in this batch (min+weighted), decay, else boost.

## Hierarchical multi-batch steps

For each batch t with load L = loads[t]:

1. Remaining caps: `g_rem_cap[g] = g_cap[g] - group_total[g]`, `s_rem_cap[s] = s_cap[s] - sub_total[s]`
2. Sum member remaining caps per group: `sum_member_rem[g] = sum s_rem_cap[s] for s in group g`
3. Effective remaining group cap: `eff_g_rem[g] = min(g_rem_cap[g], sum_member_rem[g])`
4. Group-level allocation: `group_batch = allocate_with_min_priority(L, groups with priority/min/weight/cap=eff_g_rem/credit)`
5. For each group g:
   - Collect its subscribers indices `idxs` in input order
   - If empty, continue
   - Build per-member remaining caps, mins, etc for those subs
   - `sub_batch_in_group = allocate_with_min_priority(group_batch[g], members)`
   - Scatter to global per-sub batch allocation
6. Update totals: `group_total[g] += group_batch[g]`, `sub_total[s] += sub_batch[s]`
7. Output line for this batch: `sub_batch` for all S subs (0 for subs whose group got 0 or not enough) as CSV in input order.

After all batches, total allocated per sub never exceeds its cap, per group never exceeds its cap, and per batch sum = min(batch load, sum eff remaining caps).

## Examples

### Example 1 - hierarchical with min and priority

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
2 groups, both priority, min0, weight5 cap10 and weight3 cap10, 4 subs (group0: prio10 min0 w5 cap6, prio5 min0 w3 cap9, group1: prio5 min0 w4 cap3, prio1 min0 w1 cap12). Load 16 → group alloc 10,6 → within groups 6,4 and 3,3.

Output:
```
6,4,3,3
```

### Example 2 - min guarantees and priority

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
Group caps 10 each, but group1 effective cap is 1 (only one sub cap1). Load 9 → effective caps 10 and1 total11, group alloc weighted 4 and5? With credit decay: round1 4,4 (capped group1 to1? actually group1 cap eff1, so share min(4,1)=1), used5 rem4, group0 gets remaining 4 → group alloc 8,1. Within group0: subs have mins 2 and1, priority10 vs5, load8. Min phase: give 2 to sub0, 1 to sub1 (priority order) rem5, weighted remaining caps 8 and9: credits 5 and6 total11, 5*5/11=2, 5*6/11=2 → sub0 gets 2 more total4, sub1 gets 2 more total3? Actually with caps: sub0 cap10-2=8, sub1 cap10-1=9, 5*5/11=2, 5*6/11=2 used4 rem1 best sub1 credit6>5 → sub1 gets1 total4. So group0 subs 4,4. Group1 sub gets1.

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
T=2, loads 6 and 6, 2 groups, 3 subs. First batch load6 → group alloc 4,1? Actually group weights 4 and1 caps 11,6. Load6 → 4,1? With min0. 6*4/5=4, 6*1/5=1 → 4,1 remaining1 best group0 → 5,1. Within groups: group0 load5 → subs 4,0 and 1,0? Actually subs: group0 has w4 cap11 and w1 cap6 load5 → 4,1. Group1 load1 → sub 2,5 cap5 →1. So batch1 output `4,1,1`. Credits after batch1: group0: 4/2+1=3, group1:1/2+1=1, subs: sub0 4/2+1=3, sub11/2+1=1, sub2 1/2+1=2. Second batch load6 → group alloc with credits 3 vs1: total4, 6*3/4=4 cap remaining group0: cap11-5=6→4, group1:6*1/4=1 cap remaining 5→1 used5 rem1 best group0 →5,1 again. Within group0 load5 with credits sub0=3 sub1=1 total4 → 5*3/4=3 cap remaining sub0 11-4=7→3, sub1 6-1=5→1 used4 rem1 best sub0 →4,1. Group1 load1 with credit2 →1. So batch2 also `4,1,1`.

Output:
```
4,1,1
4,1,1
```
