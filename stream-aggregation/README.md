# codimango/stream-aggregation

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check), a **Flink-like keyed stream aggregation engine** at `/app`. It supports multiple streams, event-time tumbling and sliding windows, per-key aggregations (SUM, COUNT, MIN, MAX, AVG), watermark-based triggering, late-event handling, and optional crash-consistent durable persistence with compaction.

Commands (space-separated, timestamps `>=0`, negative timestamp = invalid input → exit non-zero):
- **Stream lifecycle:** `CREATE_STREAM` (idempotent), `DELETE_STREAM` (cascades windows + aggregates + events)
- **Window lifecycle:** `DEFINE_TUMBLING_WINDOW <id> <stream> <size> <agg>` and `DEFINE_SLIDING_WINDOW <id> <stream> <size> <slide> <agg>` — both **retroactively** aggregate prior non-late events (required, verified by `test_define_includes_past_events`), `DELETE_WINDOW` (removes aggregates)
- **Ingestion:** `INGEST <stream> <key> <value> <event_time>` → `OK` if `event_time > watermark`, `LATE` if `<= watermark`, `ERROR` if stream missing; logged only on `OK`. Value range `[-1e12, 1e12]`, key/stream/window names `[A-Za-z0-9._-]` 1..255 (keys 1..128) not `.`/`..`.
- **Watermark:** `ADVANCE_WATERMARK <stream> <wm>` monotonic, no-op if equal, `ERROR` if decreasing or stream missing. Closes windows where `window_end <= watermark` (query returns `NULL` before close, result after).
- **Query:** `QUERY <window_id> <key> <window_start>` → result (decimal), `NULL` (not closed or no data), `ERROR` (window missing or alignment: tumbling `start%size!=0`, sliding `start%slide!=0`). AVG = `sum/count` trunc toward zero.
- **Listing:** `LIST_STREAMS` → sorted comma or `NONE`; `LIST_WINDOWS` global sorted, plus filtered overload `LIST_WINDOWS <stream>` → windows for stream sorted or `ERROR` if stream missing.
- **Maintenance:** `COMPACT` atomic minimal rewrite: `CREATE_STREAM` sorted, `DEFINE_*` sorted by window_id, `INGEST` all logged events sorted by `(stream, event_time, key, value)`, `ADVANCE_WATERMARK` final per stream where `wm!=-1`, timestamp 0, via `stream.log.tmp` + atomic rename.

Payloads: tokens no spaces, value int64, event_time >=0, size/slide >=1 <=1e9, agg in `{SUM,COUNT,MIN,MAX,AVG}`. Invalid input → exit non-zero; app errors → `ERROR`/`LATE`/`NULL`.

## What makes this hard

- **Event-time windowing:** Tumbling `floor(t/size)*size` and sliding with up to `ceil(size/slide)` windows per event, walking backwards `latest = floor(t/slide)*slide` while `start > t-size`. Must handle `size=1000, slide=1` → 1000 windows/event without explosion.
- **Retroactive DEFINE:** Window definition must immediately aggregate prior events (not just future), verified explicitly. Without it compaction that emits DEFINE before INGEST would diverge.
- **Watermark + late handling:** `INGEST` returns `LATE` and drops if `event_time <= watermark`; watermark monotonic; `ADVANCE_WATERMARK` only logs on increase, no-op on equal.
- **Durable log with CRC framing:** `stream.log` records `uint32 LE len + uint32 LE crc32 + payload`; recovery stops at first corrupt/truncated record, truncates torn tail, remains appendable. Must ignore stray `.tmp`.
- **Atomic minimal compaction:** Must produce deterministic sorted minimal set that replays to same final aggregates (closed windows preserved via final watermark after all events). No-op suppression (duplicate CREATE, same watermark) must not grow file.
- **Query alignment & NULL vs ERROR:** Distinguishing not-closed (`NULL`), no-data (`NULL`), misaligned (`ERROR`), missing window (`ERROR`).
- **Delete cascade:** `DELETE_STREAM` removes its windows and aggregates.
- **Stdlib-only + fsync best-effort:** Import check rejects dot-containing imports; durability via `Sync()`/`O_SYNC` scanned informationally, not gating.

## Test / Solution Details

- **43 tests** via `go build` black-box:
  * basic tumbling SUM, COUNT/MIN/MAX/AVG, sliding COUNT/SUM, late handling, watermark monotonic ERROR, query not-closed NULL, no-data NULL, alignment ERROR
  * delete stream cascade, delete window, list sorted (global + filtered), filtered ERROR, ingest/define/advance missing stream ERROR, min/max empty NULL, avg negative trunc, multiple keys/windows, retroactive DEFINE (`test_define_includes_past_events`), complex sliding with late
  * invalid input: unknown cmd, arity, bad ints, size 0, BADAGG, slide 0, negative timestamps, bad key chars (`/`) → non-zero exit; blank lines ignored; deterministic
  * durability: persist across restart (tumbling/sliding), late not logged, torn-tail truncated header, bad CRC tail, truncated then appendable, compact preserves state/sliding, stray tmp ignored, empty log clean, in-memory no persist
  * complex: long names 200+ chars, many events tumbling (20 events, watermark 20), sliding many windows per event (size10 slide1), compact minimal deterministic & smaller (allows extra live WAL records before, checks after compact exact minimal + size shrink), noop does not append, fuzz random 30 ingests + queries vs no crash
  * stdlib-only enforced, fsync best-effort informational (checks `Sync()`/`O_SYNC`/`O_DSYNC`/`fsync`, warns if missing, asserts main func exists — not gating)
  * Total 43 after removing vacuous placeholder test.

- **Reference solution:** Go with `Stream{watermark, events}`, `WindowDef`, `aggregates[windowID][key][windowStart]Agg{sum,count,min,max,has}`, `doDefineTumbling/Sliding` retroactively scanning `events`, `doIngest` updating aggregates per belonging windows, `ADVANCE_WATERMARK` monotonic, `COMPACT` sorted CREATE→DEFINE→sorted INGEST→final watermark, `Sync()` in append/compact, atomic rename.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR `/app`, `allow_internet=true`, pytest pre-installed via Dockerfile.

## Completion Rates (online validation)

- **Oracle (reference):** 1/1 Mean 1.000 — validated (jobs/2026-07-23__10-33-18 and 2026-07-23__11-10-37)
- Frontier models not yet run on this task version; expected hard due to retroactive DEFINE + sliding window enumeration + late handling + compaction deterministic.

## Failure Analysis

- Oracle passes all 43 tests including `test_define_includes_past_events` which catches missing retroactive aggregation.
- Fuzz test `test_fuzz_random` catches crashes on random interleaving of 30 ingests and queries.
- Torn-tail tests verify crash-consistency: log truncation to valid prefix and still appendable.
- No-op test verifies duplicate CREATE and same watermark do not grow WAL — requires no-op suppression logic.

## Anti-Cheating Analysis

- No hardcoded outputs: random fuzz + arbitrary keys/values + watermark interleavings.
- Binary driven as subprocess, state_dir isolated per test via tmp_path, file size checked for no-op and compact smaller.
- CRC framing verified via bad-CRC injection, torn-tail via truncated header.
- Sorted order enforced in LIST_* and compaction minimal deterministic.
- Import check rejects third-party packages (dot in import path).
- Compaction checks exact minimal sequence after compact, not just size shrink.
