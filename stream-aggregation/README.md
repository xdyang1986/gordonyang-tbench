# codimango/stream-aggregation

## Task Overview

Build, from scratch in Go (stdlib only), a **Flink-like keyed stream aggregation engine** at `/app` — **extremely hard mode** with tumbling, sliding, **session windows**, `COUNT_DISTINCT`, **atomic batch ingestion**, **allowed lateness**, and **purge (state TTL)**.

Commands (timestamps `>=0`, negative → invalid input):
- **Stream lifecycle:** `CREATE_STREAM` (idempotent), `DELETE_STREAM` (cascades windows + aggregates + per-key session state + events + lateness)
- **Window lifecycle:** `DEFINE_TUMBLING_WINDOW <id> <stream> <size> <agg>`, `DEFINE_SLIDING_WINDOW <id> <stream> <size> <slide> <agg>`, `DEFINE_SESSION_WINDOW <id> <stream> <gap> <agg>` — all **retroactively** aggregate prior non-late events (required, `test_define_includes_past_events`). Session: per-key sorted events, group `diff <= gap`, session `[first, last+gap)`, closes when `wm >= last+gap`, out-of-order rebuild merges/splits. `DELETE_WINDOW` removes aggregates + session ends.
- **Ingestion:** `INGEST <stream> <key> <value> <et>` → `OK` if not late, `LATE` if late, `ERROR` if stream missing. Late rule with allowed lateness: stream has `allowed_lateness` (default 0 via `SET_ALLOWED_LATENESS`). If stream has any tumbling/sliding window, late if `et <= wm - allowed_lateness`; if only session windows (or no windows), strict `et <= wm`. No watermark → never late. Logged only on `OK`.
- **Atomic batch:** `INGEST_BATCH <stream> <count> <k1> <v1> <et1> ... <ts>` – tokens `4+3*count`, count 1..100. If stream missing → `ERROR` none applied; if any event would be late (same rule) → whole batch `LATE` none applied; else single `OK` and logs as individual `INGEST` records (same batch ts).
- **Watermark:** `ADVANCE_WATERMARK <stream> <wm>` monotonic, no-op if equal, `ERROR` if decreasing/missing. Closes windows `end <= wm`. Only logs on increase.
- **Allowed lateness:** `SET_ALLOWED_LATENESS <stream> <lateness>` – `lateness` 0..1e9, default 0, no-op if same, `ERROR` if missing. Logged only on change, persisted, emitted in compaction if non-zero. Allows late events within grace to update already-closed tumbling/sliding windows.
- **Purge (TTL):** `PURGE <stream> <up_to>` – deletes events with `et < up_to`, deletes tumbling/sliding aggregates where `end <= up_to`, rebuilds session windows from remaining events (old sessions disappear). No-op if nothing deleted, logged only when deletes. Does NOT advance watermark.
- **Query:** `QUERY <window_id> <key> <window_start>` → result, `NULL` (not closed / no data / no session), `ERROR` (window missing or tumbling `start%size!=0`, sliding `start%slide!=0`). Session has no alignment ERROR – any `>=0` allowed, but if not a session start → `NULL`. Aggs: `SUM`, `COUNT`, `COUNT_DISTINCT` (distinct values), `MIN`, `MAX`, `AVG` trunc toward zero.
- **Listing:** `LIST_STREAMS` sorted/`NONE`; `LIST_WINDOWS` global sorted/`NONE`; filtered `LIST_WINDOWS <stream>` → windows for stream sorted/`NONE`/`ERROR`.
- **Maintenance:** `COMPACT` atomic minimal deterministic rewrite: `CREATE_STREAM` sorted, `SET_ALLOWED_LATENESS` non-zero sorted, `DEFINE_*` sorted by id, `INGEST` all remaining events sorted `(et,key,value)`, `ADVANCE_WATERMARK` final per stream where `wm!=-1`, ts 0, tmp+rename, ignore stray `.tmp`.

Names `[A-Za-z0-9._-]` not `.`/`..`: stream/window 1..255, key 1..128. Value `[-1e12,1e12]`, size/slide/gap/lateness 1..1e9 (lateness 0..1e9), batch count 1..100, ts/wm/up_to >=0. Invalid → non-zero exit; app errors → `ERROR`/`LATE`/`NULL`.

## What makes this extremely hard

- **Session windows:** Dynamic per-key, sorted event_time, gap merging `diff<=gap`, out-of-order insert forces full rebuild that can merge/split multiple sessions. Common bug: query start 0 vs actual 5, not rebuilding after O-O-O, or not handling `last+gap` end.
- **COUNT_DISTINCT:** Requires per-window per-key set (`map[int64]int`), not just count. Agents often implement as COUNT.
- **INGEST_BATCH atomic:** All-or-nothing, late if any event late (with allowed lateness), must not partially apply aggregates or session state. Many agents apply first events then hit late.
- **Allowed lateness:** Per-stream lateness shifts late boundary `et <= wm - L` for tumbling/sliding (session-only strict). Late allowed events must update already-closed windows (closed stays closed but aggregate updates). Easy to miss: using `<= wm` always, or `et < wm - L` inclusive error (equal should be late for L=0, but allowed for larger L boundary). Tests `test_allowed_lateness_basic` checks `OK 10, OK 30, LATE 30` with gap logic.
- **PURGE (TTL):** Deletes events `<up_to` and aggregates `end <= up_to`, rebuilds sessions from remaining events. Must handle case where watermark is still high and new ingest after purge with et < watermark is LATE, or where purge removes events that formed a session leaving only later session. Tests for purge basic, session, then ingest, persist, compact preserves after purge.
- **Retroactive DEFINE:** Must aggregate prior events including after purge? Our purge deletes events, so retroactive after purge uses remaining. Without retroactive, compaction (DEFINE before INGEST) diverges.
- **Sliding perf:** size100 slide1 with 100 events → 10k updates, must be efficient.
- **Durable WAL:** CRC framing LE len+crc, torn-tail truncation to valid prefix, remain appendable, stray tmp ignored, empty log clean, no-op suppression (duplicate CREATE, same watermark, same lateness, purge that deletes nothing not growing file), compact strictly smaller and deterministic sorted minimal.
- **Query NULL vs ERROR:** Not-closed vs no-data vs no-session vs misaligned vs missing.

## Test / Solution Details

- **72 tests** via `go build` black-box:
  * 43 easy: tumbling SUM, COUNT/MIN/MAX/AVG, sliding COUNT/SUM, late, watermark monotonic ERROR, not-closed NULL, no-data NULL, alignment ERROR, delete cascade, delete window, list sorted/filtered, missing stream errors, min/max empty, avg negative trunc, multiple keys/windows, retroactive DEFINE, complex sliding with late, invalid input (unknown cmd, arity, bad ints, size0, BADAGG, slide0, negative ts, bad key), blank lines, deterministic, stdlib-only, fsync informational
  * durability: persist restart tumble/slide, late not logged, torn-tail header, bad CRC, truncated then appendable, compact preserves, stray tmp, empty log, in-memory no persist, noop suppression, compact minimal deterministic & smaller
  * complex: long names 200 chars, many events tumbling 20 sum45/145, sliding many windows per event slide1, batch 100 boundary
  * **hard 19 (session/distinct/batch):** session basic `[0,15)30` & `[20,30)5`, gap merge, out-of-order merge (gap15 events 0,30 +12), retroactive session, late session (start 5 not 0), mixed tumble/slide/session, COUNT_DISTINCT tumble/slide/session, batch basic, late atomic, error missing, distinct via batch, session merge via batch, persist batch, session compact, large sliding perf, session not exist NULL
  * **extremely hard 10 (lateness/purge):** allowed lateness basic (OK 10, OK 30, LATE 30), batch lateness atomic, persist lateness, sliding lateness (et=5 <=5 late), purge basic (NULL 20), purge session (rebuild), purge then ingest (LATE after watermark), purge persist, compact preserves lateness+purge, invalid lateness/purge (negative → non-zero, missing → ERROR)

- **Reference solution:** `Stream{watermark, allowedLateness, events}`, `sessionEvents[stream][key][]Event` sorted, `WindowDef` typ Tumble/Slide/Session, `aggregates[windowID][key][start]Agg{sum,count,min,max,distinct map,has}`, `sessionEnds[windowID][key][start]end`. `doDefine*` retroactive via `events` or `rebuildSessionsForKey`. `doIngest` late check uses `hasNonSession` to decide `et <= wm - L` vs `et <= wm`. `doIngestBatch` atomic checks any late → LATE, else bulk. `rebuildSessionsForKey` clears old, scans sorted events grouping `diff<=gap`, flush creates Agg + end=`last+gap`. `doSetAllowedLateness` changes L, `doPurge` filters events `<up_to`, filters per-key sessionEvents, deletes tumble/slide aggregates `end<=up_to`, rebuilds session windows, logs only when changed. `COMPACT` emits sorted CREATE, SET_ALLOWED_LATENESS non-zero, DEFINE_*, sorted INGEST, final WM. `Sync()` in append/compact.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true`, pytest pre-installed, `tests/test.sh` captures STATUS directly (fixed high-severity bug of `$?` after if with `:`).

## Completion Rates

- **Oracle easy (43 tests):** 1/1 Mean 1.000
- **Oracle hard (62 tests):** 1/1 Mean 1.000
- **Oracle extremely hard (72 tests):** 1/1 Mean 1.000 (2026-07-23__12-47-22, 13-42-33)
- Expected hard for frontier: session out-of-order rebuild + COUNT_DISTINCT + batch atomic + allowed lateness boundary `<= wm - L` vs `<` + purge rebuild that must advance watermark in tests or get NULL/LATE.

## Failure Analysis

- Prior easy version: Opus 4.8 and GPT-5.5 5/5 (too easy), Avocado 2/5 failing fuzz GROUP lifecycle.
- After adding session/distinct/batch (62 tests): frontier still 5/5 likely, but at least 19 new discriminators (session start alignment, gap merge O-O-O, distinct set, batch atomic LATE).
- After adding allowed lateness + purge (72 tests): adds 10 more discriminators where naive `et <= wm` always fails, and purge where watermark still high causes LATE on new ingest (test_purge_then_ingest expects LATE vs OK confusion). Batch lateness atomic also tricky.

## Anti-Cheating

- No hardcoded outputs: random fuzz (30 events) + batch 100 + session gap merges + allowed lateness (wm - L boundary) + purge TTL + 200-char names.
- WAL: CRC framing, torn-tail truncation, size checks for no-op (duplicate CREATE, same WM, same lateness, purge no-op) and compact strictly smaller, sorted deterministic.
- LIST sorted, filtered ERROR, alignment ERROR vs NULL for session.
- Import check stdlib-only, fsync informational with real asserts (Go files>0, BIN exists, has main).
- Session query requires exact session start, cannot guess.
