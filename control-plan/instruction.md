# Fix the control-plane coordinator

A Go program lives in `/app` — a ZooKeeper-like control-plane coordinator. It reads a
config line then a stream of commands on stdin (`REGISTER`, `HEARTBEAT`, `FAIL`,
`COMPACT`, and `QUERY_*`) and writes one line of output per query to stdout. When the
environment variable `COORD_STATE_DIR` is set it also persists state to a durable
append-only log in that directory and recovers it on startup.

The program builds and runs, but **it has bugs**: some outputs are wrong. Your job is to
find and fix the defect(s) in the Go source under `/app` so the program is correct for all
inputs. The logic is sound apart from the defects — do **not** rewrite it from scratch, and
keep the same commands, input parsing, and output format.

Note: the coordinator's leadership is intentionally *sticky* — the first-registered node
stays primary even when a later node has higher weight, and heartbeat expiration never
unseats it. That behavior is **correct and deliberate**; do not "fix" it.

## Failing cases

**1) Leadership term after a failover.**

Input:
```
timeout=0
REGISTER n1 a1 z1 5 0
REGISTER n2 a2 z1 5 0
QUERY_PRIMARY 0
FAIL n1 1
QUERY_PRIMARY 2
```
Currently prints `n1 1` then `n2 1`. The second line is wrong — re-electing after the
primary is `FAIL`ed begins a new leadership term, so the correct output is `n1 1` then
`n2 2`.

**2) Replica selection when zones are exhausted.**

Input:
```
timeout=0
REGISTER a aa Z1 5 0
REGISTER b bb Z1 5 0
QUERY_REPLICAS 2 0
```
Currently prints `a`. `QUERY_REPLICAS 2` must return `min(k, alive) = 2` nodes — after one
per distinct zone, the rest are filled by rank regardless of zone — so the correct output
is `a,b`.

**3) Durability across a restart.**

Run once with `COORD_STATE_DIR=/tmp/coord` on stdin:
```
timeout=0
REGISTER n1 a1 z1 5 0
REGISTER n2 a2 z1 5 0
```
Then run again with the same `COORD_STATE_DIR` on stdin:
```
timeout=0
QUERY_NODES 5
```
Currently prints `n1` — the most recent registration was lost on recovery. The correct
output is `n1,n2`.

## Task

Repair the program in `/app` so it is correct on these cases and in general, keeping the
existing behavior and I/O format intact.

Build: `cd /app && go build -o /app/coordinator .`
