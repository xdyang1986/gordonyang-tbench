# Step 1: Batch Data Ingestion Pipeline — Extra Hard v4

## Context
You work on the e-commerce data platform at `/app`. Raw events land in `/app/data/incoming/` as JSONL `events_<id>.jsonl` from multiple producers. Producers write temp files (`*.tmp`, `*.part`, `*.gz`, hidden `.*`, `..` in name) that must be ignored. Need data quality SLOs, audit tables, archiving, future/outlier filtering, sessionization, fraud detection.

Directories:
```
/app/
  config.yaml
  data/incoming/
  data/archive/     # you create
  state/
  metrics/
  logs/
  warehouse.db
```

## Source Format
Valid file regex `^events_[A-Za-z0-9_.\-]+\.jsonl$`, middle part >=1 char, no `..`, not starting with `.` after `events_`. One JSON per line, empty lines skip.

Event schema:
```json
{
  "event_id": "evt_123",
  "user_id": "user_42",
  "event_type": "purchase"|"view"|"cart",
  "amount": 12.34,
  "event_time": "2024-01-15T10:23:00Z" // Z or +00:00 or +/-HH:MM, fractional allowed
}
```
Invalid reasons for dead-letter:
- `invalid_json`, `invalid_fields`, `invalid_type`, `negative_amount`, `outlier_amount` (>100000), `invalid_time`, `future_event` (>now+1h)

Dedup: keep latest `event_time` per `event_id`; equal → last seen wins (lexicographic file order + line order). Must normalize timezone to UTC for comparison.

## Task: Implement /app/batch_ingest.py

```
python /app/batch_ingest.py
python /app/batch_ingest.py --config /app/config.yaml
python /app/batch_ingest.py --incremental
```

### Discovery (strict)
- List incoming, filter regex, ignore hidden (`.*`), `..`, `*.tmp`, `*.part`, `*.gz`, `events_.jsonl` invalid.
- Sorted lexicographically.
- Create dirs if missing.

### Parsing & Hashing
- SHA256 hex + file_size per file.
- Line-by-line (low memory), track per-file num_lines, num_valid, num_invalid, num_future, num_outlier.
- Parse ISO8601 robustly supporting `Z`, `+00:00`, `+05:00`, `-08:00`, fractional.

### Warehouse – 8 tables (extra hard):

```sql
events(event_id PK, user_id, event_type, amount, event_time, processed_at)
daily_sales(date PK, total_amount, event_count)
ingestion_manifest(file_name PK, file_hash, file_size INT, num_lines, num_valid, num_invalid, num_future, num_outlier, processed_at)
user_stats(user_id PK, first_seen, last_seen, total_purchases, total_amount REAL, total_views, total_carts, avg_amount REAL)
daily_top_users(date TEXT, user_id TEXT, total_amount REAL, PK(date,user_id)) -- top 3 per date
hourly_sales(hour TEXT PK, total_amount REAL, event_count INT) -- hour truncated YYYY-MM-DDTHH:00:00Z, purchases only
sessions(session_id TEXT PK, user_id TEXT, start_time TEXT, end_time TEXT, event_count INT, total_amount REAL, duration_sec INT)
fraud_alerts(user_id TEXT, window_start TEXT, window_end TEXT, purchase_count INT, total_amount REAL, PRIMARY KEY(user_id, window_start))
```

- `events`: upsert REPLACE, processed_at now Z.
- `daily_sales`: recompute from scratch per date `substr(event_time,1,10)` where purchase.
- `ingestion_manifest`: upsert per file with hash, size, counts.
- `user_stats`: per user after dedup: first_seen MIN(event_time), last_seen MAX(event_time), total_purchases COUNT purchase, total_amount SUM purchase, total_views COUNT view, total_carts COUNT cart, avg_amount = total_amount / total_purchases if >0 else 0.
- `daily_top_users`: per date per user purchase sum, keep only top 3 per date by total_amount DESC, user_id ASC tie.
- `hourly_sales`: per hour `substr(event_time,1,13)||':00:00Z'`, purchases only.
- `sessions`: sessionization per user: sort user's events by event_time ascending, split into sessions where gap between consecutive events >30 minutes (1800 sec). For each session, session_id = `{user_id}_{start_time_iso}` where start_time is first event's event_time (use normalized Z format from DB? Keep original string of first event for id stability). start_time = first event_time, end_time = last event_time, event_count = count events in session (all types), total_amount = SUM purchase amounts in session, duration_sec = end_time - start_time in seconds (0 if single event). Use UTC parsed times for gap calc.
- `fraud_alerts`: sliding 1-hour window fraud detection for purchases only: for each user, sort purchase events by event_time ascending, use two pointers sliding window where window duration <=1 hour (3600 sec). For any window where purchase_count >5, emit alert row: user_id, window_start = first event_time in window, window_end = last event_time in window, purchase_count, total_amount sum in window. Deduplicate alerts: if multiple windows have same window_start for same user, keep one with max purchase_count. Only emit if count >5.

All recomputations in single transaction `BEGIN IMMEDIATE`, WAL mode `PRAGMA journal_mode=WAL`.

### Checkpoint v2 atomic
`/app/state/checkpoint.json`:
```json
{
  "version": 2,
  "last_batch_time": "<now Z>",
  "created_at": "<now Z>",
  "files_processed": [...],
  "files": {"events_001.jsonl": {"hash": "...", "size": 1234, "num_lines": 10, "num_valid": 8, "num_invalid": 2, "num_future": 0, "num_outlier": 0, "mtime": 1234567890.0}},
  "archive": [...],
  "total_events": 5,
  "total_invalid": 1,
  "total_future": 0,
  "total_outlier": 0,
  "mode": "batch"
}
```
Atomic write tmp→rename.

### Dead-letter sorted
`/app/state/dead_letter.jsonl` overwrite each run, sorted file ASC line_no ASC, reasons 7 types.

### Archiving
After commit, move each valid processed file from incoming to `/app/data/archive/` (overwrite). Checkpoint archive field list. Second full run with empty incoming should process 0 files but DB unchanged.

### Metrics v2
`/app/metrics/freshness.json` with mode batch, version 2, total_events, total_files_processed (only valid regex), total_invalid, total_future, total_outlier, max/min event_time, freshness_sec, manifest_count, user_stats_count, daily_top_users_count, hourly_sales_count, sessions_count, fraud_alerts_count, archived_files. Atomic write.

### Idempotency & Incremental
Full rerun idempotent. `--incremental` skip files where hash equals previous checkpoint hash.

### Logging & Robustness
stdout summary with all counts, stderr warnings, must not crash on empty, hidden, tmp, .., gz, future, outlier.

### Validation
Tests will create files including tmp/part/gz/hidden/.., future/outlier, +00:00/+05:00 times, verify 8 tables, manifest hash/size, checkpoint version 2 atomic, dead_letter sorted 7 reasons, archive moves, metrics 14 fields, idempotency, incremental, daily_top_users top3, hourly, sessions split 30min gap, fraud >5 purchases in 1h sliding window.
