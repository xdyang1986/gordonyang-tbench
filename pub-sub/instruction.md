# Fix the broker message allocator

A small Go program lives in `/app` (`main.go`). It is a message broker's **fan-out allocator**: given a batch of pending messages and a set of subscribers (each with a weight and a capacity), it decides how many messages each subscriber receives — sharing them out by weight while never exceeding any subscriber's capacity.

It builds and runs, but **it has a bug**: for some inputs the allocation it prints is wrong. Your job is to find and fix the defect in the Go source under `/app` so the program is correct for all inputs. The logic is sound apart from the defect — do **not** rewrite it from scratch, and keep the same input/output format.

## Input / output

Read from standard input:

```
<load>
<weight> <cap>
<weight> <cap>
...
```

- `<load>` is the number of messages to allocate (a non-negative integer).
- Each following line describes one subscriber: its integer `weight` (≥ 1) and integer `cap` (≥ 0), in order.
Write to standard output a single line: the number of messages allocated to each subscriber, in input order, comma-separated. The allocation never exceeds any subscriber's cap, and the total allocated equals `min(load, sum of caps)`.
Build: `cd /app && go build -o /app/allocator .`

## Failing cases

The program currently produces the wrong output for these inputs. The **correct** output is shown.

Input:

```
16
5 6
3 9
4 3
1 12
```

Correct output: `6,5,3,2`  (the program currently prints something else.)

Input:

```
9
5 10
6 10
6 1
```

Correct output: `3,5,1`

Input:

```
6
4 11
1 6
2 5
```

Correct output: `4,0,2`

## Task

Repair the program in `/app` so it produces the correct allocation for these cases and in general (the same weight-and-capacity sharing the existing code already implements, minus the defect). Standard library only.
