# codimango/stream-aggregation

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check), a **Flink-like keyed stream aggregation engine** at `/app` — **extremely hard mode** with 4 window types (tumbling, sliding, **session**, **cumulative**), 7 aggregations (`SUM COUNT MIN MAX AVG COUNT_DISTINCT TOP_K`), **atomic batch ingestion**, **allowed lateness**, and **purge TTL**.

Commands (timestamps `>=0`, negative → invalid input):
- **Stream lifecycle:** `CREATE_STREAM` (idempotent), `DELETE_STREAM` (cascades windows + aggregates + session state + events + lateness + purge)
- **Window lifecycle:**
  - `DEFINE_TUMBLING_WINDOW <id> <stream> <size> <agg>`: `[floor(t/size)*size,...+size)`
  - `DEFINE_SLIDING_WINDOW <id> <stream> <size> <slide> <agg>`: starts every `slide`, event belongs to all `start<=t<start+size`
  - `DEFINE_SESSION_WINDOW <id> <stream> <gap> <agg>`: per-key sorted events, group `diff<=gap`, session `[first, last+gap)`, out-of-order rebuild merges/splits
  - `DEFINE_CUMULATIVE_WINDOW <id> <stream> <max_size> <slide> <agg>`: windows `[0,slide)`, `[0,2*slide)`,...`[0,max_size)`, must have `max_size%slide==0` else invalid input, event `t` belongs to all ends `>t`
  - All retroactively aggregate prior non-late events (required, `test_define_includes_past_events`). `DELETE_WINDOW` removes aggregates + session ends.
- **Ingestion:** `INGEST <stream> <key> <value> <et>` → `OK` if not late, `LATE` if late, `ERROR` if stream missing. Late rule with allowed lateness: if stream has any tumbling/sliding/cumulative window, late if `et <= wm - allowed_lateness` (default L=0 → `et<=wm`); if only session windows or no windows, strict `et<=wm`. No watermark → never late.
- **Atomic batch:** `INGEST_BATCH <stream> <count> <k1> <v1> <et1> ... <ts>`: tokens `4+3*count`, count 1..100, each triple validated, arity mismatch → invalid input. If stream missing → `ERROR` none applied; if any late (same rule) → whole batch `LATE` none applied; else single `OK` and logs as individual `INGEST` records same ts.
- **Watermark:** `ADVANCE_WATERMARK <stream> <wm>` monotonic, no-op if equal, `ERROR` if decreasing/missing, closes windows `end<=wm`, only logs on increase.
- **Allowed lateness:** `SET_ALLOWED_LATENESS <stream> <L>`: L 0..1e9, default 0, no-op if same, `ERROR` if missing, logged only on change, persisted, emitted in compaction if non-zero. Allows late events within grace `wm-L < et <= wm` to update already-closed tumble/slide/cumulative windows.
- **Purge TTL:** `PURGE <stream> <up_to>`: deletes events `et<up_to`, deletes tumble/slide/cumulative aggregates `end<=up_to`, rebuilds session windows from remaining events, no-op if nothing, logged only when deletes, does NOT advance watermark.
- **Query:** `QUERY <window_id> <key> <window_start>` → result, `NULL` (not closed / no data / no session), `ERROR` (missing window or alignment: tumble `start%size!=0`, slide `start%slide!=0`, cumulative `end%slide!=0` or `end>max_size` or `<=0`). Session has no alignment ERROR. For cumulative, `window_start` param is actually `window_end`. Aggs: `SUM`, `COUNT`, `COUNT_DISTINCT` (distinct count), `MIN`, `MAX`, `AVG` trunc toward zero, `TOP_K` e.g., `TOP_2` → top K descending comma-separated e.g., `10,7`.
- **Listing:** `LIST_STREAMS` sorted/`NONE`; `LIST_WINDOWS` global sorted/`NONE`; filtered `LIST_WINDOWS <stream>` → windows for stream sorted/`NONE`/`ERROR`.
- **Maintenance:** `COMPACT` atomic minimal deterministic: `CREATE_STREAM` sorted, `SET_ALLOWED_LATENESS` non-zero sorted, `DEFINE_*` sorted by id (including session/cumulative), `INGEST` all remaining events sorted `(et,key,value)`, `ADVANCE_WATERMARK` final per stream where `wm!=-1`, ts 0, tmp+rename, ignore stray `.tmp`.

Validation: names `[A-Za-z0-9._-]` not `.`/`..`: stream/window 1..255, key 1..128, value `[-1e12,1e12]`, size/slide/gap/maxSize 1..1e9 (lateness 0..1e9, upTo >=0), batch count 1..100, topK 1..100, cumulative requires `maxSize%slide==0` else invalid input, ts/wm/upTo >=0. Invalid → non-zero exit; app errors → `ERROR`/`LATE`/`NULL`.

## What makes this extremely hard (vs easy)

- **4 window types:** Tumbling floor, sliding backward walk `latest=floor(t/slide)*slide` while `start>t-size`, session gap merging `diff<=gap` with full rebuild on out-of-order, cumulative `[0,slide)`, `[0,2*slide)`... where event belongs to all ends `>t` (first end `(floor(t/slide)+1)*slide`). Must handle 100 windows/event perf.
- **Retroactive DEFINE:** Window definition must scan prior events (or rebuild sessions) including after purge? Purge deletes events, so retroactive after purge uses remaining. Without retroactive, compaction (DEFINE before INGEST) diverges. Tested via `test_define_includes_past_events` and session retroactive.
- **COUNT_DISTINCT:** Requires per-window per-key set `map[int64]int`, not just count.
- **TOP_K:** `TOP_2` etc returns top K descending comma-separated, must maintain values slice per window per key, sort for query. Easy to return ascending or include spaces.
- **INGEST_BATCH atomic:** All-or-nothing, late check any late → whole `LATE`, must not partially apply aggregates or session state. Many agents apply first events then hit late.
- **Allowed lateness:** Shifts late boundary `et <= wm - L` for tumble/slide/cumulative (session-only strict). Late allowed events must update already-closed windows (closed stays closed but aggregate updates). Inclusive/exclusive off-by-one (`<=` vs `<`) catches many: with L=0 equal is LATE, with L=5, et=5 with wm=10 (W-L=5) is LATE (since <=), et=6 allowed.
- **Purge TTL:** Deletes events `<up_to` and aggregates `end<=up_to`, rebuilds sessions from remaining events. After purge watermark still high, new ingest with `et < wm` is still LATE (test_purge_then_ingest). Purge that deletes nothing must not grow WAL.
- **Durable WAL:** CRC framing LE len+crc, torn-tail truncation, remain appendable, stray tmp ignored, empty log clean, no-op suppression (duplicate CREATE, same WM, same lateness, purge no-op), compact strictly smaller and deterministic sorted minimal including SET_ALLOWED_LATENESS.
- **Query NULL vs ERROR:** Not-closed vs no-data vs no-session vs misaligned (tumble/slide/cumulative) vs missing window. Session has no alignment ERROR, cumulative alignment on end.
- **Delete cascade:** Must clear `sessionEvents` and `sessionEnds` and `allowedLateness`.

## Test / Solution Details

- **85 tests** via `go build` black-box (hardened after review):
  * 43 easy: basic tumble SUM, COUNT/MIN/MAX/AVG, sliding COUNT/SUM, late, wm monotonic ERROR, not-closed NULL, no-data NULL, alignment ERROR, delete cascade, delete window, list sorted/filtered, missing stream errors, min/max empty, avg negative trunc, multiple keys/windows, retroactive DEFINE, complex sliding with late, invalid input (unknown cmd, arity, bad ints, size0, BADAGG, slide0, negative ts, bad key), blank lines, deterministic, stdlib-only, fsync now requires Sync + WAL framing (LittleEndian/crc32/stream.log) + main func
  * durability: persist restart tumble/slide, late not logged, torn-tail header, bad CRC, truncated then appendable, compact preserves tumble/slide, stray tmp, empty log, in-memory no persist, noop suppression (duplicate CREATE, same WM, same lateness, purge no-op), compact minimal deterministic & smaller, plus **compact sorted-order byte-asserted** (CREATE sorted, SET_ALLOWED_LATENESS sorted, DEFINE sorted by id, INGEST sorted by et/key/value, WM sorted, category order CREATE→LATENESS→DEFINE→INGEST→WM)
  * complex: long names 200 chars, many events tumble 20 sum45/145, sliding many windows per event slide1 (100 windows/event perf), batch 100 boundary k0 sum950, fuzz random 30 events
  * hard (19): session basic `[0,15)30` & `[20,30)5`, gap merge, out-of-order merge gap15 events 0,30+12, retroactive session, late session (start 5 not 0), mixed tumble/slide/session, COUNT_DISTINCT tumble/slide/session, batch basic, late atomic, error missing, distinct via batch, session merge via batch, persist batch, session compact preserves, large sliding perf, session not exist NULL
  * extremely hard (23): allowed lateness basic (OK 10, OK 30, LATE 30 with boundary <=W-L), batch lateness atomic, persist lateness, sliding lateness (et=5 <=5 LATE), purge basic/session/then ingest/persist/compact preserves lateness+purge, invalid lateness/purge/cumulative not multiple/top 0/101, cumulative basic `[0,10)10 [0,20)30 [0,30)60` and out-of-order and batch and purge, TOP_K tumble `10,7`, sliding `20,10,5`/`20,5`, session `8,5`, batch distinct/top_k, fewer-than-K `7,3` and duplicates `10,5,5`, delete stream cascade lateness reset (L=5 allows 6→30 then after delete L reset to 0 → LATE NULL), delete window removes session ends (redefine after delete returns 30 immediately), plus **compact sorted-order deterministic** and **lateness cascade** tests added for review

- **Reference solution:** `Stream{watermark, allowedLateness, events}`, `sessionEvents[stream][key][]Event` sorted, `WindowDef` typ Tumble/Slide/Session/Cumulative + agg + topK, `aggregates[windowID][key][start]Agg{sum,count,min,max,distinct map,values slice,has}`, `sessionEnds[windowID][key][start]end`. `doDefineTumbling/Sliding/Cumulative` retroactive scanning `events` with late check `et <= wm - allowedLateness`, `doDefineSession` rebuilds via `rebuildSessionsForKey`. `doIngest` late check via `isLateForIngest` (hasNonSession decides allowed lateness), updates tumble/slide/cumulative incremental, inserts sorted sessionEvents, rebuilds session windows for key. `doIngestBatch` atomic any late → LATE, else bulk. `rebuildSessionsForKey` clears old, scans sorted events grouping `diff<=gap`, flush creates Agg and end=`last+gap`. `doSetAllowedLateness`, `doPurge` filters events `<upTo`, deletes aggregates `end<=upTo`, rebuilds sessions. `COMPACT` emits sorted CREATE, SET_ALLOWED_LATENESS non-zero, DEFINE_*, sorted INGEST, final WM, temp+rename, Sync.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true`, pytest pre-installed, `tests/test.sh` captures STATUS directly (fixed prior high bug).

## Completion Rates (online validation — commit d2a3fda, 85 tests, 2026-07-23)

- Oracle: **3/3** — validated
- Opus 4.8 (agent): **3/5** — validated
- GPT-5.5 (codex): **5/5** — failed (solved every trial → too easy for this model)
- Avocado (metacode): **2/5** — validated
- avgReward **0.92**, validation passing — balanced: Oracle solves it, Opus and Avocado land in the 1-4/5 band on genuine failures.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. This run had **real completed test failures** (not infra), and they converge on a single discriminator.

- **`test_cumulative_purge` — the sole discriminator (4 of 4 real failures, both Opus and Avocado).** Each failing trial failed *only* this test (84/85). It checks cumulative-window `PURGE` semantics: after `PURGE s 20` (purge events with timestamp ≤ 20), subsequent `QUERY cum k 10` and `QUERY cum k 20` must return `NULL`, and `QUERY cum k 30` → `30`. The failing models returned the stale cumulative value (`10`) at index 6 instead of `NULL` — i.e. they don't rebuild/invalidate cumulative window state after a purge. A legitimate behavioral correctness check with a clear expected output (not a brittle grep or undocumented convention).

- **Opus 4.8 (agent) — 3/5.** 3 clean passes; 2 genuine failures, both `test_cumulative_purge` only.
- **GPT-5.5 (codex) — 5/5.** All clean passes; too easy for this model.
- **Avocado (metacode) — 2/5.** 2 clean passes; 2 genuine failures (`test_cumulative_purge`) + 1 `status=error` infra.
- **Oracle — 3/3.** Reference solution passes.

**Assessment:** valid, well-calibrated task. Oracle solves it, the failures are real (not infra) and land on a genuine correctness edge (cumulative PURGE must invalidate purged data → NULL), and Opus/Avocado sit in the 1-4/5 band. Only caveat: GPT-5.5 is 5/5 (too easy for that single model); purge/rebuild-style edges are the lever if you want it to bite the strongest models too.

## Anti-Cheating

- No hardcoded outputs: fuzz random 30 events + batch 100 + session gap merges + cumulative + topK + allowed lateness boundary + purge TTL + 200-char names.
- WAL: CRC framing, torn-tail truncation, size checks for no-op and compact smaller, sorted deterministic compaction including SET_ALLOWED_LATENESS.
- LIST sorted, filtered ERROR, alignment ERROR vs NULL for session, cumulative ERROR on non-multiple slide.
- Import check stdlib-only, fsync best-effort with real asserts.
- Session query requires exact session start, TOP_K requires exact descending order.
