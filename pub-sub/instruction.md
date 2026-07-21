# Build the broker message allocator

Implement a Go program at `/app/main.go` — a message broker's **fan-out allocator** that distributes a batch of pending messages across weighted, capacity-limited subscribers.

## Input / output

Read from standard input:

```
<load>
<weight> <cap>
<weight> <cap>
...
```

- `<load>` is the number of messages to allocate (non-negative integer).
- Each following line describes one subscriber: its integer `weight` (≥ 1) and integer `cap` (≥ 0), in order.

Write to standard output a single line: the number of messages allocated to each subscriber, in input order, comma-separated. The allocation must never exceed any subscriber's cap, and total allocated equals `min(load, sum caps)`.

Build: `cd /app && go build -o /app/allocator .`

Standard library only.

## Algorithm - credit-decay weighted allocation

You must implement this exact multi-round algorithm:

```
alloc[i] = 0 for all
credit[i] = weight[i] for all
remaining = load

loop while remaining > 0:
  active = { i | alloc[i] < cap[i] } in input order
  if active empty: break

  total = sum credit[i] for i in active

  if total == 0:
    # credits drained - fallback to round-robin in input order
    for remaining > 0:
      progressed = false
      for i in active in order:
        if remaining==0: break
        if alloc[i] < cap[i]:
          alloc[i] += 1
          remaining -= 1
          progressed = true
      if not progressed: break
    break

  delta[i]=0 for all
  used=0
  for i in active:
    share = (remaining * credit[i]) / total   # integer division
    share = min(share, cap[i]-alloc[i])
    alloc[i] += share
    delta[i] = share
    used += share

  if used == 0:
    # guarantee progress - give 1 to highest-credit active, tie lowest index
    best = active[0] with max credit
    alloc[best] += 1
    delta[best] = 1
    used = 1

  remaining -= used

  for i in active:
    if delta[i] > 0:
      credit[i] = credit[i]/2 + 1
    else:
      credit[i] += weight[i]
```

## Examples

Input:
```
16
5 6
3 9
4 3
1 12
```
Output: `6,5,3,2`

Input:
```
9
5 10
6 10
6 1
```
Output: `3,5,1`

Input:
```
6
4 11
1 6
2 5
```
Output: `4,0,2`
