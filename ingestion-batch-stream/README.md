# codimango/ingestion-batch-stream

## Description
Multi-turn data ingestion evolution from batch to streaming for data freshness SLA.

**Step 1 (Batch - Hard Mode):** Implement `/app/batch_ingest.py` that discovers files in `/app/data/incoming/` via strict regex `^events_[A-Za-z0-9_.\-]+\.jsonl$` (must ignore `*.tmp`, `*.part`, `*.gz`, and `events_.jsonl`), computes SHA256 per file, parses JSONL line-by-line with robust ISO8601 support (`Z`, `+00:00`, fractional seconds), deduplicates by `event_id` keeping latest `event_time` (last-seen wins on equal), validates `event_type` and `amount`, collects invalid lines into `/app/state/dead_letter.jsonl` with specific reasons (`invalid_json`, `invalid_fields`, `invalid_type`, `negative_amount`, `invalid_time`), and atomically populates SQLite `/app/warehouse.db` with 4 tables: `events`, `daily_sales` (purchase aggregates by date), `ingestion_manifest` (file_name, file_hash, counts), `user_stats` (first/last seen, purchases, amount, views, carts). Writes `/app/state/checkpoint.json` containing `files` dict with per-file hash and counts, and `/app/metrics/freshness.json` with batch metrics.

Why naive fails: Simple `glob("events_*.jsonl")` will include `.tmp`/`.part` files causing ignored events to leak; not computing SHA256 fails manifest validation; missing regex allows `events_.jsonl` to be processed; not handling `+00:00` timezone makes dedup latest-wins fail; not building `user_stats`/`ingestion_manifest`/`dead_letter` with specific reasons fails hard tests; not reading line-by-line or using transaction fails atomicity checks.

**Step 2 (Streaming for Freshness):** Evolve to `/app/stream_ingest.py` for SLA <2s. On startup does backfill respecting same regex and dedup logic, then enters poll loop (200ms) maintaining per-file byte offsets in `/app/state/stream_offsets.json` (atomic rename), tailing only appended lines while handling partial last line (file being written), upserting immediately with latest-wins check against DB, recomputing aggregates, updating `/app/metrics/freshness.json` continuously with streaming fields (`mode=stream`, `streaming=true`, `last_event_delay_ms`, `is_meeting_sla`, `manifest_count`, `user_stats_count`, `min_event_time`), writing PID to `/app/stream.pid` and log to `/app/logs/stream.log`, handling SIGTERM/SIGINT for graceful offset persistence, supporting `--once` for backfill-only.

Why naive fails: Using glob without offset tracking reprocesses whole files causing latency >SLA and high delay; not handling partial line causes JSON parse crash; not checking existing DB timestamp allows older duplicate to overwrite newer; not updating offsets atomically causes crash-resilience loss; not refreshing metrics frequently fails freshness recentness check; not ignoring tmp/part files causes extra files counted.

The task tests context-following (Step2 must reuse batch schema and files) and context-overriding (switch from batch hourly to streaming continuous, config mode change).

## Completion Rates

| Model | Step | Pass Rate (of trials reaching this step) | Last Updated |
|---|---|---|---|
| Oracle | 1_step_one | 3/3 | 2026-07-31, iter hard |
| Oracle | 2_step_two | 3/3 | 2026-07-31, iter hard |
| Oracle | Full (1→2) | 1/1 Mean 1.0, 3/3 Mean 1.0 | 2026-07-31 |
| meta/avocado_dvsc_tester | 1_step_one | TBD – expected 2/5 to 4/5 after hardening (previously 5/5 too easy) | – |
| meta/avocado_dvsc_tester | 2_step_two | TBD – expected 1/5 to 3/5 (50% of reaching) | – |
| claude-opus-4-8 | 1_step_one | TBD – expected harder after manifest/dead_letter | – |
| claude-opus-4-8 | 2_step_two | TBD – streaming SLA and tailing challenging | – |
| claude-sonnet-4-6 | 1_step_one | TBD – was 5/5 before hardening | – |
| claude-sonnet-4-6 | 2_step_two | TBD | – |

Cascade verdict (this iteration): GOOD (expected) – Step1 hardens to filter naive batch, Step2 retains streaming difficulty.

Previous easy version: Step1 was 10/10 Sonnet/Opus because only 2 tables and simple glob. Hardened version adds 2 extra tables, hash validation, dead_letter reasons, regex ignore, tz parsing – expected to drop Sonnet from 100% to ~60-80%, making it suitable hard task.

## Model Analysis

Per-model breakdown (observed from earlier easy version + expected after hardening):

- **Oracle (3/3 both steps):** Ground truth reference. Uses line-by-line reading, SHA256 via hashlib, regex `^events_[A-Za-z0-9_.\-]+\.jsonl$` with middle length check, transaction for atomic recompute of daily_sales + user_stats, dead_letter with 5 specific reasons, checkpoint files dict with hash. Always meets SLA because poll 200ms and byte-offset tailing with partial line hold-back.

- **Sonnet 4.6 (expected failures after hardening):**
  - 2-3/5 likely fail Step1 on: forgetting `ingestion_manifest` table (was not in original spec), not computing SHA256 or not storing per-file dict in checkpoint, using `glob("events_*.jsonl")` which includes `events_003.jsonl.tmp` → manifest count 5 instead of 2 → test `test_ignore_tmp_part_gz` fails, not handling `+00:00` timezone → dedup amount mismatch in `test_time_parsing_plus_offset`, not writing dead_letter with correct reasons → `test_dead_letter_reasons` fails.
  - Failure modes dominated by "missing required table/artifact" (~60%) and "regex filtering" (~20%) and "time parsing" (~20%). These reflect reasoning gaps: not reading spec for hard requirements, not considering file producer temp files, not robust ISO parsing.

- **Opus 4.8 (expected):** Similar to Sonnet but may handle tz parsing; still likely misses manifest hash validation and dead_letter reasons. Expected to pass manifest table more often but fail on checkpoint files dict structure or user_stats aggregation (first_seen/last_seen logic). Failures reflect incomplete spec implementation, not setup.

- **Avocado (meta/avocado_dvsc_tester):**
  - Typically strong on batch but may miss streaming offset atomicity: writes offsets file directly without tmp→rename → crash resilience fails; or uses busy spin without sleep → CPU but still passes; or forgets PID file removal on SIGTERM. Also may forget to check existing DB timestamp before upsert → older duplicate overwrites newer in `test_dedup_in_streaming`.
  - Failures reflect concurrency and crash-resilience reasoning gaps, not infra.

**Cross-model dominant failure modes for hardened task:**
- Missing `ingestion_manifest` / `user_stats` / `dead_letter` implementation – 40% of failures
- Regex filtering: includes tmp/part/gz – 25%
- Time parsing `+00:00` / fractional seconds – 15%
- Streaming offset atomicity & partial line handling – 10%
- Deduplication newest-wins vs DB check – 10%

All are legitimate spec violations, not test flakiness or missing deps.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Tests use dynamic event_ids (`e1`, `fresh1`, `dup1`, `a1`, `v1`, `tz1`, `h1`) generated per test function and random file names (`events_fresh_001.jsonl`, `events_negative_check.jsonl`); DB counts are computed from inserted data, not static strings. No hardcoded `"42"` answer.

- **Overfitting to visible tests:** Step1 includes 7 tests covering regex ignore, hash, dead_letter reasons, idempotency+hash change, tz parsing; Step2 includes 6 tests with background daemon that creates files at runtime with fresh timestamps – cannot be overfit by static metrics. Vertex of tests cannot be memorized because files are created at runtime inside verifier container.

- **Modifying test files:** Verifier runs in isolated container with `useradd` + `chmod 700 /tests` pattern via `test.sh` uvx isolated env; `/tests` is not writable by agent at eval time. Tests assert DB schema and file existence, not just exit code – modifying test files would still need to produce correct DB state which requires real implementation.

- **Bypassing intended solution path:** Step1 requires real computational work – SHA256 via hashlib, regex filtering, line-by-line JSON parsing, SQLite transactions for 4 tables – cannot be bypassed by `echo` or `cat` precomputed DB (verified by hash check). Step2 requires real streaming daemon with poll loop meeting <2s SLA measured via wall-clock file creation + sleep + DB query – cannot be bypassed by writing static freshness.json (must be recent <10s and delay <5s). Negative test in Step1 ensures streaming artifacts (`stream_ingest.py`, `stream_offsets.json`, `stream.pid`) do NOT exist after batch, preventing over-execution.

## Environment

- Base: `public.ecr.aws/docker/library/python:3.11-slim` (pinned, ECR mirror)
- Py deps: `pyyaml==6.0.2`, `watchdog==6.0.0`, `pytest==8.4.1` (pinned)
- System: `sqlite3`
- Directories: `/app/data/incoming`, `/app/state`, `/app/metrics`, `/app/logs`
- Config: `/app/config.yaml` mode batch → stream
- DB: `/app/warehouse.db` with 4 tables
- Dead-letter: `/app/state/dead_letter.jsonl`
- Offsets: `/app/state/stream_offsets.json` atomic
- PID: `/app/stream.pid`, Log: `/app/logs/stream.log`
- Verifier timeout 600s, agent 1200s per step, build 600s, 2 CPU / 4096MB
