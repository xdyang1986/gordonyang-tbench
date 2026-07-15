# Control Plane Coordinator

Build a ZooKeeper-style control-plane coordinator in Go at `/app`. It tracks cluster membership, elects a sticky primary, serves service-discovery queries, and optionally persists all state to disk so it survives restarts.

You will implement a single `package main` binary. It reads commands from stdin, updates in-memory state, optionally appends to a durable log, and writes one line of output per query to stdout.

---

## Configuration

The first non-empty line of stdin is:

```
timeout=<seconds>
```

* `timeout` — integer ≥0. A node is considered alive for discovery queries only if `timestamp - last_heartbeat ≤ timeout`. `0` disables expiration. Expiration never affects primary election.

The coordinator also reads environment variable `COORD_STATE_DIR`:

* unset or empty → **in-memory mode**: no disk writes, `COMPACT` is a no-op.
* set to a directory path → **durable mode**: append-only log at `$COORD_STATE_DIR/coordinator.log`, recovered on startup, compacted on demand.

---

## Command Stream

After the config line, each non-blank line is one command. Tokens are space-separated, no quoted strings. Timestamps are non-negative integers and non-decreasing in valid inputs. Commands are processed strictly in order.

### State-changing commands — no output

**`REGISTER <node_id> <address> <zone> <weight> <timestamp>`**
Create or update a node. Store address, zone, weight (integer ≥0). Mark not failed, set last_heartbeat to timestamp. Revives a previously failed node. Always logged in durable mode.

**`HEARTBEAT <node_id> <timestamp>`**
Refresh last_heartbeat if the node exists and is not failed. Ignored otherwise. Logged only when it updates state.

**`FAIL <node_id> <timestamp>`**
Mark node failed immediately. Future heartbeats are ignored until a new REGISTER. Logged only if node exists.

**`DEMOTE <timestamp>`**
Voluntarily step the current primary down **without failing it**, then immediately re-elect. If there is no current primary this is a no-op (no epoch change, nothing logged). Otherwise clear the primary and re-elect the best non-failed node by the standard ordering (highest weight → most recent last_heartbeat → smallest address → smallest node_id), incrementing the epoch by 1. The demoted node stays registered and non-failed and remains eligible, so if it is still the best it is re-elected — **the epoch still increments even though the primary is unchanged**. Re-election ignores heartbeat expiration, exactly like FAIL re-election. Logged only when it changes state (i.e. only when there was a primary).

**`TRANSFER <node_id> <timestamp>`**
Explicit leadership handoff. If `<node_id>` exists and is not failed, it becomes the primary and the epoch increments by 1 — **even if it has lower weight or worse rank than the current incumbent** (the ranking is ignored on an explicit handoff, so leadership can move with no FAIL). Transferring to the current primary is still a valid handoff to itself and increments the epoch. A TRANSFER to an unknown or failed node is ignored (no epoch change, nothing logged), like HEARTBEAT. Logged only when it changes state.

**`COMPACT <timestamp>`**
In durable mode, rewrite the log to the minimal record set that reconstructs the current state and current primary exactly. Atomic via temp file + rename. In-memory mode: no-op. No output.

### Query commands — one output line each

All queries take a timestamp used for aliveness evaluation. Aliveness for discovery queries means: node is registered, not failed, and `timeout==0 or timestamp-last_heartbeat ≤ timeout`.

**`QUERY_PRIMARY <timestamp>`**
Output <primary_node_id> <epoch> (two tokens, one space), or NONE if there is no primary.

Leadership is sticky and independent of aliveness. The coordinator maintains an integer leadership epoch (term), starting at 0:
- When there is no primary, elect the best non-failed node by: highest weight → most recent last_heartbeat → smallest address → smallest node_id, and increment the epoch by 1.
- Once elected, the primary stays primary through new REGISTERs, heartbeats, and expirations. A higher-weight node does not preempt, and the epoch does not change.
- Leadership changes only via an explicit `FAIL`, `DEMOTE`, or `TRANSFER`, and every such change begins a new term (the epoch increments by 1):
  - `FAIL` of the current primary re-elects the best remaining non-failed node by the ordering above; if none remain, there is no primary and the epoch is left unchanged (the next election increments it). A FAIL of a non-primary node never changes leadership.
  - `DEMOTE` re-elects the best non-failed node with the incumbent still eligible (see DEMOTE); the epoch always increments when there is a primary to demote.
  - `TRANSFER` sets the primary to the named non-failed node regardless of rank (see TRANSFER); the epoch always increments on a valid handoff.

**`QUERY_CONNECT <client_id> <timestamp>`**
Output address for simple hash routing, or `NONE` if no alive nodes.

Take alive node_ids sorted ascending. Compute `h = sum of byte values of client_id`. Pick index `h % len(alive)`. Output that node's address.

**`QUERY_ROUTE <client_id> <timestamp>`**
Output address for stable routing, or `NONE`.

Must satisfy three properties: (1) deterministic for a given client and alive set; (2) minimal-disruption — when the alive set changes, only clients whose old node left or whose new node joined may change assignment, with no reshuffle among unchanged nodes; (3) balanced — with multiple alive nodes and many distinct clients, assignments are spread across the alive set, not collapsed onto a single node. Any scheme meeting all three is acceptable.

**`QUERY_REPLICAS <k> <timestamp>`**
Output comma-separated node_id list of size `min(k, alive_count)`, or `NONE` if no alive nodes. `k ≥1`.

Order alive nodes by preference: highest weight → most recent heartbeat → smallest address → smallest id.

Phase 1 — zone diversity: scan preference order, pick each node whose zone has not yet been selected, until k picked or scan ends.
Phase 2 — fill: scan preference order again, pick highest-preference not-yet-picked nodes ignoring zone, until k picked.

Output selected ids in selection order, no spaces.

**`QUERY_NODES <timestamp>`**
Output comma-separated alive node_ids sorted ascending, or `NONE`.

---

## Output Format

For each query in input order, write exactly one line: `NONE`, a node_id, an address, or a comma list. No header, no extra spaces, no trailing blank lines required beyond newline. Flush and exit 0 on valid input. On invalid input, exit non-zero; output is unspecified.

Invalid input includes: malformed config, unknown command name, wrong arity, non-integer or negative numeric field, empty required string. `HEARTBEAT` for unknown node is silently ignored, not an error; likewise `TRANSFER` to an unknown or failed node is ignored, not an error.

---

## Durable Persistence

Only active when `COORD_STATE_DIR` is set.

**Startup recovery:** create directory if needed. Before reading stdin, replay `$COORD_STATE_DIR/coordinator.log` record by record in order. Each record must reconstruct REGISTER, HEARTBEAT, FAIL, DEMOTE, or TRANSFER exactly as originally processed, so primary election order and the epoch are preserved. Stop at first incomplete or corrupt record; discard it and all following bytes. Truncate the log to the valid prefix so later appends are clean. Never fail startup due to a torn tail. An EPOCH record (produced by compaction) sets the current epoch on replay.

**Log format — `coordinator.log`:**
Sequence of records, each:
```
uint32 little-endian  payload_len
uint32 little-endian  crc32 IEEE of payload
payload_len bytes     UTF-8 payload
```
Payload is the command text exactly as logged: e.g. `REGISTER n1 10.0.0.1:8080 east 5 7`, no newline. A record is valid only if 8 header bytes plus payload_len bytes are present and CRC matches.

**What is logged, in order:**
* `REGISTER` — always.
* `HEARTBEAT` — only when it updates an existing non-failed node.
* `FAIL` — only when node exists.
* `DEMOTE` — only when it changes state (i.e. only when there was a primary to step down).
* `TRANSFER` — only when it changes state (i.e. only for a valid handoff to a known non-failed node).
Queries and COMPACT are never appended as payloads; COMPACT rewrites the whole file instead.
- EPOCH <n> — written only by compaction, never by live appends; on replay it sets the current epoch to n.

Each append must be durable before process exit.

**Compaction:** `COMPACT` writes a new temporary file `$COORD_STATE_DIR/coordinator.log.tmp` containing for each known node a REGISTER with its current address zone weight last_heartbeat, followed by FAIL if currently failed, in an order that replays to the same primary as before (emit the current primary's REGISTER first so replay re-elects the same incumbent even when it is not the highest-ranked node), and finally a single EPOCH <n> record carrying the current epoch (the election count cannot be recomputed from a minimized log, so it is persisted explicitly). Then atomically rename over coordinator.log. Recovery ignores `.tmp` files.

---

## Functional Requirements Summary

1. REGISTER creates/updates and revives; HEARTBEAT refreshes; FAIL kills.
2. Aliveness for discovery queries uses timeout lazily at query time.
3. Primary election is sticky (weight → freshness → address → id); leadership changes only via explicit FAIL, DEMOTE, or TRANSFER. DEMOTE re-elects best with the incumbent still eligible; TRANSFER forces a named non-failed node regardless of rank. A leadership epoch increments on each election/handoff, is reported by QUERY_PRIMARY, and is preserved across restart and compaction.
4. QUERY_CONNECT uses sorted-id modulo hash.
5. QUERY_ROUTE is deterministic and stable under membership change.
6. QUERY_REPLICAS is preference-ordered zone-diverse then fill.
7. Deterministic output for same stdin and same starting disk state. No randomness, no time. Sort all map keys.
8. Durable mode survives restarts with crash-consistent recovery and atomic compaction preserving primary and epoch.
9. Go standard library only.
10. Invalid config or command → non-zero exit.

---

## Examples

### In-memory example

Input:
```
timeout=0
REGISTER edge 10.0.0.9:9 west 1 0
REGISTER core1 10.0.0.1:1 east 5 3
REGISTER core2 10.0.0.2:2 east 5 8
REGISTER mid 10.0.0.3:3 west 5 8
QUERY_PRIMARY 10
FAIL edge 11
QUERY_PRIMARY 12
QUERY_REPLICAS 2 12
QUERY_NODES 12
```

Output:
```
edge 1
core2 2
core2,mid
core1,core2,mid
```

`edge` registers first and becomes sticky primary despite lower weight. After explicit FAIL, re-elect among remaining: weight 5 tie, freshest heartbeat 8 tie between core2 and mid, core2 address sorts first. Replicas pick one per zone in preference order, then nodes list sorted.

### Leadership state-machine example

Input:
```
timeout=0
REGISTER a a_addr z1 1 0
REGISTER b b_addr z1 9 0
QUERY_PRIMARY 1
DEMOTE 2
QUERY_PRIMARY 3
TRANSFER a 4
QUERY_PRIMARY 5
REGISTER c c_addr z1 9 6
QUERY_PRIMARY 7
```

Output:
```
a 1
b 2
a 3
a 3
```

`a` registers first and is sticky primary (term 1) even though `b` has higher weight. `DEMOTE` steps `a` down and re-elects the best non-failed node → `b` (weight 9), term 2. `TRANSFER a` forces leadership back to `a` regardless of rank, term 3. The later higher-weight `REGISTER c` does not preempt the sticky incumbent, so the primary stays `a` at term 3.

### Durable example

With `COORD_STATE_DIR=/var/coord`, first run stdin:
```
timeout=0
REGISTER low la west 1 0
REGISTER high ha east 9 0
```
Second run with same directory, stdin:
```
timeout=0
QUERY_PRIMARY 5
```
Output must be:
```
low 1
```
`low` was first registered and remains sticky primary after recovery, even though `high` has higher weight.

---

## Non-Functional

* Go, `package main`, builds with `go build -o <binary> .`.
* Reads stdin, writes stdout, exit codes as above.
* Single-threaded sequential processing.
* Deterministic sorting for all map iteration.

Implement at `/app`. The test harness builds your binary, feeds stdin, checks stdout exactly, and restarts processes with a shared `COORD_STATE_DIR` to verify durability and crash-consistent recovery.
