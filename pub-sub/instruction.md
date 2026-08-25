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

## Failing cases

The program currently produces the wrong output for these inputs. The **correct** output
is shown; the program prints something else.

Input:

```
3
5
5
5
1
0 0 1 100 0 0
2
0 0 0 3 50 0 0 1
0 0 0 1 50 0 0 1
```

Correct output:

```
4,1
4,1
4,1
```

Input:

```
3
5
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
1,1
```

Input:

```
3
1
1
10
1
0 0 1 100 0 0
2
0 0 0 1 10 0 0 1
0 0 0 2 10 0 0 1
```

Correct output:

```
0,1
1,0
4,6
```

Input:

```
4
1
1
1
12
1
0 0 1 100 0 0
2
0 0 0 1 20 0 0 1
0 0 0 4 20 0 0 1
```

Correct output:

```
0,1
0,1
1,0
5,7
```

## Task

Repair the program in `/app` so it produces the correct allocation for these cases and in
general — the same allocation policy the existing code already implements, minus the
defects. Standard library only.
