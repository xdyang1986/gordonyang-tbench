#!/bin/bash
set -e

cat > /app/batch_ingest.py << 'PYEOF'
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import re
import hashlib
import sqlite3
import shutil
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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
    "archive_dir": "/app/data/archive",
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
            print(f"Warning: config {e}", file=sys.stderr)
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
    conn.execute("PRAGMA journal_mode=WAL;")
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
      file_size INTEGER NOT NULL,
      num_lines INTEGER NOT NULL,
      num_valid INTEGER NOT NULL,
      num_invalid INTEGER NOT NULL,
      num_future INTEGER NOT NULL,
      num_outlier INTEGER NOT NULL,
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
      total_carts INTEGER NOT NULL,
      avg_amount REAL NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_top_users (
      date TEXT NOT NULL,
      user_id TEXT NOT NULL,
      total_amount REAL NOT NULL,
      PRIMARY KEY(date, user_id)
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS hourly_sales (
      hour TEXT PRIMARY KEY,
      total_amount REAL NOT NULL,
      event_count INTEGER NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
      session_id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      start_time TEXT NOT NULL,
      end_time TEXT NOT NULL,
      event_count INTEGER NOT NULL,
      total_amount REAL NOT NULL,
      duration_sec INTEGER NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS fraud_alerts (
      user_id TEXT NOT NULL,
      window_start TEXT NOT NULL,
      window_end TEXT NOT NULL,
      purchase_count INTEGER NOT NULL,
      total_amount REAL NOT NULL,
      PRIMARY KEY(user_id, window_start)
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
    archive_dir = cfg.get('archive_dir', "/app/data/archive")

    os.makedirs(incoming_dir, exist_ok=True)
    os.makedirs(archive_dir, exist_ok=True)
    os.makedirs(checkpoint_dir, exist_ok=True)
    os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
    os.makedirs(os.path.dirname(dead_letter_path), exist_ok=True)
    os.makedirs(os.path.dirname(warehouse_db), exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)

    try:
        all_files = os.listdir(incoming_dir)
    except OSError:
        all_files = []
    valid_files = []
    for fn in all_files:
        if fn.startswith('.'):
            continue
        if '..' in fn:
            continue
        if not VALID_FILENAME_RE.match(fn):
            continue
        middle = fn[7:-6]
        if len(middle) == 0:
            continue
        if middle.startswith('.'):
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
    total_future = 0
    total_outlier = 0
    now_dt = datetime.now(timezone.utc)
    future_threshold = now_dt + timedelta(hours=1)

    for fn, fp in zip(valid_files_sorted, full_paths):
        try:
            with open(fp, 'rb') as hf:
                content_bytes = hf.read()
                file_hash = hashlib.sha256(content_bytes).hexdigest()
                file_size = len(content_bytes)
            stat_mtime = os.path.getmtime(fp)
        except Exception as e:
            print(f"Warning: hash fail {fp}: {e}", file=sys.stderr)
            continue

        if args.incremental and fn in prev_checkpoint:
            prev_hash = prev_checkpoint[fn].get("hash")
            if prev_hash == file_hash:
                per_file_stats[fn] = {
                    "hash": file_hash,
                    "size": file_size,
                    "num_lines": prev_checkpoint[fn].get("num_lines", 0),
                    "num_valid": prev_checkpoint[fn].get("num_valid", 0),
                    "num_invalid": prev_checkpoint[fn].get("num_invalid", 0),
                    "num_future": prev_checkpoint[fn].get("num_future", 0),
                    "num_outlier": prev_checkpoint[fn].get("num_outlier", 0),
                    "mtime": stat_mtime
                }
                continue

        num_lines = 0
        num_valid = 0
        num_invalid = 0
        num_future_file = 0
        num_outlier_file = 0

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
                    if event_type not in ('purchase','view','cart'):
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "invalid_type",
                            "detected_at": now_iso()
                        })
                        continue
                    if amount != amount:
                        num_invalid += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "negative_amount",
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
                    if amount > 100000:
                        num_invalid += 1
                        num_outlier_file += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "outlier_amount",
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
                    if dt > future_threshold:
                        num_invalid += 1
                        num_future_file += 1
                        dead_letters.append({
                            "file": fn,
                            "line_no": line_no,
                            "content": raw[:500],
                            "reason": "future_event",
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
            print(f"Warning: read fail {fp}: {e}", file=sys.stderr)
            continue

        per_file_stats[fn] = {
            "hash": file_hash,
            "size": file_size,
            "num_lines": num_lines,
            "num_valid": num_valid,
            "num_invalid": num_invalid,
            "num_future": num_future_file,
            "num_outlier": num_outlier_file,
            "mtime": stat_mtime
        }
        total_invalid_global += num_invalid
        total_future += num_future_file
        total_outlier += num_outlier_file

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
        conn.execute("BEGIN IMMEDIATE")
        for ev in deduped.values():
            conn.execute("""
                INSERT OR REPLACE INTO events (event_id, user_id, event_type, amount, event_time, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ev['event_id'], ev['user_id'], ev['event_type'], ev['amount'], ev['event_time'], processed_at))
        for fn, stats in per_file_stats.items():
            conn.execute("""
                INSERT OR REPLACE INTO ingestion_manifest (file_name, file_hash, file_size, num_lines, num_valid, num_invalid, num_future, num_outlier, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (fn, stats["hash"], stats["size"], stats["num_lines"], stats["num_valid"], stats["num_invalid"], stats["num_future"], stats["num_outlier"], processed_at))
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
            INSERT INTO user_stats (user_id, first_seen, last_seen, total_purchases, total_amount, total_views, total_carts, avg_amount)
            SELECT
                user_id,
                MIN(event_time) as first_seen,
                MAX(event_time) as last_seen,
                SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) as total_purchases,
                SUM(CASE WHEN event_type='purchase' THEN amount ELSE 0 END) as total_amount,
                SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END) as total_views,
                SUM(CASE WHEN event_type='cart' THEN 1 ELSE 0 END) as total_carts,
                CASE WHEN SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) > 0
                     THEN SUM(CASE WHEN event_type='purchase' THEN amount ELSE 0 END) *1.0 / SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END)
                     ELSE 0 END as avg_amount
            FROM events
            GROUP BY user_id
        """)
        # daily_top_users
        conn.execute("DELETE FROM daily_top_users")
        cur = conn.cursor()
        cur.execute("""
            SELECT substr(event_time, 1, 10) as date, user_id, SUM(amount) as total_amount
            FROM events
            WHERE event_type='purchase'
            GROUP BY date, user_id
        """)
        rows = cur.fetchall()
        grouped = defaultdict(list)
        for date, user_id, total in rows:
            grouped[date].append((user_id, total))
        for date, ulist in grouped.items():
            ulist_sorted = sorted(ulist, key=lambda x: (-x[1], x[0]))
            top3 = ulist_sorted[:3]
            for user_id, total in top3:
                conn.execute("""
                    INSERT INTO daily_top_users (date, user_id, total_amount)
                    VALUES (?, ?, ?)
                """, (date, user_id, total))
        # hourly_sales
        conn.execute("DELETE FROM hourly_sales")
        conn.execute("""
            INSERT INTO hourly_sales (hour, total_amount, event_count)
            SELECT substr(event_time, 1, 13) || ':00:00Z' as hour,
                   SUM(amount) as total_amount,
                   COUNT(*) as event_count
            FROM events
            WHERE event_type='purchase'
            GROUP BY hour
        """)
        # sessions
        conn.execute("DELETE FROM sessions")
        cur.execute("SELECT user_id, event_id, event_time, event_type, amount FROM events ORDER BY user_id, event_time")
        events_rows = cur.fetchall()
        # group by user
        user_events = defaultdict(list)
        for user_id, event_id, event_time, event_type, amount in events_rows:
            dt = parse_iso(event_time)
            if dt is None:
                continue
            user_events[user_id].append((dt, event_time, event_id, event_type, amount))
        for user_id, ev_list in user_events.items():
            ev_list_sorted = sorted(ev_list, key=lambda x: x[0])
            # sessionization
            session_start_idx = 0
            while session_start_idx < len(ev_list_sorted):
                session_start_dt, session_start_str, _, _, _ = ev_list_sorted[session_start_idx]
                session_events = [ev_list_sorted[session_start_idx]]
                last_dt = session_start_dt
                next_idx = session_start_idx + 1
                while next_idx < len(ev_list_sorted):
                    cur_dt, _, _, _, _ = ev_list_sorted[next_idx]
                    gap = (cur_dt - last_dt).total_seconds()
                    if gap > 1800:  # 30 min
                        break
                    session_events.append(ev_list_sorted[next_idx])
                    last_dt = cur_dt
                    next_idx += 1
                # build session
                start_time = session_events[0][1]  # keep original string for stability
                end_time = session_events[-1][1]
                start_dt = session_events[0][0]
                end_dt = session_events[-1][0]
                duration = int((end_dt - start_dt).total_seconds())
                event_count = len(session_events)
                total_amount = sum(ev[4] for ev in session_events if ev[3] == 'purchase')
                session_id = f"{user_id}_{start_time}"
                # ensure unique session_id if collision (same start_time duplicate rare)
                # append count if collision
                base_session_id = session_id
                suffix = 0
                while True:
                    try:
                        conn.execute("""
                            INSERT INTO sessions (session_id, user_id, start_time, end_time, event_count, total_amount, duration_sec)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (session_id, user_id, start_time, end_time, event_count, total_amount, duration))
                        break
                    except sqlite3.IntegrityError:
                        suffix += 1
                        session_id = f"{base_session_id}_{suffix}"
                        if suffix > 10:
                            break
                session_start_idx = next_idx

        # fraud_alerts sliding 1h window >5 purchases
        conn.execute("DELETE FROM fraud_alerts")
        for user_id, ev_list in user_events.items():
            # only purchases
            purchases = [(dt, et_str, amount) for dt, et_str, _, et_type, amount in ev_list if et_type == 'purchase']
            if len(purchases) <= 5:
                continue
            purchases_sorted = sorted(purchases, key=lambda x: x[0])
            # sliding window two pointers
            left = 0
            best_per_start = {}  # window_start -> (purchase_count, total_amount, window_end)
            for right in range(len(purchases_sorted)):
                while purchases_sorted[right][0] - purchases_sorted[left][0] > timedelta(seconds=3600):
                    left += 1
                window_count = right - left + 1
                if window_count > 5:
                    ws_dt, ws_str, _ = purchases_sorted[left]
                    we_dt, we_str, _ = purchases_sorted[right]
                    total_amt = sum(p[2] for p in purchases_sorted[left:right+1])
                    # keep max count per window_start
                    existing = best_per_start.get(ws_str)
                    if existing is None or window_count > existing[0] or (window_count == existing[0] and total_amt > existing[1]):
                        best_per_start[ws_str] = (window_count, total_amt, we_str)
            # insert
            for ws_str, (cnt, total_amt, we_str) in best_per_start.items():
                conn.execute("""
                    INSERT INTO fraud_alerts (user_id, window_start, window_end, purchase_count, total_amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, ws_str, we_str, cnt, total_amt))

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error tx {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
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
    cur.execute("SELECT COUNT(*) FROM daily_top_users")
    top_users_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM hourly_sales")
    hourly_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sessions")
    sessions_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM fraud_alerts")
    fraud_count = cur.fetchone()[0]
    conn.close()

    dead_letters_sorted = sorted(dead_letters, key=lambda x: (x["file"], x["line_no"]))
    with open(dead_letter_path, 'w') as dl:
        for entry in dead_letters_sorted:
            dl.write(json.dumps(entry) + "\n")
    if not os.path.exists(dead_letter_path):
        open(dead_letter_path, 'w').close()

    archived = []
    for fn in per_file_stats.keys():
        src = os.path.join(incoming_dir, fn)
        dst = os.path.join(archive_dir, fn)
        try:
            if os.path.exists(src):
                shutil.move(src, dst)
                archived.append(fn)
        except Exception as e:
            print(f"Warning: archive fail {fn}: {e}", file=sys.stderr)

    checkpoint_data = {
        "version": 2,
        "last_batch_time": now_iso(),
        "created_at": now_iso(),
        "files_processed": sorted(per_file_stats.keys()),
        "files": per_file_stats,
        "archive": sorted(archived),
        "total_events": total_events,
        "total_invalid": total_invalid_global,
        "total_future": total_future,
        "total_outlier": total_outlier,
        "mode": "batch"
    }
    tmp_cp = checkpoint_path + ".tmp"
    with open(tmp_cp, 'w') as out:
        json.dump(checkpoint_data, out, indent=2)
    os.rename(tmp_cp, checkpoint_path)

    freshness_sec = None
    if max_event_time:
        dt_max = parse_iso(max_event_time)
        if dt_max:
            now_dt2 = datetime.now(timezone.utc)
            freshness_sec = (now_dt2 - dt_max).total_seconds()

    metrics_data = {
        "mode": "batch",
        "version": 2,
        "last_batch_time": checkpoint_data["last_batch_time"],
        "total_events": total_events,
        "total_files_processed": len(per_file_stats),
        "total_invalid": total_invalid_global,
        "total_future": total_future,
        "total_outlier": total_outlier,
        "warehouse_db": warehouse_db,
        "max_event_time": max_event_time,
        "min_event_time": min_event_time,
        "freshness_sec": freshness_sec,
        "manifest_count": manifest_count,
        "user_stats_count": user_stats_count,
        "daily_top_users_count": top_users_count,
        "hourly_sales_count": hourly_count,
        "sessions_count": sessions_count,
        "fraud_alerts_count": fraud_count,
        "archived_files": len(archived)
    }
    tmp_m = metrics_path + ".tmp"
    with open(tmp_m, 'w') as out:
        json.dump(metrics_data, out, indent=2)
    os.rename(tmp_m, metrics_path)

    print(f"Processed {len(per_file_stats)} files, {total_events} events, {total_invalid_global} invalid, {user_stats_count} users, {daily_count} daily, {top_users_count} top_users, {hourly_count} hourly, {sessions_count} sessions, {fraud_count} fraud, archived {len(archived)}")

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
archive_dir: /app/data/archive
manifest_db_table: ingestion_manifest
batch:
  dedup_strategy: latest_event_time
stream:
  poll_interval_ms: 200
  freshness_sla_sec: 2
  offsets_file: /app/state/stream_offsets.json
YAML
echo "Batch ingestor v4 extra hard installed"
