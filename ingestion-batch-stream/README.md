# codimango/ingestion-batch-stream

## Description
Multi-turn ingestion batch → streaming freshness – Extra Hard v4.

**Step1 (Batch v4):** Implements `/app/batch_ingest.py` with strict regex `^events_[A-Za-z0-9_.\-]+\.jsonl$` ignoring tmp/part/gz/hidden/.. and `events_.jsonl`. Computes SHA256+size per file, line-by-line, validates 7 reasons (invalid_json, invalid_fields, invalid_type, negative_amount, outlier_amount>100k, invalid_time, future_event>now+1h), supports Z, +00:00, +/-HH:MM, fractional. Dedup latest event_time wins, last-seen on equal. Populates 8 tables atomically WAL BEGIN IMMEDIATE: `events`, `daily_sales` by date, `ingestion_manifest` (hash, size, counts), `user_stats` (first/last seen, purchases, amount, views, carts, avg), `daily_top_users` top3 per date, `hourly_sales` per hour `YYYY-MM-DDTHH:00:00Z`, `sessions` sessionization per user gap >30min with session_id user+start, duration, counts, total_amount, `fraud_alerts` sliding 1h window >5 purchases per user (two-pointer, dedup per window_start). Writes versioned checkpoint v2 atomic tmp→rename with files dict hash/size/mtime and archive list, sorted dead-letter by file+line, archives files to `/app/data/archive/`, metrics 14 fields incl sessions/fraud.

Naive fails: glob includes tmp, no hash, no 8 tables, no future/outlier, no archiving, no top3/hourly/sessions/fraud logic (session gap calc and sliding window >5 are classic LLM miss), no atomic checkpoint, dead-letter unsorted, tz +00:00 not normalized.

**Step2 (Streaming):** `/app/stream_ingest.py` backfill respecting same regex, poll 200ms byte offsets atomic, partial line hold-back, immediate upsert checking existing DB timestamp, recompute all 8 aggregates, PID/log, SIGTERM, --once. SLA <2s via daemon + fresh file + DB query.

Tests context-following (reuse 8-table schema) and context-overriding (batch hourly → streaming continuous).

## Completion Rates

### Latest online validation (commit `c0254c3`)

**Status: PASSING** (structural 10/10, oracle 3/3, contamination MEDIUM, provenance clean). Full multi-turn pass rates per agent (all stages completed):

| Stage | Agent / Model | Full multi-turn | Turn 1 | Turn 2 | Mean |
|-------|---------------|-----------------|--------|--------|------|
| Oracle | oracle | 3/3 (100%) | 3/3 | 3/3 | 1.00 |
| Codex | gpt-5.5 | **0/10 (0%)** | 0/10 | – | 0.00 |
| Metacode | meta/avocado-5.14-code | 6/10 (60%) | 8/10 | 6/8 | 0.70 |
| Agent | claude-code / claude-opus-4-8 | 8/10 (80%) | 10/10 | 8/10 | 0.90 |

Turn 2 only runs after a Turn-1 pass, so Turn-2 denominators equal Turn-1 passes.

Calibration read: **polarized**. Oracle 100% and Opus 80% (Turn 1 flawless 10/10) confirm the task is solvable; avocado at 60% is on the easy edge for the weighted calibration model; codex is artificially stuck at 0% by a single under-specified field-name (see below), not by genuine difficulty.

### Failure analysis (from trial ctrf verifier output)

| Model | Where it fails | # tests | Root cause |
|-------|----------------|---------|------------|
| Codex (Turn 1, **10/10 trials**) | `test_basic_ingestion_hard_v4` | 1/7 | Dead-letter record used field key **`file_name`** but the test sorts by **`file`** (`KeyError: 'file'`). Spec says "sorted **file** ASC line_no ASC" but the `ingestion_manifest` PK is `file_name`, priming the wrong key. |
| Avocado (Turn 1, ~2/8) | `test_basic_ingestion_hard_v4` | 1/7 | Same `file`/`file_name` dead-letter key issue (intermittent). |
| Avocado (Turn 2) | `test_streaming_freshness_sla` | 1/6 | `last_event_delay_ms` computed against a stale/historical baseline (~68e9 ms) instead of the freshly-streamed event → fails the `<5000` freshness SLA. |

Key observations:
- **Codex is one test short in every trial (0/10).** All 10 fail *only* `test_basic_ingestion_hard_v4`, and always on the same line: the dead-letter schema key. This is a single, universal Turn-1 gate.
- ⚠️ **Fairness note:** that discriminator is a schema key-name (`file` vs `file_name`) that the spec only implies via the phrase "sorted file ASC", while the explicit `ingestion_manifest` schema uses `file_name`. Codex reasonably reused `file_name` and is blocked on all 10 trials. Consider either (a) adding an explicit dead-letter record schema (`{file, line_no, raw_line, reason}`) to the instruction, or (b) accepting both keys in the test — otherwise this reads as an under-specification trap rather than a difficulty signal.
- **Avocado (final 6/10):** clears Turn 1 8/10 and Turn 2 6/8, losing to the same dead-letter key (2 Turn-1 fails) and the streaming freshness SLA (Turn-2 fails) — genuine hard requirements, but 60% overall is on the easy edge for the weighted model.
- **Opus (final 8/10):** Turn 1 flawless (10/10); its only 2 losses are on Turn 2 (streaming freshness SLA), confirming the streaming step is the real strong-model discriminator once the Turn-1 dead-letter key is handled.

Cascade: Step1 hard filter (sessionization 30min gap, fraud sliding window two-pointer >5, plus the dead-letter key), Step2 streaming <5s freshness SLA.

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
