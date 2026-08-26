# Fix the broker message allocator

A Go program lives in `/app` (`main.go`). It is a message broker's **hierarchical
allocator**: it reads a series of batch loads together with a set of groups and
subscribers, and for each batch decides how many messages every subscriber receives.
Allocator state carries over from one batch to the next.

It builds and runs, but **it has bugs**: for some inputs the allocations it prints are
wrong. Your job is to find and fix the defects in the Go source under `/app` so the
program is correct for all inputs. The logic is sound apart from the defects — do **not**
rewrite it from scratch, and keep the same input/output format.

## Input / output

Read from standard input:

```
T
load_1
...
load_T
G
g_prio g_min g_weight g_cap g_rate g_burst        (G lines)
S
gid prio min weight cap rate burst cost           (S lines)
```

Write to standard output exactly `T` lines. Each line is `S` comma-separated integers —
the count allocated to each subscriber for that batch, in input order, no spaces. Counts
may be negative when a load is negative.

Build: `cd /app && go build -o /app/allocator .` Standard library only.

## Invariants

The correct program always respects these properties (they are not recurrences, just
limits it never exceeds):

- A subscriber's cumulative cost (`sum count * cost` over all batches so far) never
  exceeds its `cap`.
- When `rate > 0`, a subscriber's per-batch count never exceeds `rate + remaining burst`;
  likewise for groups — a group's per-batch total never exceeds `group rate + remaining group burst`.
- Per-batch total allocated equals `min(load, available capacity)` (limited by caps and
  rates), with no over-allocation.
- Counts are non-negative except when `load` is negative (deallocation).

Any output violating these invariants is flatly wrong, regardless of policy.

## Failing cases

The program currently produces the wrong output for these inputs. The **correct** output
is shown; the program prints something else. Each shipped output violates an invariant
above.

Input:

```
2
15
12
2
0 0 1 6 0 0
0 0 1 5 0 0
3
0 0 0 3 5 0 0 1
1 0 0 3 9 0 0 2
1 0 0 2 11 0 0 1
```

Correct output:

```
5,3,2
0,0,0
```

Shipped prints `6,3,2` / `0,0,0` — subscriber 0 has `cap 5 cost 1`, so 6 messages puts
cumulative cost at 6 against a cap of 5, violating the cap invariant. Whatever the
intended policy, this is objectively wrong.

Input:

```
2
5
5
1
0 0 1 100 2 3
2
0 0 0 1 50 2 3 1
0 0 0 1 50 0 0 1
```

Correct output:

```
3,2
1,1
```

Shipped prints `3,2` / `3,2` — second batch exceeds the group's `rate + burst`
(`rate 2 burst 3` consumed in batch 1, so batch 2 max is 2, not 5), violating the group
rate invariant.

Input:

```
2
5
5
1
0 0 1 100 0 0
1
0 0 0 1 50 2 3 1
```

Correct output:

```
5
2
```

Shipped prints `5` / `5` — second batch exceeds the subscriber's rate
(`rate 2 burst 3` consumed in batch 1, so batch 2 max is 2, not 5), violating the
subscriber rate invariant.

Input:

```
3
4
4
10
1
0 0 1 200 0 0
2
0 0 0 1 34 0 0 1
0 0 0 3 33 0 0 1
```

Correct output:

```
1,3
1,3
3,7
```

Shipped prints `1,3` / `0,4` / `10,0`. No rates, bursts or costs are involved here — only the per-batch credit update differs. This isolates the credit recurrence and falsifies the common guess c*9/10 which would give 1,3 / 0,4 / 5,5.

## Task

Repair the program in `/app` so it produces the correct allocation for these cases and in
general — the same allocation policy the existing code already implements, minus the
defects. Standard library only.
