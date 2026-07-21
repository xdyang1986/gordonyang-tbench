# Build the hierarchical broker allocator

Implement a Go program at `/app/main.go` that is a **hierarchical fan-out allocator** for a message broker. Messages are first allocated to groups (by group weight/cap), then within each group to subscribers (by subscriber weight/cap). Both levels use the same credit-decay weighted algorithm.

## Input format

```
<load>
<G>
<group_weight> <group_cap>
... G lines, groups 0..G-1 in order
<S>
<group_id> <weight> <cap>
... S lines, subscribers in input order, group_id in [0,G-1]
```

- `<load>` total messages to allocate (≥0)
- `G` number of groups (≥1)
- Each group line: weight ≥1, cap ≥0
- `S` number of subscribers (≥1)
- Each subscriber line: group_id, weight ≥1, cap ≥0

## Output format

Single line: comma-separated allocation per subscriber in input order (S integers). Each respects its own cap, and sum of allocations in a group never exceeds `min(group_cap, sum member caps)` and total sum = `min(load, sum effective group caps)`.

Build: `cd /app && go build -o /app/allocator .` Standard library only.

## Algorithm

Define a primitive `allocate(load, weights, caps)` → allocations:

```
alloc = [0]*n
credit = weights copy
rem = load
while rem > 0:
  active = [i | alloc[i] < caps[i]] in order
  if empty: break
  total = sum credit[i] for i in active
  if total == 0:
    # round-robin fallback
    while rem > 0:
      made = false
      for i in active:
        if rem==0: break
        if alloc[i] < caps[i]:
          alloc[i] += 1
          rem -= 1
          made = true
      if not made: break
    break
  delta = [0]*n
  used = 0
  for i in active:
    share = (rem * credit[i]) // total
    share = min(share, caps[i]-alloc[i])
    alloc[i] += share
    delta[i] = share
    used += share
  if used == 0:
    best = active[0] with max credit, tie lowest index
    alloc[best] += 1
    delta[best] = 1
    used = 1
  rem -= used
  for i in active:
    if delta[i] > 0:
      credit[i] = credit[i]//2 + 1
    else:
      credit[i] += weights[i]
return alloc
```

Hierarchical steps:

1. For each group g, compute `sum_member_caps[g] = sum cap of subscribers in g`. Effective cap `eff_cap[g] = min(group_cap[g], sum_member_caps[g])`.

2. Group-level: `group_alloc = allocate(load, group_weights, eff_cap)` (length G).

3. For each group g:
   - Gather its subscribers in input order: indices `idxs[g]`, weights `w_sub`, caps `c_sub`.
   - Let `gl = group_alloc[g]`.
   - `sub_alloc_in_group = allocate(gl, w_sub, c_sub)`.
   - Scatter back to global output in input order.

## Examples

Input:
```
16
2
5 10
3 10
4
0 5 6
0 3 9
1 4 3
1 1 12
```
Explanation: load 16, 2 groups (w5 cap10, w3 cap10), 4 subs (group0: w5 cap6, w3 cap9, group1: w4 cap3, w1 cap12). Group alloc: 10,6. Within group0 load10 → 6,4. Within group1 load6 → 3,3.

Output: `6,4,3,3`

Input:
```
9
2
5 10
6 10
3
0 5 10
0 6 10
1 6 1
```
Output: `3,5,1`

Input:
```
6
2
4 11
1 6
3
0 4 11
0 1 6
1 2 5
```
Output: `4,0,2`
