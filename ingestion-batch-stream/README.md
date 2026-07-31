# codimango/ingestion-batch-stream

## Description
Multi-turn data ingestion task that evolves from batch to streaming to meet data freshness SLA.

- **Step 1 (Batch):** Implement `/app/batch_ingest.py` that scans `/app/data/incoming/events_*.jsonl` (JSONL with `event_id`, `user_id`, `event_type`, `amount`, `event_time`), deduplicates by `event_id` keeping latest `event_time`, upserts into SQLite `/app/warehouse.db` (`events` and aggregated `daily_sales`), and writes checkpoint `/app/state/checkpoint.json` and metrics `/app/metrics/freshness.json` with mode=batch.

- **Step 2 (Streaming):** Migrate to streaming for <2s freshness SLA. Implement `/app/stream_ingest.py` that does initial backfill, then continuously tails incoming dir with per-file byte offsets persisted to `/app/state/stream_offsets.json`, upserts immediately, updates streaming metrics (`mode=stream`, `streaming=true`, `last_event_delay_ms`, `is_meeting_sla`), writes PID to `/app/stream.pid` and log to `/app/logs/stream.log`, handles SIGTERM, supports `--once` flag.

## Completion Rates
- Oracle Step1: 3/3
- Oracle Step2: 3/3 (Full multi-turn 1/1 Mean 1.0, 3/3 Mean 1.0)
- Sonnet: TBD
- Avocado: TBD

## Anti-Cheating
- Dynamic event_ids, random fresh file names, DB counts checked not static strings.
- Streaming test spawns real daemon and measures wall-clock latency.
- Negative test in Step1 ensures no streaming artifacts exist early.
