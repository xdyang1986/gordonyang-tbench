#!/bin/bash
set -e

cat > /app/batch_ingest.py << 'PYEOF'
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import re
import glob
import hashlib
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
    "dead_letter_path": "/app/state/dead_letter.jsonl",
}

VALID_FILENAME_RE = re.compile(r'^events_[A-Za-z0-9_.\-]+\.jsonl$')

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
        orig = s
        if s.endswith('Z'):
            s2 = s[:-1]
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                try:
                    dt = datetime.strptime(s2, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        iso = orig.replace('Z', '+00:00')
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
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
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ingestion_manifest (
      file_name TEXT PRIMARY KEY,
      file_hash TEXT NOT NULL,
      num_lines INTEGER NOT NULL,
      num_valid INTEGER NOT NULL,
      num_invalid INTEGER NOT NULL,
      processed_at TEXT NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS user_stats (
      user_id TEXT PRIMARY KEY,
      first_seen TEXT NOT NULL,
      last_seen TEXT NOT NULL,
      total_purchases INTEGER NOT NULL,
      total_amount REAL NOT NULL,
      total_views INTEGER NOT NULL,
      total_carts INTEGER NOT NULL
    );
    """)
    conn.commit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/app/config.yaml')
    parser.add_argument('--incremental', action='store_true')
    args = parser.parse_args()

    cfg = load_config(args.config)
    incoming_dir = cfg.get('incoming_dir', DEFAULT_CONFIG['incoming_dir'])
    warehouse_db = cfg.get('warehouse_db', DEFAULT_CONFIG['warehouse_db'])
    checkpoint_dir = cfg.get('checkpoint_dir', DEFAULT_CONFIG['checkpoint_dir'])
    metrics_path = cfg.get('metrics_path', DEFAULT_CONFIG['metrics_path'])
    dead_letter_path = cfg.get('dead_letter_path', "/app/state/dead_letter.jsonl")

    os.makedirs(incoming_dir, exist_ok=True)
    os.makedirs(checkpoint_dir, exist_ok=True)
    os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
    os.makedirs(os.path.dirname(dead_letter_path), exist_ok=True)
    os.makedirs(os.path.dirname(warehouse_db), exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)

    all_files = os.listdir(incoming_dir)
    valid_files = []
    for fn in all_files:
        if VALID_FILENAME_RE.match(fn):
            middle = fn[7:-6]
            if len(middle) == 0:
                continue
            valid_files.append(fn)

    valid_files_sorted = sorted(valid_files)
    full_paths = [os.path.join(incoming_dir, f) for f in valid_files_sorted]

    prev_checkpoint = {}
    checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.json")
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, 'r') as cf:
                data = json.load(cf)
                if "files" in data:
                    prev_checkpoint = data["files"]
        except Exception:
            prev_checkpoint = {}

    deduped = {}
    per_file_stats = {}
    dead_letters = []
    total_invalid_global = 0

    for fn, fp in zip(valid_files_sorted, full_paths):
        try:
            with open(fp, 'rb') as hf:
                content_bytes = hf.read()
                file_hash = hashlib.sha256(content_bytes).hexdigest()
        except Exception as e:
            print(f"Warning: could not hash file {fp}: {e}", file=sys.stderr)
            continue

        if args.incremental and fn in prev_checkpoint:
            prev_hash = prev_checkpoint[fn].get("hash")
            if prev_hash == file_hash:
                per_file_stats[fn] = {
                    "hash": file_hash,
                    "num_lines": prev_checkpoint[fn].get("num_lines", 0),
                    "num_valid": prev_checkpoint[fn].get("num_valid", 0),
                    "num_invalid": prev_checkpoint[fn].get("num_invalid", 0)
                }
                continue

        num_lines = 0
        num_valid = 0
        num_invalid = 0

        try:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                for line_no, line in enumerate(f, 1):
                    num_lines += 1
                    raw = line.rstrip('\n')
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except json.JSONDecodeError:
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "invalid_json",
                            "detected_at": now_iso()
                        })
                        continue
                    try:
                        event_id = str(obj['event_id'])
                        user_id = str(obj['user_id'])
                        event_type = str(obj['event_type'])
                        amount = float(obj['amount'])
                        event_time = str(obj['event_time'])
                    except (KeyError, ValueError, TypeError):
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "invalid_fields",
                            "detected_at": now_iso()
                        })
                        continue
                    if event_type not in ('purchase', 'view', 'cart'):
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "invalid_type",
                            "detected_at": now_iso()
                        })
                        continue
                    if amount < 0:
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "negative_amount",
                            "detected_at": now_iso()
                        })
                        continue
                    dt = parse_iso(event_time)
                    if dt is None:
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "invalid_time",
                            "detected_at": now_iso()
                        })
                        continue
                    num_valid += 1
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
                        if dt > existing['_dt'] or (dt == existing['_dt']):
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

        per_file_stats[fn] = {
            "hash": file_hash,
            "num_lines": num_lines,
            "num_valid": num_valid,
            "num_invalid": num_invalid
        }
        total_invalid_global += num_invalid

    if args.incremental:
        conn = sqlite3.connect(warehouse_db)
        ensure_tables(conn)
        cur = conn.cursor()
        cur.execute("SELECT event_id, event_time FROM events")
        existing_db = {}
        for eid, etime in cur.fetchall():
            dt = parse_iso(etime)
            existing_db[eid] = (etime, dt)
        conn.close()
        filtered = {}
        for eid, rec in deduped.items():
            if eid in existing_db:
                _, existing_dt = existing_db[eid]
                if existing_dt and rec['_dt'] < existing_dt:
                    continue
            filtered[eid] = rec
        deduped = filtered

    conn = sqlite3.connect(warehouse_db)
    ensure_tables(conn)
    processed_at = now_iso()
    try:
        conn.execute("BEGIN")
        for ev in deduped.values():
            conn.execute("""
                INSERT OR REPLACE INTO events (event_id, user_id, event_type, amount, event_time, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ev['event_id'], ev['user_id'], ev['event_type'], ev['amount'], ev['event_time'], processed_at))
        for fn, stats in per_file_stats.items():
            conn.execute("""
                INSERT OR REPLACE INTO ingestion_manifest (file_name, file_hash, num_lines, num_valid, num_invalid, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (fn, stats["hash"], stats["num_lines"], stats["num_valid"], stats["num_invalid"], processed_at))
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
        conn.execute("DELETE FROM user_stats")
        conn.execute("""
            INSERT INTO user_stats (user_id, first_seen, last_seen, total_purchases, total_amount, total_views, total_carts)
            SELECT
                user_id,
                MIN(event_time) as first_seen,
                MAX(event_time) as last_seen,
                SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) as total_purchases,
                SUM(CASE WHEN event_type='purchase' THEN amount ELSE 0 END) as total_amount,
                SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END) as total_views,
                SUM(CASE WHEN event_type='cart' THEN 1 ELSE 0 END) as total_carts
            FROM events
            GROUP BY user_id
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error transaction {e}", file=sys.stderr)
        sys.exit(1)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    total_events = cur.fetchone()[0]
    cur.execute("SELECT MAX(event_time) FROM events")
    max_event_time = cur.fetchone()[0]
    cur.execute("SELECT MIN(event_time) FROM events")
    min_event_time = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM daily_sales")
    daily_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM ingestion_manifest")
    manifest_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM user_stats")
    user_stats_count = cur.fetchone()[0]
    conn.close()

    with open(dead_letter_path, 'w') as dl:
        for entry in dead_letters:
            dl.write(json.dumps(entry) + "\n")

    checkpoint_data = {
        "last_batch_time": now_iso(),
        "files_processed": sorted(per_file_stats.keys()),
        "files": per_file_stats,
        "total_events": total_events,
        "total_invalid": total_invalid_global,
        "mode": "batch"
    }
    with open(checkpoint_path, 'w') as out:
        json.dump(checkpoint_data, out, indent=2)

    freshness_sec = None
    if max_event_time:
        dt_max = parse_iso(max_event_time)
        if dt_max:
            now_dt = datetime.now(timezone.utc)
            freshness_sec = (now_dt - dt_max).total_seconds()

    metrics_data = {
        "mode": "batch",
        "last_batch_time": checkpoint_data["last_batch_time"],
        "total_events": total_events,
        "total_files_processed": len(per_file_stats),
        "total_invalid": total_invalid_global,
        "warehouse_db": warehouse_db,
        "max_event_time": max_event_time,
        "min_event_time": min_event_time,
        "freshness_sec": freshness_sec,
        "manifest_count": manifest_count,
        "user_stats_count": user_stats_count
    }
    with open(metrics_path, 'w') as out:
        json.dump(metrics_data, out, indent=2)

    print(f"Processed {len(per_file_stats)} files, {total_events} unique events, {total_invalid_global} invalid, {user_stats_count} users, {daily_count} daily")

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
dead_letter_path: /app/state/dead_letter.jsonl
manifest_db_table: ingestion_manifest
batch:
  dedup_strategy: latest_event_time
stream:
  poll_interval_ms: 200
  freshness_sla_sec: 2
  offsets_file: /app/state/stream_offsets.json
YAML

echo "Batch ingestor oracle hard mode installed"
