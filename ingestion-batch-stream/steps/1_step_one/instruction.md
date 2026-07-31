# Step 1: Batch Data Ingestion Pipeline

## Context
You work on the e-commerce data platform at `/app`. Raw purchase/view/cart events land as JSONL files in `/app/data/incoming/` with names `events_*.jsonl`. Business currently uses manual batch loads; you need to automate it.

Directory layout (created by Dockerfile):
```
/app/
  config.yaml              # ingestion config, initially mode: batch
  data/incoming/           # source JSONL files
  state/                   # checkpoint directory
  metrics/                 # freshness metrics output
  warehouse.db             # SQLite warehouse (you must create)
```

## Source Data Format
Each file `events_*.jsonl` contains one JSON per line. Empty lines may exist and must be skipped. Invalid JSON lines must be skipped with a warning to stderr, not crash.

Schema per event:
```json
{
  "event_id": "evt_123",
  "user_id": "user_42",
  "event_type": "purchase" | "view" | "cart",
  "amount": 12.34,          // float, for purchase >0, for view/cart can be 0
  "event_time": "2024-01-15T10:23:00Z"  // ISO8601 UTC, always ending with Z
}
```
- `event_id` is globally unique but duplicates can appear across files (re-delivery). De-duplication rule: **keep the record with the latest `event_time`**. If `event_time` equal, keep the last seen (in processing order).
- `event_type` must be one of `purchase`, `view`, `cart`; otherwise skip line as invalid.
- `amount` must be numeric >=0; otherwise skip.

## Task: Implement Batch Ingestor

Create file `/app/batch_ingest.py` with executable batch logic. It should be runnable as:
```
python /app/batch_ingest.py
```
Optionally it may accept `--config /app/config.yaml` but must work with no args using defaults.

### Requirements

1. **Config handling:**
   - Read `/app/config.yaml` if exists, else use defaults:
     ```yaml
     incoming_dir: /app/data/incoming
     warehouse_db: /app/warehouse.db
     checkpoint_dir: /app/state
     metrics_path: /app/metrics/freshness.json
     ```
   - Config contains `mode: batch` initially but parser should tolerate extra fields.

2. **Discovery:**
   - Scan `incoming_dir` for files matching `events_*.jsonl` sorted lexicographically.
   - If directory does not exist, create it and exit with 0 (nothing to ingest).
   - If no files, create DB if not exists and write metrics with 0 files.

3. **Parsing & Deduplication:**
   - For each file in sorted order, read line by line.
   - Track in memory dict `event_id -> record` for de-duplication across files (keep latest `event_time`).
   - Comparison of `event_time`: parse ISO8601 `YYYY-MM-DDTHH:MM:SSZ` – you can compare lexicographically since format is sortable, or parse to datetime.
   - After scanning all files, you have final deduped set.

4. **SQLite Warehouse:**
   - DB path from config: `/app/warehouse.db`
   - Create tables if not exist:
     ```sql
     CREATE TABLE IF NOT EXISTS events (
       event_id TEXT PRIMARY KEY,
       user_id TEXT NOT NULL,
       event_type TEXT NOT NULL,
       amount REAL NOT NULL,
       event_time TEXT NOT NULL,
       processed_at TEXT NOT NULL
     );
     CREATE TABLE IF NOT EXISTS daily_sales (
       date TEXT PRIMARY KEY,
       total_amount REAL NOT NULL,
       event_count INTEGER NOT NULL
     );
     ```
   - Upsert events: use `INSERT OR REPLACE INTO events ...` or `ON CONFLICT(event_id) DO UPDATE`.
     - `processed_at` must be current UTC time ISO8601 ending with Z (e.g. `datetime.utcnow().isoformat() + 'Z'`), truncated to seconds is ok.
   - After upserting all deduped events, recompute `daily_sales` from scratch:
     - For each date `YYYY-MM-DD` extracted from `event_time` (first 10 chars) where `event_type='purchase'`, aggregate:
       `total_amount = SUM(amount)`, `event_count = COUNT(*)`
     - Use `INSERT OR REPLACE` into `daily_sales`.

5. **Checkpoint:**
   - After successful processing, ensure checkpoint dir exists.
   - Write `/app/state/checkpoint.json`:
     ```json
     {
       "last_batch_time": "<ISO8601 now Z>",
       "files_processed": ["events_001.jsonl", ...],
       "total_events": <distinct count>,
       "mode": "batch"
     }
     ```
   - Also if `checkpoint_dir` contains `processed.json` legacy, you may write both but `checkpoint.json` is required.

6. **Metrics / Freshness:**
   - Write file from `metrics_path` default `/app/metrics/freshness.json`:
     ```json
     {
       "mode": "batch",
       "last_batch_time": "<ISO8601 now Z>",
       "total_events": <int>,
       "total_files_processed": <int>,
       "warehouse_db": "/app/warehouse.db",
       "max_event_time": "<latest event_time in batch or null>",
       "freshness_sec": <now - max_event_time in seconds if calculable, else null>
     }
     ```
   - Ensure parent directory exists.

7. **Idempotency & Robustness:**
   - Re-running batch must not create duplicates (PK handles) and must produce same final DB counts (idempotent).
   - Partial file failures: skip bad lines, continue.
   - Exit code 0 on success, non-zero only on unrecoverable error (e.g. cannot write DB).

8. **Logging:**
   - Print to stdout: `Processed <N> files, <M> unique events, <K> daily aggregates` similar.
   - Invalid lines to stderr.

### Example

Incoming file `events_001.jsonl`:
```
{"event_id":"e1","user_id":"u1","event_type":"purchase","amount":10.0,"event_time":"2024-01-01T01:00:00Z"}
{"event_id":"e2","user_id":"u2","event_type":"view","amount":0,"event_time":"2024-01-01T02:00:00Z"}
{"event_id":"e1","user_id":"u1","event_type":"purchase","amount":15.0,"event_time":"2024-01-01T03:00:00Z"}
```
Result: e1 kept with amount 15.0 (later time), total distinct 2, daily_sales for 2024-01-01 = 15.0 count 1.

### Validation

Tests will:
- Clean `/app/data/incoming`, `/app/warehouse.db`, `/app/state`, `/app/metrics`
- Create sample `events_*.jsonl` files with duplicates, invalid lines, empty lines, multiple dates
- Run `python /app/batch_ingest.py` and check:
  - DB exists and has correct tables
  - Deduplication correct (latest wins)
  - daily_sales aggregated only purchases
  - checkpoint.json and freshness.json exist and have mode batch
  - Rerun idempotent (counts unchanged)
  - Handles empty dir and malformed lines without crashing

You must NOT modify test files. Implement only `/app/batch_ingest.py` and ensure it meets spec. You may edit `/app/config.yaml` if needed but keep batch mode.
