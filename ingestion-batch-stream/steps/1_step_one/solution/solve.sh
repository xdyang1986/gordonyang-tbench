#!/bin/bash
set -e

# Oracle solution for Step 1 - Batch Ingestion

cat > /app/batch_ingest.py << 'PYEOF'
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import glob
import sqlite3
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_CONFIG = {
    "incoming_dir": "/app/data/incoming",
    "warehouse_db": "/app/warehouse.db",
    "checkpoint_dir": "/app/state",
    "metrics_path": "/app/metrics/freshness.json",
}

def load_config(path="/app/config.yaml"):
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            if yaml:
                with open(path, 'r') as f:
                    data = yaml.safe_load(f) or {}
                    for k in DEFAULT_CONFIG:
                        if k in data:
                            cfg[k] = data[k]
        except Exception as e:
            print(f"Warning: could not read config {path}: {e}", file=sys.stderr)
    return cfg

def parse_iso(s):
    try:
        if s.endswith('Z'):
            s2 = s[:-1]
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                try:
                    dt = datetime.strptime(s2, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        iso = s.replace('Z', '+00:00')
        return datetime.fromisoformat(iso)
    except Exception:
        return None

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def ensure_tables(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS events (
      event_id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      amount REAL NOT NULL,
      event_time TEXT NOT NULL,
      processed_at TEXT NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_sales (
      date TEXT PRIMARY KEY,
      total_amount REAL NOT NULL,
      event_count INTEGER NOT NULL
    );
    """)
    conn.commit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/app/config.yaml')
    args = parser.parse_args()

    cfg = load_config(args.config)
    incoming_dir = cfg.get('incoming_dir', DEFAULT_CONFIG['incoming_dir'])
    warehouse_db = cfg.get('warehouse_db', DEFAULT_CONFIG['warehouse_db'])
    checkpoint_dir = cfg.get('checkpoint_dir', DEFAULT_CONFIG['checkpoint_dir'])
    metrics_path = cfg.get('metrics_path', DEFAULT_CONFIG['metrics_path'])

    os.makedirs(incoming_dir, exist_ok=True)
    os.makedirs(checkpoint_dir, exist_ok=True)
    os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
    os.makedirs(os.path.dirname(warehouse_db), exist_ok=True)

    pattern = os.path.join(incoming_dir, 'events_*.jsonl')
    files = sorted(glob.glob(pattern))

    deduped = {}

    total_lines = 0
    invalid_lines = 0

    for fp in files:
        try:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    total_lines += 1
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        print(f"Warning: invalid JSON {fp}:{line_no}: {line[:100]}", file=sys.stderr)
                        invalid_lines += 1
                        continue

                    try:
                        event_id = str(obj['event_id'])
                        user_id = str(obj['user_id'])
                        event_type = str(obj['event_type'])
                        amount = float(obj['amount'])
                        event_time = str(obj['event_time'])
                    except (KeyError, ValueError, TypeError) as e:
                        print(f"Warning: missing/invalid fields {fp}:{line_no}: {e}", file=sys.stderr)
                        invalid_lines += 1
                        continue

                    if event_type not in ('purchase', 'view', 'cart'):
                        print(f"Warning: invalid event_type {fp}:{line_no}: {event_type}", file=sys.stderr)
                        invalid_lines += 1
                        continue
                    if amount < 0:
                        print(f"Warning: negative amount {fp}:{line_no}", file=sys.stderr)
                        invalid_lines += 1
                        continue
                    dt = parse_iso(event_time)
                    if dt is None:
                        print(f"Warning: invalid event_time {fp}:{line_no}: {event_time}", file=sys.stderr)
                        invalid_lines += 1
                        continue

                    existing = deduped.get(event_id)
                    if existing is None:
                        deduped[event_id] = {
                            'event_id': event_id,
                            'user_id': user_id,
                            'event_type': event_type,
                            'amount': amount,
                            'event_time': event_time,
                            '_dt': dt
                        }
                    else:
                        existing_dt = existing['_dt']
                        if dt > existing_dt or (dt == existing_dt):
                            deduped[event_id] = {
                                'event_id': event_id,
                                'user_id': user_id,
                                'event_type': event_type,
                                'amount': amount,
                                'event_time': event_time,
                                '_dt': dt
                            }
        except Exception as e:
            print(f"Warning: failed to read file {fp}: {e}", file=sys.stderr)
            continue

    conn = sqlite3.connect(warehouse_db)
    ensure_tables(conn)

    processed_at = now_iso()
    for ev in deduped.values():
        conn.execute("""
            INSERT OR REPLACE INTO events (event_id, user_id, event_type, amount, event_time, processed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ev['event_id'], ev['user_id'], ev['event_type'], ev['amount'], ev['event_time'], processed_at))
    conn.commit()

    conn.execute("DELETE FROM daily_sales")
    conn.execute("""
        INSERT INTO daily_sales (date, total_amount, event_count)
        SELECT substr(event_time, 1, 10) as date,
               SUM(amount) as total_amount,
               COUNT(*) as event_count
        FROM events
        WHERE event_type='purchase'
        GROUP BY substr(event_time, 1, 10)
    """)
    conn.commit()

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    total_events = cur.fetchone()[0]
    cur.execute("SELECT MAX(event_time) FROM events")
    row = cur.fetchone()
    max_event_time = row[0] if row else None
    cur.execute("SELECT COUNT(*) FROM daily_sales")
    daily_count = cur.fetchone()[0]
    conn.close()

    freshness_sec = None
    if max_event_time:
        dt_max = parse_iso(max_event_time)
        if dt_max:
            now_dt = datetime.now(timezone.utc)
            freshness_sec = (now_dt - dt_max).total_seconds()

    checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.json")
    checkpoint_data = {
        "last_batch_time": now_iso(),
        "files_processed": [os.path.basename(f) for f in files],
        "total_events": total_events,
        "mode": "batch"
    }
    with open(checkpoint_path, 'w') as out:
        json.dump(checkpoint_data, out, indent=2)

    metrics_data = {
        "mode": "batch",
        "last_batch_time": checkpoint_data["last_batch_time"],
        "total_events": total_events,
        "total_files_processed": len(files),
        "warehouse_db": warehouse_db,
        "max_event_time": max_event_time,
        "freshness_sec": freshness_sec
    }
    with open(metrics_path, 'w') as out:
        json.dump(metrics_data, out, indent=2)

    print(f"Processed {len(files)} files, {total_events} unique events, {daily_count} daily aggregates (skipped {invalid_lines} invalid lines)")

if __name__ == "__main__":
    main()
PYEOF

chmod +x /app/batch_ingest.py

cat > /app/config.yaml << 'YAML'
mode: batch
incoming_dir: /app/data/incoming
warehouse_db: /app/warehouse.db
checkpoint_dir: /app/state
metrics_path: /app/metrics/freshness.json
batch:
  dedup_strategy: latest_event_time
stream:
  poll_interval_ms: 200
  freshness_sla_sec: 2
  offsets_file: /app/state/stream_offsets.json
YAML

echo "Batch ingestor oracle installed, config set to batch"
