# codimango/ingestion-batch-stream

## Description
Multi-turn ingestion from batch to streaming for freshness SLA – Hard Mode v3.

**Step1 (Batch v3):** Implements `/app/batch_ingest.py` discovering via strict regex `^events_[A-Za-z0-9_.\-]+\.jsonl$` ignoring `*.tmp`, `*.part`, `*.gz`, hidden `.*`, `..`, and `events_.jsonl`. Computes SHA256 and file size per file, reads line-by-line, validates 7 reasons (`invalid_json`, `invalid_fields`, `invalid_type`, `negative_amount`, `outlier_amount>100k`, `invalid_time`, `future_event>now+1h`), supports `+00:00` timezone offsets and fractional seconds, dedup by latest `event_time` (last-seen on equal). Populates 6 tables atomically with WAL and `BEGIN IMMEDIATE`: `events`, `daily_sales` by date, `ingestion_manifest` (hash, size, counts including future/outlier), `user_stats` (first/last seen, purchases, amount, views, carts, avg), `daily_top_users` top 3 per date, `hourly_sales` per hour. Writes versioned `checkpoint.json` v2 atomically with `files{hash,size,counts,mtime}` and `archive` list, sorted dead-letter `/app/state/dead_letter.jsonl` by file+line, moves processed files to `/app/data/archive/`, and metrics with 12 fields including future/outlier and archived counts.

Naive fails: glob includes tmp, no hash, no manifest/user_stats/top_users/hourly, no future/outlier filtering, no archiving, no atomic checkpoint, dead-letter not sorted, timezone +00:00 not parsed, no transaction atomicity.

**Step2 (Streaming):** Evolves to `/app/stream_ingest.py` with backfill respecting same regex, poll 200ms tailing with byte offsets atomic, partial line hold-back, immediate upsert checking existing DB timestamp, recompute aggregates including top users/hourly, PID/log, SIGTERM handling, `--once` mode. Freshness SLA <2s verified by spawning daemon and creating fresh file then DB query within 3s.

Tests context-following (reuse schema) and context-overriding (batch hourly → streaming continuous).

## Completion Rates

| Model | Step | Pass Rate (of trials reaching) | Last Updated |
|---|---|---|---|
| Oracle | 1_step_one | 3/3 | 2026-07-31 v3 hard |
| Oracle | 2_step_two | 3/3 | 2026-07-31 v3 |
| Oracle Full | 1→2 | 3/3 Mean 1.0 | 2026-07-31 |
| meta/avocado_dvsc_tester | 1_step_one | Expected 1/5 to 3/5 after v3 (was 5/5 easy) | – |
| meta/avocado_dvsc_tester | 2_step_two | Expected 1/5 to 2/5 | – |
| claude-opus-4-8 | 1_step_one | Expected 2/5 after v3 – fails manifest hash, dead-letter reasons, archiving, top3, future/outlier | – |
| claude-opus-4-8 | 2_step_two | Expected 2/5 | – |
| claude-sonnet-4-6 | 1_step_one | Expected 1/5 after v3 – previously 10/10 easy – now must handle 6 tables, regex hidden/.., archiving, future/outlier, versioned checkpoint | – |
| claude-sonnet-4-6 | 2_step_two | Expected 1/5 | – |

Cascade verdict expected: GOOD – Step1 now hard filter, Step2 retains streaming difficulty.

## Model Analysis

- **Oracle 3/3:** Implements regex filtering excluding hidden/.., SHA256, file_size, mtime, line-by-line, transaction WAL BEGIN IMMEDIATE, 6 tables, top3 per date sorted by amount DESC user_id ASC, hourly truncated, dead-letter sorted file+line with 7 reasons, checkpoint v2 atomic tmp→rename with archive list, metrics 12 fields, archiving move.

- **Sonnet 4.6 expected failures v3:**
  - Misses `daily_top_users` top3 logic – implements daily_sales only → `test_top_users_and_hourly` fails
  - Misses `hourly_sales` hour truncation → fails
  - Forgets archiving move – files remain in incoming, checkpoint archive empty, metrics archived_files 0 → `test_basic_ingestion_hard_v3` fails on archive existence
  - Uses glob not regex – includes `.tmp/.part/.gz/.hidden/..` → manifest count wrong, ignored events leak
  - No future/outlier filtering – treats 2099 and 200k as valid → DB count 7 vs 5, dead-letter missing `future_event`/`outlier_amount`
  - No SHA256 verification – manifest hash mismatch
  - No dead-letter sorted – order wrong
  - No version 2 field – checkpoint version check fails
  - ~30% fail on missing tables, 20% regex, 15% archiving, 15% future/outlier, 10% hash, 10% top3/hourly

- **Opus expected:** Better at tables but still misses archiving atomic move, top3 tie-breaking, future threshold (now+1h), file_size in manifest.

- **Avocado expected:** Batch may pass but streaming often misses offset atomicity, partial line hold-back, and checking existing DB timestamp for dedup (older duplicate overwrites newer).

All failures are reasoning gaps, not infra.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Tests use dynamic IDs (`e1`, `future1`, `outlier1`, `v1`, `tz1`) and dynamic file names (`events_fresh_001.jsonl`, `events_negative_check.jsonl`) created at runtime; DB counts computed, hashes computed via `hashlib.sha256` of actual file content – cannot hardcode.

- **Overfitting to visible tests:** Step1 7 tests cover regex ignore (tmp/part/gz/hidden/..), hash validation, 7 dead-letter reasons sorted, archiving move, idempotency hash change, top3 and hourly. Step2 6 tests spawn real daemon with wall-clock file creation + sleep measuring SLA – cannot overfit static metrics.

- **Modifying test files:** Verifier isolated, `/tests` chmod 700, uvx env – agent cannot modify. Tests assert 6 tables existence and per-file hashes, not just exit code.

- **Bypassing intended path:** Requires real computational work: SHA256, regex, line-by-line, transaction with 6 tables, top3 per date logic, hourly truncation, atomic checkpoint temp→rename, file archiving move, sorted dead-letter, streaming poll 200ms tailing with partial line handling meeting <2s SLA – cannot be bypassed by echo/cat precomputed DB; archive existence and hash validation prevent cheating.

## Environment

- Base: `public.ecr.aws/docker/library/python:3.11-slim` ECR mirror
- Deps: `pyyaml==6.0.2`, `watchdog==6.0.0`, `pytest==8.4.1` pinned
- System: `sqlite3`
- Dirs: `/app/data/incoming`, `/app/data/archive`, `/app/state`, `/app/metrics`, `/app/logs`
- Tables: 6 (events, daily_sales, ingestion_manifest, user_stats, daily_top_users, hourly_sales)
- Dead-letter: `/app/state/dead_letter.jsonl` sorted
- Offsets: `/app/state/stream_offsets.json` atomic
- PID/Log: `/app/stream.pid`, `/app/logs/stream.log`
- Timeouts: verifier 600s, agent 1200s, build 600s, 2 CPU / 4096MB
- Tag: multi-turn, context-following, context-overriding, hard
