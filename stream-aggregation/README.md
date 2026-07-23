# codimango/stream-aggregation

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check), a **Flink-like keyed stream aggregation engine** at `/app` — **hard mode** with tumbling, sliding, **session windows**, `COUNT_DISTINCT`, and **atomic batch ingestion**.

Commands (space-separated, timestamps `>=0`, negative timestamp = invalid input → exit non-zero):
- **Stream lifecycle:** `CREATE_STREAM` (idempotent), `DELETE_STREAM` (cascades windows + aggregates + per-key session state + events)
- **Window lifecycle:** `DEFINE_TUMBLING_WINDOW <id> <stream> <size> <agg>`, `DEFINE_SLIDING_WINDOW <id> <stream> <size> <slide> <agg>`, **hard:** `DEFINE_SESSION_WINDOW <id> <stream> <gap> <agg>` — all **retroactively** aggregate prior non-late events (required, verified by `test_define_includes_past_events`). `DELETE_WINDOW` removes aggregates + session ends.
  - Session: per-key sorted events, group when `diff <= gap`, session `[first, last+gap)`, closes when `watermark >= last+gap`. Out-of-order ingestion must rebuild sessions for that key from scratch merging/splitting.
- **Ingestion:** `INGEST <stream> <key> <value> <event_time>` → `OK` if `et>wm`, `LATE` if `<=wm`, `ERROR` if stream missing; logged only on `OK`. **Hard:** `INGEST_BATCH <stream> <count> <k1> <v1> <et1> ... <ts>` atomic: count 1..100, total tokens `4+3*count`, if any event would be LATE → whole batch `LATE` none applied, if stream missing → `ERROR`, else single `OK` and logs as individual `INGEST` records (one per event, same batch timestamp).
- **Watermark:** `ADVANCE_WATERMARK <stream> <wm>` monotonic, no-op if equal, `ERROR` if decreasing or missing. Closes windows where `end <= wm` (tumbling/sliding `start+size`, session `last+gap`). Only logs on increase.
- **Query:** `QUERY <window_id> <key> <window_start>` → result, `NULL` (not closed or no data or no session), `ERROR` (window missing or tumbling `start%size!=0`, sliding `start%slide!=0`). Session has **no alignment error** — any `>=0` allowed, but if not a session start → `NULL`. Aggregations: `SUM`, `COUNT`, `COUNT_DISTINCT` (distinct values), `MIN`, `MAX`, `AVG` trunc toward zero.
- **Listing:** `LIST_STREAMS` sorted/`NONE`; `LIST_WINDOWS` global sorted/`NONE`; filtered `LIST_WINDOWS <stream>` → windows for stream sorted/`NONE`/`ERROR`.
- **Maintenance:** `COMPACT` atomic minimal rewrite: `CREATE_STREAM` sorted, `DEFINE_*` (including session) sorted by id, `INGEST` all logged events sorted by `(event_time, key, value)`, `ADVANCE_WATERMARK` final per stream where `wm!=-1`, timestamp 0, via tmp + rename.

Payloads: tokens no spaces, values `[-1e12,1e12]`, event_time/wm/size/slide/gap/ts >=0, size/slide/gap 1..1e9, batch count 1..100, agg `{SUM,COUNT,MIN,MAX,AVG,COUNT_DISTINCT}`. Invalid input → non-zero exit; app errors → `ERROR`/`LATE`/`NULL`.

## What makes this hard (vs easy)

- **Session windows:** Dynamic per-key sessions, out-of-order rebuild, gap merging (`diff <= gap` same session), `end = last+gap`, retroactive DEFINE must build sessions from prior events. Common bug: query start 0 vs actual start 5, or not rebuilding after out-of-order insert.
- **COUNT_DISTINCT:** Requires per-window per-key distinct set (`map[int64]int`), not just count. Easy to implement COUNT incorrectly.
- **INGEST_BATCH atomic:** All-or-nothing, LATE if any event late, ERROR if stream missing, logs as individual INGEST. Must not partially apply. Many agents apply first events then hit late.
- **Event-time windowing:** Sliding up to `size/slide = 1e9` windows/event (tests use 100 slide1 with 100 events → 10k updates), tumbling floor, sliding backward walk `latest = floor(t/slide)*slide` while `start > t-size`.
- **Retroactive DEFINE:** Must scan prior `events` and for session rebuild all per-key sessions. Without it compaction that emits DEFINE before INGEST diverges. Tested explicitly.
- **Watermark + late:** `et <= wm` LATE (equal is late), monotonic, same wm no-op not logged.
- **Durable WAL:** `stream.log` `uint32 LE len + uint32 LE crc32 + payload`, torn-tail truncation to valid prefix, remain appendable, ignore stray `.tmp`, empty log clean, in-memory no persist, no-op suppression (duplicate CREATE, same WM not growing file), compact strictly smaller.
- **Query NULL vs ERROR:** Not-closed vs no-data vs misaligned vs missing window vs no-session.
- **Delete cascade + session state:** DELETE_STREAM must clear `sessionEvents` and `sessionEnds` as well.
- **Stdlib-only + fsync best-effort:** Import check rejects dot, scan for `Sync()`.

## Test / Solution Details

- **62 tests** via `go build`:
  * original 43 covering basic tumbling (SUM, COUNT/MIN/MAX/AVG), sliding COUNT/SUM, late, watermark monotonic ERROR, not-closed NULL, no-data NULL, alignment ERROR, delete cascade, delete window, list sorted/filtered, missing stream errors, min/max empty, avg negative trunc, multiple keys/windows, retroactive DEFINE, complex sliding with late, invalid input (unknown cmd, arity, bad ints, size0, BADAGG, slide0, negative ts, bad key), blank lines, deterministic
  * durability: persist restart (tumbling/sliding), late not logged, torn-tail header, bad CRC tail, truncated then appendable, compact preserves tumbling/sliding, stray tmp, empty log, in-memory no persist
  * complex: long names 200 chars, many events tumbling (20 events sum 45/145), sliding many windows per event (size10 slide1 → 100 windows/event perf), compact minimal deterministic & smaller, noop suppression, fuzz random 30 events + queries
  * **hard new (19 tests):**
    - session basic `[0,15) sum30` and `[20,30) sum5`, gap merge, out-of-order merge (gap15 events 0,30 plus 12 merges), retroactive session, late handling session (start 5 not 0), mixed window types same stream (tumbling 60, sliding 60/30, session 60)
    - COUNT_DISTINCT tumbling/sliding/session
    - INGEST_BATCH basic, late atomic (partial late → whole LATE), error missing stream, distinct via batch, session merge via batch, persist via batch, batch count 100 boundary (`k0` sum 950)
    - session compact preserves, large sliding perf (100 events size100 slide1 sum100)
    - query session not exist NULL
  * stdlib-only, fsync informational (now asserts Go files>0, BIN exists, has func main not just assert True)
  * No vacuous `pass` tests.

- **Reference solution:** Go with `Stream{watermark, events}`, `sessionEvents[stream][key][]Event` sorted, `WindowDef` typ Tumbling/Sliding/Session, `aggregates[windowID][key][start]Agg{sum,count,min,max,distinct:map,has}`, `sessionEnds[windowID][key][start]end`. `doDefineTumbling/Sliding` retroactive scanning `events`, `doDefineSession` calls `rebuildSessionsForKey` for all keys. `doIngest` updates tumbling/sliding incremental + inserts sorted + rebuilds session windows for that key. `doIngestBatch` checks any late → LATE, else bulk insert + rebuild affected keys. `rebuildSessionsForKey` clears old aggregates/ends for key, scans sorted events grouping `diff<=gap`, flush creates Agg and end=`last+gap`. `COMPACT` sorted CREATE, DEFINE (including session), sorted INGEST, final WM. `Sync()` in append/compact.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true`, pytest pre-installed.

## Completion Rates (online)

- **Oracle (reference) easy:** 1/1 Mean 1.000 (2026-07-23__10-33-18, 11-10-37) — 43 tests
- **Oracle (reference) hard (62 tests):** 1/1 Mean 1.000 (2026-07-23__12-46-16 and 2026-07-23__12-?? after hard) — validated, 62 passed locally
- Expected to be hard for frontier: session out-of-order rebuild + COUNT_DISTINCT set + batch atomic late + retroactive DEFINE + compaction deterministic

## Failure Analysis (easy version)

- Previously frontier Opus 4.8 and GPT-5.5 were 5/5 (too easy). Only Avocado failed 3/5 on `test_fuzz_random` LIST_GROUPS edge (group lifecycle from errored SEEK — not applicable here but similar group-lifecycle edge for streams/windows).
- Hard mode adds 19 discriminators: session start alignment (query 0 vs 5), gap merge logic, out-of-order merge, COUNT_DISTINCT set counting, batch atomicity — common agent bugs: not rebuilding sessions, counting distinct as count, applying partial batch on late, missing retroactive for session.

## Anti-Cheating Analysis

- No hardcoded outputs: fuzz random + arbitrary keys/values + watermark interleavings + session gap merges + batch 100.
- WAL verified via CRC framing, torn-tail truncation, size checks for no-op and compact smaller, sorted deterministic compaction sequence.
- Sorted LIST_* and filtered LIST_WINDOWS ERROR case.
- Import check stdlib-only, fsync best-effort with real asserts.
- Session query requires exact session start, not just any start — cannot guess.
