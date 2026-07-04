# Fix the DR Buffer capacity planner

A Go program lives in `/app` — a disaster-recovery capacity planner. It reads a
JSON report on standard input and prints a JSON summary on standard output:
per-region worst-case load under failures, whether each region is overwhelmed,
each region's DR buffer, and — for the fleet — whether it is resilient, its total
capacity shortfall, and the worst cascade scenario.

The program builds and runs, but **it has bugs**: some outputs are wrong. Your job
is to find and fix the defect(s) in the Go source under `/app` so the program is
correct for all inputs. The logic is sound apart from the defects — do **not**
rewrite it from scratch, and keep the same input/output format.

## A failing case

Input:

```json
{"maxFailures": 1, "regions": [
  {"name": "A", "capacity_rps": 100, "demand_rps": 80},
  {"name": "B", "capacity_rps": 100, "demand_rps": 80},
  {"name": "C", "capacity_rps": 100, "demand_rps": 10},
  {"name": "D", "capacity_rps": 100, "demand_rps": 10}
]}
```

The program currently reports:

```
capacityShortfall: -81.48...
worstScenario: {"failed": ["A"], "collapsed": ["A","B","C","D"], "cascadeRounds": 2}
```

Both are wrong. The correct result for this input is:

```
capacityShortfall: 37.037...
worstScenario: {"failed": ["A"], "collapsed": ["A","B"], "cascadeRounds": 1}
```

## Another failing case

For three regions each with capacity 1000 rps and demand 100 rps and
`maxFailures` 1 — a fleet that comfortably survives any single failure — the
program reports `capacityShortfall: -2500`, but the correct value is `0`.

## Task

Repair the program in `/app` so it is correct on these cases and in general.

Build: `cd /app && go build -o /app/drbuffer .`
