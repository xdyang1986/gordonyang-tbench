# Flink-like Stream Aggregation Engine — Hard Mode

Build a Flink-inspired keyed stream aggregation engine in Go at `/app`. It supports multiple streams, event-time windows (tumbling, sliding, **session**), per-key aggregations (including **COUNT_DISTINCT**), **atomic batch ingestion**, watermark-based triggering, late-event handling, and optional crash-consistent durable persistence with compaction.

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
- **Value**: signed integer `int64`, allowed range `[-1e12, 1e12]` (outside range is invalid input).
- **Event time, Watermark, Window size, Slide, Gap, Timestamp**: integer `>=0`. Negative → invalid input → non-zero exit.
- **Window size / Slide / Gap**: `>=1 && <= 1e9`.
- **Batch count**: `>=1 && <=100`.
- **Agg function**: one of `SUM COUNT MIN MAX AVG COUNT_DISTINCT` (uppercase only).
- **Window start** in query: integer `>=0`.

---

## Command Stream

Each non-blank line is one command, tokens space-separated. On **invalid input** (malformed, unknown command, wrong arity, non-integer where int expected, invalid name, value/size/slide/gap/count out of range, agg unknown, timestamp negative), exit non-zero. Application errors (stream/window missing, alignment error, watermark decreasing, late) produce `ERROR`/`LATE`/`NULL` and continue.

### State-changing — no output on success unless stated

**`CREATE_STREAM <stream> <timestamp>`**
Watermark `-1` initially. Idempotent. Logged only when creates.

**`DELETE_STREAM <stream> <timestamp>`**
Deletes stream + all its windows + aggregates + events + per-key session state. No-op if missing. Logged only when existed.

**`DEFINE_TUMBLING_WINDOW <window_id> <stream> <window_size> <agg_func> <timestamp>`**
- Assignment: `window_start = floor(event_time / size) * size`, `window_end = start + size`, `[start,end)`.
- **Retroactive (required):** Must immediately aggregate all prior non-late events for its stream. Verified by `test_define_includes_past_events`. Without it compaction that emits DEFINE before INGEST diverges.
- Error if stream missing → `ERROR`, if window_id exists → `ERROR`. Logged only on success.

**`DEFINE_SLIDING_WINDOW <window_id> <stream> <window_size> <slide> <agg_func> <timestamp>`**
- Starts every `slide`: `[0, slide, 2*slide...]`. Event `t` belongs to all where `start <= t < start+size` and `start%slide==0`. Iterate `latest = floor(t/slide)*slide` backwards while `start > t-size`.
- Retroactive same as tumbling. Error handling same. Logged only on success.

**`DEFINE_SESSION_WINDOW <window_id> <stream> <gap> <agg_func> <timestamp>`**
- **Session windows — hard:** Per-key dynamic sessions.
  - Per key, maintain events sorted by `event_time`. Build sessions by scanning sorted events: start session at first event, `curLast = event_time`. For next event `ev`, if `ev.event_time - curLast <= gap` → same session (extend `curLast = ev.event_time`, aggregate `ev`), else close previous session `[curStart, curLast+gap)` and start new `[ev.event_time, ev.event_time+gap)`.
  - Each session has `start = first event time`, `end = last event time + gap`, `[start,end)`. Gap = inactivity timeout.
  - Out-of-order ingestion must rebuild sessions for that key from scratch (merge/split possible). If gap=10, events at 0 and 25 → two sessions [0,10) and [25,35). Adding event at 12 → 0 alone, 12 alone, 25 alone (since 12-0=12>10). If gap=15, events 0,12,25 → one session [0,40) (0,12 same, 12,25 diff 13 <=15 same).
  - Watermark closes session when `watermark >= session_end`.
  - Retroactive: upon definition, build sessions from all prior non-late per-key events.
  - Query checks session existence, not alignment.
- Error if stream missing → `ERROR`, window_id exists → `ERROR`, gap invalid → invalid input. Logged only on success.

- Aggregators for all window types:
  - `SUM`, `COUNT` (ignores value), `MIN`, `MAX`, `AVG` (sum/count trunc toward zero), `COUNT_DISTINCT` (distinct values count).

**`DELETE_WINDOW <window_id> <timestamp>`**
Removes window + aggregates + session ends. No-op if missing. Logged only when existed.

**`INGEST <stream> <key> <value> <event_time> <timestamp>`**
- Missing stream → `ERROR`.
- `event_time <= watermark` → `LATE`, drop, not logged.
- Else update all windows on stream (tumbling/sliding incremental, session via rebuild for that key), append event to stream's raw log for compaction, output `OK`, log original line.

**`INGEST_BATCH <stream> <count> <key1> <value1> <event_time1> <key2> <value2> <event_time2> ... <timestamp>`**
- Atomic batch: `count` triples, then final processing timestamp. Total tokens `4+3*count`. `count` 1..100 else invalid input.
- Validation: each key valid, each value in range, each event_time >=0 else invalid input. Arity mismatch → invalid input.
- If stream missing → `ERROR`, none applied.
- If any event would be late (`event_time <= watermark`) → entire batch `LATE`, none applied, not logged.
- Else atomic success: apply all events in given order (for tumbling/sliding aggregates incremental, for session rebuild per affected key once after all insertions), append all to stream's raw events, output single `OK`, log as individual `INGEST <stream> <key> <value> <event_time> <timestamp>` records (one per event, same batch timestamp) for recovery (mirrors `PRODUCE_BATCH` → individual `PRODUCE` logging).
- Queries never logged.

**`ADVANCE_WATERMARK <stream> <watermark> <timestamp>`**
- Missing stream → `ERROR`. Decreasing (`watermark < current` when current !=-1) → `ERROR`. Equal → no-op no log.
- On increase, set watermark, no output, fires windows where `end <= watermark`. Logged only on increase.

**`COMPACT <timestamp>`**
Durable mode rewrites to minimal deterministic set via tmp + atomic rename. In-memory no-op.

### Query — one output line each

**`QUERY <window_id> <key> <window_start> <timestamp>`**
- Window missing → `ERROR`.
- Tumbling alignment: `window_start % size !=0` → `ERROR`; sliding: `window_start % slide !=0` → `ERROR`; session: no alignment error — any `>=0` allowed, but if no session with that start for key → `NULL`.
- Stream deleted (window should have been cascade-deleted) → `ERROR`.
- If watermark < window_end → `NULL` (not closed). For tumbling/sliding `window_end = start+size`, for session `window_end = session_end` (looked up, if no session → `NULL`).
- If closed but no events for key in that window/session → `NULL`.
- Else result:
  - `SUM` → sum
  - `COUNT` → count
  - `COUNT_DISTINCT` → distinct values count
  - `MIN`, `MAX` → min/max
  - `AVG` → sum/count trunc toward zero
- Uppercase `NULL`/`ERROR`.

**`LIST_STREAMS <timestamp>`** → sorted comma or `NONE`.
**`LIST_WINDOWS <timestamp>`** → global sorted or `NONE`.
**`LIST_WINDOWS <stream> <timestamp>`** → filtered for stream sorted or `NONE`, `ERROR` if stream missing (arity overload: 2 tokens global, 3 tokens filtered).

---

## Output Format

- One line per output-producing command in input order.
- `INGEST`/`INGEST_BATCH` → `OK`, `LATE`, `ERROR`.
- `QUERY` → result, `NULL`, `ERROR`.
- `LIST_*` → `NONE`, comma list, `ERROR`.
- No extra spaces, flush, exit 0.

---

## Durable Persistence

When `STREAM_STATE_DIR` set.

**Log format `stream.log`:** `uint32 LE len | uint32 LE crc32 IEEE(payload) | payload bytes UTF-8`. Valid only if 8 header + len present and CRC matches.

**What is logged, in order, only when state changes:**
- `CREATE_STREAM`, `DELETE_STREAM`, `DEFINE_TUMBLING_WINDOW`, `DEFINE_SLIDING_WINDOW`, `DEFINE_SESSION_WINDOW`, `DELETE_WINDOW`, `INGEST` (only `OK`), `ADVANCE_WATERMARK` (only increase). For `INGEST_BATCH` success, log as individual `INGEST` records with same batch timestamp. Queries/`COMPACT` never logged.

**Startup recovery:** create dir, replay `stream.log` in order reconstructing state exactly. For `INGEST`, apply same late-check (`event_time <= watermark` → drop). For normal logs late never appears because late not logged. For compacted logs all `INGEST` are before final `ADVANCE_WATERMARK`, so watermark=-1 during replay → no late, final watermark closes windows preserving closed aggregates. Stop at first incomplete/corrupt record, discard tail, truncate file to valid prefix. Empty log clean.

**Best-effort durability:** each append should `Sync()`/fsync. Informational test scans for `Sync()`.

**Compaction:** writes `$STREAM_STATE_DIR/stream.log.tmp` minimal deterministic:
- Streams sorted asc: `CREATE_STREAM <s> 0`
- Windows sorted asc by id: `DEFINE_TUMBLING_WINDOW <id> <stream> <size> <agg> 0`, `DEFINE_SLIDING_WINDOW <id> <stream> <size> <slide> <agg> 0`, `DEFINE_SESSION_WINDOW <id> <stream> <gap> <agg> 0`
- For each stream sorted, events sorted by `(event_time asc, key asc, value asc)`: `INGEST <stream> <key> <value> <event_time> 0` — emit **all** logged events (including those whose windows already closed) excluding deleted streams, to preserve closed aggregates.
- Watermarks sorted: `ADVANCE_WATERMARK <stream> <wm> 0` where `wm!=-1`
Atomic rename over `stream.log`, ignore stray `.tmp`.

---

## Functional Requirements Summary (hard)

1. Watermark -1 initially, monotonic advance.
2. Tumbling `[floor(t/size)*size,...+size)`, sliding `[start in [t-size+1,t], start%slide==0]`, session per-key `[first, last+gap)` with merge on `diff<=gap`, sorted event_time, out-of-order rebuild.
3. Retroactive DEFINE must aggregate prior non-late events (tumbling/sliding/session).
4. Per-key aggregates `SUM COUNT MIN MAX AVG COUNT_DISTINCT` (distinct via set).
5. `INGEST` OK if `et>wm` else LATE; atomic `INGEST_BATCH` (count 1..100) all late → LATE none applied, missing stream → ERROR, success → OK.
6. Watermark closes windows `end<=wm`, query open → NULL, no data → NULL, misalignment (tumbling/sliding) → ERROR.
7. Session query: no alignment error, start must equal session start else NULL, end=last+gap, closed when `wm>=end`.
8. Delete stream cascades windows+events+session state; delete window removes aggregates.
9. List sorted, filtered LIST_WINDOWS.
10. Durable WAL CRC framing, torn-tail truncation, atomic minimal compaction preserving all aggregates via events+final watermark, no-op suppression (duplicate CREATE, same watermark not logged).
11. Deterministic, stdlib only, invalid input → non-zero exit.

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
```

Output:
```
OK
OK
OK
12
NULL
NULL
```

### Sliding COUNT

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
QUERY w k 10 8
```

Output:
```
OK
LATE
OK
10
30
```

### Session window (gap=10)

Input:
```
CREATE_STREAM s 0
DEFINE_SESSION_WINDOW sess s 10 SUM 1
INGEST s k 10 0 2
INGEST s k 20 5 3
INGEST s k 5 20 4
ADVANCE_WATERMARK s 15 5
QUERY sess k 0 6
ADVANCE_WATERMARK s 30 7
QUERY sess k 0 8
QUERY sess k 20 9
```

Explanation:
- Events 0 and 5 diff 5 <=10 → same session [0, 5+10=15)
- Event 20 diff 15 >10 from last 5 → new session [20,30)
- Watermark 15 closes first session (end 15), second still open.
- Watermark 30 closes second.

Output:
```
OK
OK
OK
30
30
5
```

### COUNT_DISTINCT

Input:
```
CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 COUNT_DISTINCT 1
INGEST s k 5 1 2
INGEST s k 5 2 3
INGEST s k 7 3 4
ADVANCE_WATERMARK s 10 5
QUERY w k 0 6
```

Two distinct values 5,7 → count 2.

Output:
```
OK
OK
OK
2
```

### INGEST_BATCH atomic

Input:
```
CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST_BATCH s 2 k1 10 5 k2 20 6 2
ADVANCE_WATERMARK s 10 3
QUERY w k1 0 4
QUERY w k2 0 5
INGEST_BATCH s 2 k1 5 5 k1 10 15 6
```

Second batch contains event time 5 which is <= watermark 10 → LATE, whole batch dropped.

Output:
```
OK
10
20
LATE
```

Build at `/app`. Tests cover all including session merge/out-of-order, distinct, batch atomicity, durability.
