# Step 2: Change Batch to Streaming for Data Freshness

## Context
You completed batch ingestion in Step 1. `/app/batch_ingest.py` now loads all `events_*.jsonl` into `/app/warehouse.db`.

The business now has a new requirement: **data freshness SLA < 2 seconds**. Batch hourly is too slow – new events landing in `/app/data/incoming/` must be visible in the warehouse within seconds.

You must evolve the pipeline to **streaming ingestion**.

Current state inherited from Step 1:
- `/app/batch_ingest.py` exists and works
- `/app/warehouse.db` may contain historic data
- `/app/config.yaml` has `mode: batch`
- `/app/data/incoming/` contains historic files
- Checkpoint at `/app/state/checkpoint.json`, metrics at `/app/metrics/freshness.json`

## Task: Implement Streaming Ingestor

Create `/app/stream_ingest.py` that provides low-latency streaming.

### Requirements

#### 1. Config Update
- Update `/app/config.yaml` to reflect streaming mode. After your change it must contain:
  ```yaml
  mode: stream
  incoming_dir: /app/data/incoming
  warehouse_db: /app/warehouse.db
  checkpoint_dir: /app/state
  metrics_path: /app/metrics/freshness.json
  stream:
    poll_interval_ms: 200
    freshness_sla_sec: 2
    offsets_file: /app/state/stream_offsets.json
  ```
- Keep batch settings if you want, but `mode` must be `stream`.

#### 2. Streaming Script `/app/stream_ingest.py`
Runnable as:
```
python /app/stream_ingest.py            # runs forever until SIGTERM/SIGINT
python /app/stream_ingest.py --once     # does initial backfill only and exits
python /app/stream_ingest.py --config /app/config.yaml
```

**Core logic:**
- **Initial backfill on startup:** Scan all `events_*.jsonl` sorted, same deduplication as batch (keep latest `event_time` per `event_id`). Upsert into warehouse DB.
- **Continuous tailing loop (unless --once):**
  - Maintain per-file offsets in memory and persist to `offsets_file` (`/app/state/stream_offsets.json`) format:
    ```json
    {
      "events_001.jsonl": {"offset": 1234, "lines": 10, "mtime": 1234567890}
    }
    ```
  - Poll loop every `poll_interval_ms` (default 200ms):
    - List files matching `events_*.jsonl` sorted.
    - If file not in offsets → process whole file (new file).
    - If file size larger than offset → file was appended, seek to previous offset and process only new lines (tail).
    - If truncated → reprocess from start.
    - Parse JSONL same validation as batch (skip empty, invalid JSON, invalid event_type/amount/event_time).
    - Deduplicate: if same `event_id` appears with newer `event_time`, replace; if older, keep existing.
    - Upsert immediately into SQLite, recompute `daily_sales` after new data.
    - Update offsets file atomically (tmp→rename) and metrics file.
  - Metrics file `/app/metrics/freshness.json` must be updated continuously with:
    ```json
    {
      "mode": "stream",
      "last_processed_time": "<ISO8601 now Z>",
      "total_events": <int>,
      "total_files_processed": <int>,
      "last_event_delay_ms": <ms>,
      "freshness_sla_sec": 2,
      "is_meeting_sla": true,
      "streaming": true,
      "warehouse_db": "/app/warehouse.db",
      "max_event_time": "<latest>",
      "uptime_sec": <float>
    }
    ```
  - Logging: append to `/app/logs/stream.log` each time events processed.
  - PID file: on start write PID to `/app/stream.pid`, remove on exit. Handle SIGTERM/SIGINT.

- **Freshness SLA:** When new file appears or existing appended, events must be in DB within 2 seconds.
- Preserve batch compatibility: do NOT delete `/app/batch_ingest.py`.

#### 3. Example
```bash
python /app/stream_ingest.py &
echo '{"event_id":"new1","user_id":"u99","event_type":"purchase","amount":99.9,"event_time":"2024-06-01T12:00:01Z"}' >> /app/data/incoming/events_010.jsonl
# within 2 sec, DB contains new1
```

#### 4. Validation
Tests will start streamer in background, create fresh file, sleep 3s, verify DB and freshness.json recent and delay <5s, test append tailing, check crash resilience via restart, and verify config mode stream.

Handle partial last line during tailing (file being written) by not processing incomplete line until next poll.

Good luck!
