# Flink-like Stream Aggregation Engine

Build a Flink-inspired keyed stream aggregation engine in Go at `/app`. It supports multiple streams, event-time windows (tumbling and sliding), per-key aggregations, watermark-based triggering, late-event handling, and optional crash-consistent durable persistence with compaction.

You will implement a single `package main` binary. It reads commands from stdin, updates in-memory state, optionally appends to a durable log, and writes one line of output per query to stdout.

---

## Runtime and Environment

- Go standard library only (enforced by import check; no third-party packages — verifier rejects any import containing a dot).
- Build: `cd /app && go build -o /app/aggregator .`
- Reads stdin line-by-line, writes stdout, exits 0 on valid input.
- Single-threaded sequential processing.

The engine reads environment variable `STREAM_STATE_DIR`:

- unset or empty → **in-memory mode**: no disk writes, `COMPACT` is a no-op.
- set to a directory path → **durable mode**: append-only log at `$STREAM_STATE_DIR/stream.log`, recovered on startup, compacted on demand. The directory is created if needed.

Blank lines (empty or whitespace-only) are ignored.

---

## Naming and Value Validation

- **Stream / Window ID / Key name**: length 1..255 for stream and window, 1..128 for key, characters only `[A-Za-z0-9._-]`, not `.` nor `..`.
- **Value**: signed integer `int64`, allowed range `[-1e12, 1e12]` (outside range is invalid input). Parser must accept normal decimal representation (optional leading `-`).
- **Event time, Watermark, Window size, Slide, Timestamp (processing time)**: integer `>=0`. Value `0` is allowed. Negative values are **invalid input** and must cause non-zero exit.
- **Window size**: integer `>=1 && <= 1e9`.
- **Slide**: integer `>=1 && <= 1e9`.
- **Agg function**: one of `SUM COUNT MIN MAX AVG` (uppercase only).
- **Window start** in query: integer `>=0`.

---

## Command Stream

After start, each non-blank line is one command. Tokens are space-separated (no quoted strings). On **invalid input** (malformed line, unknown command, wrong arity, non-integer where integer expected, invalid name, value out of allowed range, timestamp negative, size/slide out of range, agg unknown), the engine must exit with non-zero status. Output is unspecified in that case.

Application-level errors (e.g., stream does not exist, window does not exist, window_start not aligned, watermark decreasing) are **not** invalid input: they produce a single line `ERROR` and continue, except where specified (`LATE`, `NULL`).

### State-changing commands — no output on success, `ERROR` on application error unless stated

**`CREATE_STREAM <stream> <timestamp>`**
Create stream with empty watermark (`-1` = no watermark yet, so no late events). If stream already exists, idempotent: no-op, nothing logged. Logged in durable mode only when it actually creates.

**`DELETE_STREAM <stream> <timestamp>`**
Delete stream and all its windows and all aggregates and events related to that stream. If stream does not exist, no-op. Logged only when stream existed. Cascades: all window definitions whose stream equals deleted stream are removed.

**`DEFINE_TUMBLING_WINDOW <window_id> <stream> <window_size> <agg_func> <timestamp>`**
Define a tumbling window aggregation per key on `stream` with size `window_size`. Aggregation per key per window.
- Tumbling window assignment: for event with `event_time`, `window_start = floor(event_time / window_size) * window_size`, `window_end = window_start + window_size`, window is `[start, end)`.
- If stream does not exist → output `ERROR`.
- If `window_id` already exists → output `ERROR` (even if same definition).
- If size invalid (already checked as invalid input if <1) → invalid input, but if agg invalid → invalid input.
- Aggregators:
  - `SUM`: sum of values
  - `COUNT`: count of events (value ignored)
  - `MIN`: minimum value
  - `MAX`: maximum value
  - `AVG`: integer division `sum / count` truncated toward zero (Go int64 division). If count=0 result is considered empty.
- On success, no output. Logged only on success.

**`DEFINE_SLIDING_WINDOW <window_id> <stream> <window_size> <slide> <agg_func> <timestamp>`**
Define sliding window.
- Sliding windows start every `slide`: start values `0, slide, 2*slide,...`. Event at time `t` belongs to all windows where `start <= t < start+size` and `start % slide == 0`.
- Number of windows per event is at most `ceil(size/slide)`. Implementation must iterate: latest start `= floor(t / slide)*slide`, then walk backwards while `start > t - size`.
- Same error handling as tumbling, plus `slide` validation. If stream missing → `ERROR`. If window_id exists → `ERROR`.
- Logged only on success.

**`DELETE_WINDOW <window_id> <timestamp>`**
Delete window definition and its aggregates. If window does not exist, no-op. Logged only when existed.

**`INGEST <stream> <key> <value> <event_time> <timestamp>`**
Ingest event.
- If stream missing → output `ERROR`.
- If `event_time <= current_watermark[stream]` (watermark != -1) → event is **late**: output `LATE`, drop event, do not log.
- Otherwise, for each window defined on that stream, compute belonging window_start(s) and update aggregate for that key.
  - For each belonging window, maintain per-key state: sum, count, min, max.
  - Update: `sum += value`, `count +=1`, `min = min(old min, value)`, `max = max(old max, value)`.
  - On success output `OK`.
  - Logged only on `OK` (not LATE, not ERROR). In durable mode log the original command line exactly.

**`ADVANCE_WATERMARK <stream> <watermark> <timestamp>`**
Advance event-time watermark for stream.
- If stream missing → `ERROR`.
- If `watermark < current_watermark` → `ERROR` (decreasing not allowed). Current watermark is `-1` initially, so any `>=0` is allowed first time.
- If `watermark == current` → no-op (no log).
- On success, set watermark to new value, no output.
- Fires windows: windows with `window_end <= watermark` become **closed** and their aggregates become queryable. Windows with end > watermark remain open (query returns NULL).
- Logged only when watermark actually increases.

**`COMPACT <timestamp>`**
In durable mode, rewrite log to minimal record set that reconstructs current state exactly via temp file + atomic rename. In-memory mode: no-op. No output.

### Query commands — one output line each

**`QUERY <window_id> <key> <window_start> <timestamp>`**
Query aggregated result for a specific window instance and key.
- If window_id does not exist (or its stream was deleted) → output `ERROR`.
- If key invalid name? Invalid name is invalid input → non-zero exit, not ERROR. Assume key validated.
- If `window_start` negative → invalid input → non-zero exit.
- If alignment invalid:
  - tumbling: `window_start % window_size != 0` → `ERROR`
  - sliding: `window_start % slide != 0` → `ERROR`
- Else if stream's watermark < window_start+size (i.e., window not yet closed) → output `NULL` (not ready).
- Else (closed): look up aggregate for key and window_start. If no events for that key in that window → `NULL`. Else output aggregation result as decimal string:
  - `SUM` → sum
  - `COUNT` → count
  - `MIN` → min
  - `MAX` → max
  - `AVG` → sum / count truncated toward zero (integer).
- `NULL` and `ERROR` are uppercase.

**`LIST_STREAMS <timestamp>`**
Sorted (lexicographic) comma-separated stream names, or `NONE` if none.

**`LIST_WINDOWS <timestamp>`**
Sorted comma-separated window IDs (all windows across streams), or `NONE`.

Additionally, to support per-stream window listing for tests, we support optional filtered variant:

**`LIST_WINDOWS <stream> <timestamp>`**
If two tokens after command (stream + ts) and first token is a valid stream name that exists, treat as filtered list: list windows belonging to that stream sorted, or `NONE`. If stream name does not exist → `ERROR`. This overload is distinguished by arity: 2 tokens (ts only) = global list, 3 tokens (stream + ts) = filtered. Implemented as same command name with variable arity (both allowed).

Note: The tests use both variants; ensure you handle both.

---

## Output Format

- For each command that produces output (`INGEST`, `QUERY`, `LIST_*`) write exactly one line in input order.
- `INGEST` → `OK`, `LATE`, or `ERROR`.
- `QUERY` → result string, `NULL`, or `ERROR`.
- `LIST_*` → `NONE`, comma list, or `ERROR`.
- No extra spaces. Flush and exit 0 on valid input.
- On invalid input, exit non-zero.

---

## Durable Persistence

Only when `STREAM_STATE_DIR` is set.

**Log format** — `stream.log`:
Sequence of records, each:
```
uint32 little-endian payload_len
uint32 little-endian crc32 IEEE of payload
payload_len bytes UTF-8 payload
```
Payload is command text exactly as logged, e.g., `CREATE_STREAM orders 0`, `INGEST orders mykey 5 100 1`. A record is valid only if 8 header bytes plus payload_len bytes are present and CRC matches.

**What is logged, in order, only when it changes state:**
- `CREATE_STREAM` → only when creates new.
- `DELETE_STREAM` → only when deletes existing.
- `DEFINE_TUMBLING_WINDOW` / `DEFINE_SLIDING_WINDOW` → only on success (new window).
- `DELETE_WINDOW` → only when existed.
- `INGEST` → only when `OK` (not late, not error). Log original line as received.
- `ADVANCE_WATERMARK` → only when watermark increases.
Queries and `COMPACT` are never appended as payloads; `COMPACT` rewrites file.

**Startup recovery:** create directory if needed. Before reading stdin, replay `$STREAM_STATE_DIR/stream.log` record by record in order. Each record must reconstruct state exactly as originally processed, preserving aggregates (since aggregates are derived from ingested events and watermark). For replay of `INGEST`, do NOT emit `LATE` logic based on current watermark during replay? Actually watermark replay must interleave: if during original run watermark advanced to 10, then later ingested event with event_time 5 was considered LATE and not logged. So during replay, only non-late events exist in log, so they will all be considered not late if replayed before watermark advancement. To ensure late detection matches, replay must process records in log order exactly as originally processed, using same late-check logic: when replaying an `INGEST` record, if its event_time <= current watermark at replay time, it would be considered late and should be dropped (but this should not happen if log only contains non-late events and watermark records are in correct order). However for safety, during replay skip late check? But better to keep late check: if during replay an event appears late relative to watermark, drop it (should not happen for valid compacted logs unless log was compacted and watermark emitted after events – see compaction spec). For compacted logs, we deliberately emit all events before final watermark, so events with event_time <= final watermark would be considered not late during replay if watermark is still -1, then later watermark advancement closes windows. That matches intended compaction: all events before watermark are not late. So during replay, for simplicity, if log is from compaction, events should be before watermark, so they are not late. For normal logs, events are already ordered such that any event with event_time <= watermark at that time would not have been logged, so replay will also not encounter late.

Stop at first incomplete or corrupt record (truncated header/payload or CRC mismatch); discard it and all following bytes; truncate log to valid prefix so later appends are clean. Never fail startup due to torn tail. An empty log file recovers cleanly.

**Durability (best-effort):** Each append should be made durable before continuing (e.g., via file sync / fsync). This is a best-effort guideline — not strictly required for functional tests, but static check scans source for `Sync()` / `fsync` call.

**Compaction:** `COMPACT` writes new temp file `$STREAM_STATE_DIR/stream.log.tmp` containing minimal records that replay to same final state:

- For each stream sorted asc: `CREATE_STREAM <stream> 0`
- For each window sorted asc by window_id: respective DEFINE record with timestamp 0, preserving original stream, size, slide, agg.
  - `DEFINE_TUMBLING_WINDOW <id> <stream> <size> <agg> 0`
  - `DEFINE_SLIDING_WINDOW <id> <stream> <size> <slide> <agg> 0`
- For each stream sorted asc, each ingested event that was logged (i.e., not late) sorted by `(stream asc, event_time asc, key asc, value asc)` and then by original ingestion order as tie-breaker to be deterministic: `INGEST <stream> <key> <value> <event_time> 0`
  - Important: Must emit **all** events that contribute to final state, including those whose windows are already closed, because closed window aggregates are derived from them. So emit all logged events (excluding those for deleted streams/windows, since those streams/windows no longer exist). If a stream was deleted, its events should not be emitted.
- For each stream sorted asc where watermark != -1 (i.e., watermark exists): `ADVANCE_WATERMARK <stream> <watermark> 0`

Deterministic sorted order required. Then atomic rename over `stream.log`. Ignore any stray `.tmp` files on recovery.

---

## Functional Requirements Summary

1. Streams with per-stream watermark (`-1` initially). Advance monotonically.
2. Tumbling windows: `[floor(t/size)*size, floor(t/size)*size + size)`.
3. Sliding windows: all starts `k*slide` with `start in [t-size+1, t]` inclusive, `start>=0`.
4. Per-key aggregates SUM, COUNT, MIN, MAX, AVG (avg = sum/count trunc toward zero).
5. Ingest OK if event_time > watermark else LATE; ERROR if stream missing.
6. Watermark advancing closes windows whose end <= watermark; queries for open windows return NULL.
7. Query returns NULL if no data for key in closed window, ERROR if window missing or alignment invalid.
8. Deleting stream cascades windows; deleting window removes its aggregates.
9. List streams/windows sorted.
10. Durable mode survives restarts with crash-consistent recovery and atomic compaction preserving all aggregates via events + final watermark.
11. Deterministic output; no randomness.
12. Go stdlib only; invalid input (negative timestamp, malformed, invalid names) → non-zero exit; application errors → ERROR/LATE/NULL line.

---

## Examples

### Basic tumbling SUM

Input:
```
CREATE_STREAM orders 0
DEFINE_TUMBLING_WINDOW w1 orders 10 SUM 1
INGEST orders alice 5 2 2
INGEST orders alice 7 8 3
INGEST orders bob 3 12 4
ADVANCE_WATERMARK orders 10 5
QUERY w1 alice 0 6
QUERY w1 bob 0 7
QUERY w1 bob 10 8
LIST_STREAMS 9
LIST_WINDOWS 10
```

Explanation:
- Window size 10: window `[0,10)` contains event times 2 and 8 for alice, sum=12.
- bob event time 12 is in window `[10,20)` not yet closed (watermark 10, window end 20 >10) → query returns NULL.
- After watermark 10, window `[0,10)` closed.

Output:
```
OK
OK
OK
12
NULL
NULL
orders
w1
```

Note: bob query for start 0 → no data in `[0,10)` → NULL. bob query for start 10 → window not closed → NULL.

If we advance watermark to 20 and query again:
```
ADVANCE_WATERMARK orders 20 11
QUERY w1 bob 10 12
```
Output:
```
3
```

### Sliding window COUNT

Input:
```
CREATE_STREAM s 0
DEFINE_SLIDING_WINDOW win s 10 5 COUNT 1
INGEST s k 1 2 2
INGEST s k 1 7 3
INGEST s k 1 12 4
ADVANCE_WATERMARK s 15 5
QUERY win k 0 6
QUERY win k 5 7
QUERY win k 10 8
```

- size 10 slide 5: windows `[0,10)`, `[5,15)`, `[10,20)`.
- Event 2 → windows `[0,10)` only (since start 0: 0<=2<10, start -5 would be -5 invalid)
- Event 7 → windows `[0,10)` and `[5,15)`
- Event 12 → windows `[5,15)` and `[10,20)`
- Watermark 15: windows ending <=15 are `[0,10)` (end10) and `[5,15)` (end15) closed, `[10,20)` end20 not closed.

Output:
```
OK
OK
OK
2
2
NULL
```

### Late handling

Input:
```
CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST s k 10 5 2
ADVANCE_WATERMARK s 10 3
INGEST s k 20 5 4
INGEST s k 30 15 5
ADVANCE_WATERMARK s 20 6
QUERY w k 0 7
```

- First ingest time5 OK.
- Watermark to 10 closes `[0,10)`.
- Second ingest time5 <= watermark 10 → LATE.
- Third ingest time15 >10 OK, in window `[10,20)`.
- Watermark to 20 closes `[10,20)`.

Output:
```
OK
OK
LATE
OK
30
```

Wait first query? Let's trace: after watermark 10, window [0,10) sum=10. Second query after watermark 20, window [0,10) still 10. The example shows 30? Actually after all, query w k 0 returns 10, not 30. The example output above shows 30 incorrectly? Let's recalc: The command list: after third ingest, watermark 20, query w k 0 -> should be 10. Need adjust example.

Correct example output for last query would be 10. If we queried window start10, sum=30.

Implementation must handle these.

Build your implementation at `/app`. The test harness builds your binary and drives via stdin, and restarts with shared `STREAM_STATE_DIR` to verify durability.
