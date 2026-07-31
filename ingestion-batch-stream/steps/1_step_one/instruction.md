# Step 1: Batch Data Ingestion Pipeline — Hard Mode

## Context
You work on the e-commerce data platform at `/app`. Raw purchase/view/cart events land as JSONL files in `/app/data/incoming/`. Files are named `events_<id>.jsonl` and are produced by upstream producers that sometimes write temp files (`*.tmp`, `*.part`, `*.gz`) that must be ignored. Business currently uses manual batch loads; you need to automate a robust batch ingestion that meets data quality SLOs.

Directory layout (created by Dockerfile):
```
/app/
  config.yaml              # ingestion config, initially mode: batch
  data/incoming/           # source JSONL files (may contain tmp/part/gz)
  state/                   # checkpoint + dead-letter directory
  metrics/                 # freshness metrics output
  logs/                    # logs dir
  warehouse.db             # SQLite warehouse (you must create)
```

## Source Data Format
Each **valid** file `events_<alphanum_-_.>.jsonl` (regex `^events_[A-Za-z0-9_.-]+\.jsonl$`) contains one JSON per line. Empty lines may exist and must be skipped. Invalid JSON lines must be skipped with a warning to stderr and recorded to dead-letter (see below).

Schema per event:
```json
{
  "event_id": "evt_123",
  "user_id": "user_42",
  "event_type": "purchase" | "view" | "cart",
  "amount": 12.34,
  "event_time": "2024-01-15T10:23:00Z"   // ISO8601 UTC, Z or +00:00 allowed
}
```
- `event_id` globally unique but duplicates appear across files (re-delivery). De-duplication rule: **keep the record with the latest `event_time`**. If `event_time` equal, keep last seen in processing order (lexicographic file order + line order).
- `event_type` must be one of `purchase`, `view`, `cart`; otherwise invalid.
- `amount` must be numeric >=0; otherwise invalid.
- `event_time` must be parseable ISO8601. You must support `...Z` and `...+00:00` forms. Invalid time → invalid line.

## Task: Implement Batch Ingestor

Create file `/app/batch_ingest.py` runnable as:
```
python /app/batch_ingest.py
python /app/batch_ingest.py --config /app/config.yaml
python /app/batch_ingest.py --incremental   # optional: only process changed files based on hash (if you implement)
```
Must work with no args using defaults from config.

### Requirements (All Mandatory)

1. **Config handling:**
   - Read `/app/config.yaml` if exists, else defaults:
     ```yaml
     incoming_dir: /app/data/incoming
     warehouse_db: /app/warehouse.db
     checkpoint_dir: /app/state
     metrics_path: /app/metrics/freshness.json
     dead_letter_path: /app/state/dead_letter.jsonl
     manifest_db_table: ingestion_manifest
     ```
   - Tolerate extra fields.

2. **Discovery (hard):**
   - Scan `incoming_dir` for files matching **regex** `^events_[A-Za-z0-9_.-]+\.jsonl$` (not a simple glob for `*.tmp`/`*.part`/`*.gz`). Specifically:
     - `events_001.jsonl` → valid
     - `events_001.jsonl.tmp` → ignore
     - `events_001.jsonl.part` → ignore
     - `events_001.jsonl.gz` → ignore
     - `events_.jsonl` → invalid and ignore
   - Sorted lexicographically.
   - If incoming_dir missing → create and exit 0.
   - If no valid files → create DB if not exists and write metrics with 0 files.

3. **Parsing, Hashing & Deduplication:**
   - For each valid file, compute **SHA256 hash** of its full content (hex).
   - Read line by line (do NOT load entire file into memory at once).
   - Track per-file counts: total lines, valid, invalid.
   - Track global deduped dict `event_id -> record` keeping latest `event_time`. For equal time, last seen wins.
   - Parsing ISO8601: support `Z` and `+00:00` and fractional seconds.
   - Invalid lines must be collected for dead-letter.

4. **SQLite Warehouse (hard):**
   - DB path: `/app/warehouse.db`
   - Create tables if not exist (all mandatory):
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
     CREATE TABLE IF NOT EXISTS ingestion_manifest (
       file_name TEXT PRIMARY KEY,
       file_hash TEXT NOT NULL,
       num_lines INTEGER NOT NULL,
       num_valid INTEGER NOT NULL,
       num_invalid INTEGER NOT NULL,
       processed_at TEXT NOT NULL
     );
     CREATE TABLE IF NOT EXISTS user_stats (
       user_id TEXT PRIMARY KEY,
       first_seen TEXT NOT NULL,
       last_seen TEXT NOT NULL,
       total_purchases INTEGER NOT NULL,
       total_amount REAL NOT NULL,
       total_views INTEGER NOT NULL,
       total_carts INTEGER NOT NULL
     );
     ```
   - Upsert events, manifest, recompute daily_sales and user_stats in single transaction.

5. **Checkpoint:**
   - Write `/app/state/checkpoint.json`:
     ```json
     {
       "last_batch_time": "<ISO now Z>",
       "files_processed": ["events_001.jsonl", ...],
       "files": {
         "events_001.jsonl": {"hash": "<sha256>", "num_lines": 10, "num_valid": 8, "num_invalid": 2},
         ...
       },
       "total_events": <distinct>,
       "total_invalid": <overall>,
       "mode": "batch"
     }
     ```

6. **Dead-letter:**
   - Write `/app/state/dead_letter.jsonl` with one JSON per invalid line containing file, line_no, content truncated to 500, reason (`invalid_json|invalid_fields|invalid_type|negative_amount|invalid_time`), detected_at.

7. **Metrics:**
   - Write `/app/metrics/freshness.json` with mode batch, total_events, total_files_processed (only valid regex), total_invalid, max/min event_time, freshness_sec, manifest_count, user_stats_count.

8. **Idempotency & Incremental:**
   - Re-running must not duplicate.
   - If `--incremental` flag, skip files whose hash equals previous checkpoint hash.

9. **Logging:** stdout processed counts, stderr warnings.

10. **Robustness:** Must not crash on empty, invalid, tmp/part/gz.

### Validation
Tests check 4 tables, dedup with +00:00, daily_sales, user_stats, manifest hashes, checkpoint files dict, dead_letter reasons, ignore tmp/part/gz, idempotency, empty handling.
