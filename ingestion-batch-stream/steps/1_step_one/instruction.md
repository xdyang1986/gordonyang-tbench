# Step 1: Batch Data Ingestion Pipeline — Hard Mode v3 (High Complexity)

## Context
You work on e-commerce data platform at `/app`. Raw events land in `/app/data/incoming/` as JSONL files `events_<id>.jsonl` from multiple upstream producers. Producers write temp files (`*.tmp`, `*.part`, `*.gz`, hidden `.*`, files containing `..`), which must be robustly ignored. Business requires data quality SLOs, audit tables, archiving, and future/outlier detection.

Directory layout (Dockerfile creates):
```
/app/
  config.yaml
  data/incoming/    # source
  data/archive/     # you must create and move processed files here
  state/            # checkpoint, dead_letter, manifest
  metrics/
  logs/
  warehouse.db
```

## Source Format
Each valid file `events_<id>.jsonl` matches regex `^events_[A-Za-z0-9_.\-]+\.jsonl$` with middle part at least 1 char and not `..` and not starting with `.`. One JSON per line. Empty lines skip.

Schema:
```json
{
  "event_id": "evt_123",
  "user_id": "user_42",
  "event_type": "purchase"|"view"|"cart",
  "amount": 12.34,
  "event_time": "2024-01-15T10:23:00Z" // Z or +00:00 or +/-HH:MM, fractional seconds allowed
}
```
Validation and reasons for dead-letter:
- `invalid_json` JSON decode fails
- `invalid_fields` missing required keys or not string/parseable
- `invalid_type` event_type not in allowed set
- `negative_amount` amount <0 or NaN
- `outlier_amount` amount > 100000 (new)
- `invalid_time` unparsable
- `future_event` event_time > now UTC + 1 hour (new)

Dedup: keep latest `event_time` per `event_id`; equal time → last seen wins (lexicographic file order + line order). Must handle `Z` vs `+00:00` vs `+05:00` – normalize to UTC for comparison.

## Task: Implement /app/batch_ingest.py

Runnable:
```
python /app/batch_ingest.py
python /app/batch_ingest.py --config /app/config.yaml
python /app/batch_ingest.py --incremental   # skip unchanged files by hash (optional but must not break)
```

### Requirements (All Mandatory)

1. **Discovery – strict:**
   - List `incoming_dir`, filter by regex `^events_[A-Za-z0-9_.\-]+\.jsonl$`, middle part >=1 char, must NOT contain `..`, must NOT start with `.` after `events_`, must NOT end with `.tmp/.part/.gz`. Ignore hidden files (`.*`).
   - Only files matching exactly `.jsonl` are valid. `events_001.jsonl.tmp` → ignore.
   - Sorted lexicographically.
   - Create incoming, archive, state, metrics, logs dirs if missing.

2. **Hashing & Line-by-Line:**
   - SHA256 hex of full file content per file.
   - Read line by line (low memory), track per-file: num_lines (total lines in file), num_valid, num_invalid (including future/outlier as invalid), num_future, num_outlier.
   - Also track file_size, mtime.

3. **Warehouse – 6 tables (was 4):**
   ```sql
   events(event_id PK, user_id, event_type, amount, event_time, processed_at)
   daily_sales(date PK, total_amount, event_count)
   ingestion_manifest(file_name PK, file_hash, file_size INTEGER, num_lines, num_valid, num_invalid, num_future, num_outlier, processed_at)
   user_stats(user_id PK, first_seen, last_seen, total_purchases, total_amount REAL, total_views, total_carts, avg_amount REAL)
   daily_top_users(date TEXT, user_id TEXT, total_amount REAL, PRIMARY KEY(date, user_id))
   hourly_sales(hour TEXT PK, total_amount REAL, event_count INTEGER)
   ```
   - `events`: upsert `INSERT OR REPLACE`, processed_at now Z.
   - `daily_sales`: recompute from scratch: per date `substr(event_time,1,10)` where purchase, SUM(amount), COUNT.
   - `ingestion_manifest`: upsert per file with hash, file_size, counts, processed_at now. Must include future/outlier counts.
   - `user_stats`: per user after dedup: first_seen MIN(event_time), last_seen MAX(event_time), total_purchases COUNT purchase, total_amount SUM purchase amounts, total_views COUNT view, total_carts COUNT cart, avg_amount = total_amount / total_purchases if purchases>0 else 0.
   - `daily_top_users`: per date, per user purchase sum, keep only top 3 users per date by total_amount DESC (if tie, user_id ASC). So at most 3 rows per date.
   - `hourly_sales`: per hour truncated: hour = substr(event_time,1,13) + ":00:00Z" where first 13 chars `YYYY-MM-DDTHH` → reconstruct as `YYYY-MM-DDTHH:00:00Z`. Only purchase events. SUM(amount), COUNT.
   - All recomputations in single transaction with `BEGIN IMMEDIATE` and WAL journal mode (`PRAGMA journal_mode=WAL`).

4. **Checkpoint – versioned atomic:**
   - `/app/state/checkpoint.json` structure:
     ```json
     {
       "version": 2,
       "last_batch_time": "<now Z>",
       "created_at": "<now Z>",
       "files_processed": ["events_001.jsonl", ...],
       "files": {
         "events_001.jsonl": {"hash": "<sha256>", "size": 1234, "num_lines": 10, "num_valid": 8, "num_invalid": 2, "num_future": 0, "num_outlier": 0, "mtime": 1234567890.0},
         ...
       },
       "archive": ["events_001.jsonl", ...],
       "total_events": 5,
       "total_invalid": 1,
       "total_future": 0,
       "total_outlier": 0,
       "mode": "batch"
     }
     ```
   - Must be written atomically: write to temp file then rename.
   - `files_processed` sorted, equals keys of `files`. `archive` list equals files moved.

5. **Dead-letter – sorted:**
   - `/app/state/dead_letter.jsonl` overwrite each run, one JSON per invalid:
     `{"file": "...", "line_no": 5, "content": "<raw 500>", "reason": "invalid_json|invalid_fields|invalid_type|negative_amount|outlier_amount|invalid_time|future_event", "detected_at": "<now Z>"}`
   - Sorted by file ASC then line_no ASC.
   - Must exist even if 0 invalid (empty).

6. **Archiving:**
   - After successful batch (transaction committed), move each processed valid file from `incoming_dir` to `/app/data/archive/` (create dir). Use `os.rename` or `shutil.move`. If file already exists in archive, overwrite.
   - If incremental mode and file skipped due to hash match, still ensure it is in archive (if previously archived, keep).
   - Checkpoint `archive` field must list archived files.

7. **Metrics:**
   - `/app/metrics/freshness.json`:
     ```json
     {
       "mode": "batch",
       "version": 2,
       "last_batch_time": "...",
       "total_events": 5,
       "total_files_processed": 2,
       "total_invalid": 1,
       "total_future": 0,
       "total_outlier": 0,
       "warehouse_db": "/app/warehouse.db",
       "max_event_time": "...",
       "min_event_time": "...",
       "freshness_sec": ...,
       "manifest_count": 2,
       "user_stats_count": 3,
       "daily_top_users_count": 3,
       "hourly_sales_count": 2,
       "archived_files": 2
     }
     ```
   - Atomic write.

8. **Idempotency & Incremental:**
   - Full rerun without changes: same counts, same hashes, archive still has files, but after archiving, incoming will be empty (since files moved). So second full run without new files should process 0 files, but DB remains same. Tests will re-create files after archive test.
   - `--incremental`: if checkpoint exists with same hash, skip file (do not re-parse). Must still be counted? For simplicity, incremental still processes manifest but skips event parsing – DB unchanged.

9. **Logging:** stdout summary with all counts, stderr per invalid with reason.

10. **Robustness:** Must not crash on empty, hidden, tmp, .., gz, future, outlier, invalid. Must process line by line.

### Example with new edge cases
`events_001.jsonl`:
```
{"event_id":"e1","user_id":"u1","event_type":"purchase","amount":10,"event_time":"2024-01-01T01:00:00Z"}
{"event_id":"e1","user_id":"u1","event_type":"purchase","amount":15,"event_time":"2024-01-01T03:00:00+00:00"} // +00:00, latest wins
{"event_id":"future1","user_id":"u2","event_type":"purchase","amount":5,"event_time":"2099-01-01T00:00:00Z"} // future → invalid
{"event_id":"outlier","user_id":"u3","event_type":"purchase","amount":200000,"event_time":"2024-01-01T00:00:00Z"} // outlier → invalid
```
- e1 kept 15, future/outlier go to dead_letter, not in DB.

### Validation
Tests will create files including tmp/part/gz/hidden/.. that must be ignored, future and outlier events, +00:00 and +05:00 times, verify 6 tables, manifest hash/size, checkpoint version 2 atomic, dead_letter sorted reasons, archive moves files, metrics counts, idempotency, incremental skip, hourly and daily_top_users top 3 logic.
