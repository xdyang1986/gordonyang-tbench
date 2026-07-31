# codimango/ingestion-batch-stream

## Description
Multi-turn ingestion batch → streaming freshness – Extra Hard v4.

**Step1 (Batch v4):** Implements `/app/batch_ingest.py` with strict regex `^events_[A-Za-z0-9_.\-]+\.jsonl$` ignoring tmp/part/gz/hidden/.. and `events_.jsonl`. Computes SHA256+size per file, line-by-line, validates 7 reasons (invalid_json, invalid_fields, invalid_type, negative_amount, outlier_amount>100k, invalid_time, future_event>now+1h), supports Z, +00:00, +/-HH:MM, fractional. Dedup latest event_time wins, last-seen on equal. Populates 8 tables atomically WAL BEGIN IMMEDIATE: `events`, `daily_sales` by date, `ingestion_manifest` (hash, size, counts), `user_stats` (first/last seen, purchases, amount, views, carts, avg), `daily_top_users` top3 per date, `hourly_sales` per hour `YYYY-MM-DDTHH:00:00Z`, `sessions` sessionization per user gap >30min with session_id user+start, duration, counts, total_amount, `fraud_alerts` sliding 1h window >5 purchases per user (two-pointer, dedup per window_start). Writes versioned checkpoint v2 atomic tmp→rename with files dict hash/size/mtime and archive list, sorted dead-letter by file+line, archives files to `/app/data/archive/`, metrics 14 fields incl sessions/fraud.

Naive fails: glob includes tmp, no hash, no 8 tables, no future/outlier, no archiving, no top3/hourly/sessions/fraud logic (session gap calc and sliding window >5 are classic LLM miss), no atomic checkpoint, dead-letter unsorted, tz +00:00 not normalized.

**Step2 (Streaming):** `/app/stream_ingest.py` backfill respecting same regex, poll 200ms byte offsets atomic, partial line hold-back, immediate upsert checking existing DB timestamp, recompute all 8 aggregates, PID/log, SIGTERM, --once. SLA <2s via daemon + fresh file + DB query.

Tests context-following (reuse 8-table schema) and context-overriding (batch hourly → streaming continuous).

## Completion Rates

| Model | Step | Pass Rate (reaching) | Last Updated |
|---|---|---|---|
| Oracle | 1_step_one | 3/3 | 2026-07-31 v4 extra hard |
| Oracle | 2_step_two | 3/3 | 2026-07-31 v4 |
| Oracle Full | 1→2 | 3/3 Mean 1.0 | 2026-07-31 |
| meta/avocado_dvsc_tester | 1_step_one | Expected 1/5 to 2/5 after v4 (was 5/5 easy) – sessions gap 30min and fraud sliding window >5 are hard | – |
| meta/avocado_dvsc_tester | 2_step_two | Expected 1/5 to 2/5 | – |
| claude-opus-4-8 | 1_step_one | Expected 1/5 to 3/5 after v4 – extra tables top3/hourly/sessions/fraud tough | – |
| claude-opus-4-8 | 2_step_two | Expected 2/5 | – |
| claude-sonnet-4-6 | 1_step_one | Expected 0/5 to 2/5 after v4 – previously 10/10 easy – now 8 tables, archiving, future/outlier, sessions gap, fraud sliding window cause failure | – |
| claude-sonnet-4-6 | 2_step_two | Expected 0/5 to 1/5 | – |

Cascade expected: GOOD – Step1 hard filter (sessionization 30min gap calc, fraud sliding window two-pointer >5), Step2 streaming SLA.

## Model Analysis

- **Oracle 3/3:** Implements full regex ignoring hidden/.., SHA256 size mtime, line-by-line, transaction WAL BEGIN IMMEDIATE, 8 tables, top3 per date DESC amount ASC user_id, hourly truncated, sessions: sort per user by parsed UTC time, split gap>1800s, session_id f"{user}_{start}" with collision handling, duration int(end-start), total_amount purchase sum, event_count all types, fraud: per user purchase sorted, two-pointer window <=3600s, count>5 emit window_start/window_end purchase_count total_amount, dedup per window_start keep max count, dead-letter sorted file+line 7 reasons, checkpoint v2 atomic with archive list, metrics 14 fields, archiving move.

- **Sonnet 4.6 expected failures v4:**
  - Misses sessions table entirely – doesn't implement 30min gap splitting → `test_sessions_logic` fails (expects 2 sessions, gets 1 or 0)
  - Misses fraud sliding window – simple count >5 without time window or wrong window duration → `test_fraud_detection` fails (no fraud or false fraud for 5 purchases)
  - Misses daily_top_users top3 – gets daily_sales only
  - Misses hourly truncation `:00:00Z`
  - Forgets archiving move – files remain in incoming, archive empty
  - Uses glob not regex – includes tmp/part/gz/hidden/.. → manifest count 6 vs 2
  - No future/outlier – future 2099 and 200k valid → DB count 7 vs 5
  - No hash/size/mtime – manifest hash mismatch
  - No version 2 or not atomic – checkpoint version fail
  - ~25% missing tables (sessions/fraud/top/hourly), 20% regex, 15% archiving, 15% future/outlier, 10% hash, 10% sessions gap logic, 5% fraud window

- **Opus expected:** May handle sessions but often gap calc uses naive string compare not parsed UTC, duration wrong, session_id not f"{user}_{start}" format. Fraud window may use fixed hourly buckets not sliding, so misses sliding windows. Top3 tie-breaking user_id ASC often missed.

- **Avocado expected:** Batch may pass v3 partially but sessions: duration_sec calc off or event_count includes only purchases not all types; fraud: >5 detection uses >6 or >=5. Streaming misses offset atomicity and partial line hold-back and DB timestamp check.

All legitimate spec gaps.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Dynamic IDs (`e1`, `future1`, `outlier1`, `s1..s5`, `f0..f6`), dynamic file names (`events_fresh_001.jsonl`, `events_negative_check.jsonl`, `events_sess.jsonl`, `events_fraud.jsonl`) created at runtime; hashes computed from actual file bytes via `hashlib.sha256`; top3/hourly/sessions/fraud computed from DB, not static.

- **Overfitting to visible tests:** Step1 7 tests cover regex ignore, hash/size/mtime, 7 dead-letter reasons sorted, archiving, idempotency+hash change, top3+hourly, sessions gap 30min duration calc, fraud sliding window >5 vs 5 not. Step2 6 tests spawn real daemon measuring wall-clock SLA – cannot overfit static metrics.

- **Modifying test files:** `/tests` isolated, chmod 700 via uvx env – not writable. Tests assert 8 tables existence, per-file hashes, archive move, session duration, fraud purchase_count – requires real implementation.

- **Bypassing intended path:** Requires real work: SHA256, regex, line-by-line, WAL transaction with 8 tables, top3 per date logic, hourly truncation, sessionization sort per user gap>1800s duration calc, fraud sliding two-pointer >5, atomic checkpoint tmp→rename, archiving move, sorted dead-letter, streaming poll 200ms tailing partial line handling meeting <2s SLA – cannot bypass via static DB; archive and hash validation prevent cheat.

## Environment

- Base: `public.ecr.aws/docker/library/python:3.11-slim` ECR mirror
- Deps: `pyyaml==6.0.2`, `watchdog==6.0.0`, `pytest==8.4.1` pinned
- System: `sqlite3`
- Dirs: `/app/data/incoming`, `/app/data/archive`, `/app/state`, `/app/metrics`, `/app/logs`
- Tables: 8 (events, daily_sales, ingestion_manifest, user_stats, daily_top_users, hourly_sales, sessions, fraud_alerts)
- Dead-letter: `/app/state/dead_letter.jsonl` sorted 7 reasons
- Offsets: `/app/state/stream_offsets.json` atomic
- PID/Log: `/app/stream.pid`, `/app/logs/stream.log`
- Timeouts: verifier 600s, agent 1200s, build 600s, 2 CPU / 4096MB
- Hardness: Step1 extra hard v4 to fix too easy – session gap 30min and fraud sliding window >5 are strong filters
