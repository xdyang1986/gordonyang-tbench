#!/bin/bash
set -e

cat > /app/stream_ingest.py << 'PYEOF'
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import re
import glob
import hashlib
import sqlite3
import time
import signal
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
    "stream": {
        "poll_interval_ms": 200,
        "freshness_sla_sec": 2,
        "offsets_file": "/app/state/stream_offsets.json"
    }
}

VALID_FILENAME_RE = re.compile(r'^events_[A-Za-z0-9_.\-]+\.jsonl$')

RUNNING = True
START_TIME = time.time()

def handle_signal(signum, frame):
    global RUNNING
    RUNNING = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def load_config(path="/app/config.yaml"):
    cfg = dict(DEFAULT_CONFIG)
    cfg["stream"] = dict(DEFAULT_CONFIG["stream"])
    if os.path.exists(path):
        try:
            if yaml:
                with open(path, 'r') as f:
                    data = yaml.safe_load(f) or {}
                    for k in ["incoming_dir", "warehouse_db", "checkpoint_dir", "metrics_path", "dead_letter_path", "archive_dir", "mode"]:
                        if k in data:
                            cfg[k] = data[k]
                    if "stream" in data and isinstance(data["stream"], dict):
                        for sk, sv in data["stream"].items():
                            cfg["stream"][sk] = sv
        except Exception as e:
            print(f"Warning: config {e}", file=sys.stderr)
    return cfg

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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

def get_existing_event_times(conn):
    cur = conn.cursor()
    cur.execute("SELECT event_id, event_time FROM events")
    m = {}
    for row in cur.fetchall():
        dt = parse_iso(row[1])
        m[row[0]] = (row[1], dt)
    return m

def write_metrics(metrics_path, total_events, total_files, max_event_time, last_delay_ms, warehouse_db):
    os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
    sla_sec = 2
    is_meeting = True
    if last_delay_ms is not None:
        is_meeting = last_delay_ms <= (sla_sec * 1000 + 500)
    manifest_count = user_stats_count = 0
    daily_top = hourly_cnt = sessions_cnt = fraud_cnt = 0
    min_event_time = None
    try:
        conn = sqlite3.connect(warehouse_db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ingestion_manifest")
        manifest_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM user_stats")
        user_stats_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM daily_top_users")
        daily_top = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM hourly_sales")
        hourly_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sessions")
        sessions_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM fraud_alerts")
        fraud_cnt = cur.fetchone()[0]
        cur.execute("SELECT MIN(event_time) FROM events")
        min_event_time = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass
    data = {
        "mode": "stream",
        "last_processed_time": now_iso(),
        "total_events": total_events,
        "total_files_processed": total_files,
        "last_event_delay_ms": last_delay_ms if last_delay_ms is not None else 0,
        "freshness_sla_sec": sla_sec,
        "is_meeting_sla": is_meeting,
        "streaming": True,
        "warehouse_db": warehouse_db,
        "max_event_time": max_event_time,
        "min_event_time": min_event_time,
        "manifest_count": manifest_count,
        "user_stats_count": user_stats_count,
        "daily_top_users_count": daily_top,
        "hourly_sales_count": hourly_cnt,
        "sessions_count": sessions_cnt,
        "fraud_alerts_count": fraud_cnt,
        "uptime_sec": time.time() - START_TIME
    }
    tmp = metrics_path + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.rename(tmp, metrics_path)

def write_offsets(offsets_path, offsets):
    os.makedirs(os.path.dirname(offsets_path), exist_ok=True)
    tmp = offsets_path + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(offsets, f, indent=2)
    os.rename(tmp, offsets_path)

def load_offsets(offsets_path):
    if os.path.exists(offsets_path):
        try:
            with open(offsets_path, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def process_new_lines_for_file(filepath, conn, existing_times, last_offset_bytes, is_new_file):
    processed_count = 0
    max_event_time = None
    new_offset = last_offset_bytes
    try:
        file_size = os.path.getsize(filepath)
    except OSError:
        return last_offset_bytes, 0, None, 0, 0
    if file_size < last_offset_bytes:
        last_offset_bytes = 0
    num_valid_file = 0
    num_invalid_file = 0
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        f.seek(last_offset_bytes)
        remaining = f.read()
        ends_with_newline = remaining.endswith('\n') or remaining.endswith('\r\n') or remaining.endswith('\r') or remaining == ''
        lines = remaining.splitlines()
        processable_lines = lines
        bytes_to_advance = len(remaining.encode('utf-8', errors='ignore'))
        if not ends_with_newline and lines:
            last_newline_idx = remaining.rfind('\n')
            if last_newline_idx != -1:
                bytes_to_advance = len(remaining[:last_newline_idx+1].encode('utf-8'))
                processable_lines = remaining[:last_newline_idx+1].splitlines()
            else:
                bytes_to_advance = 0
                processable_lines = []
        for line in processable_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                num_invalid_file += 1
                continue
            try:
                event_id = str(obj['event_id'])
                user_id = str(obj['user_id'])
                event_type = str(obj['event_type'])
                amount = float(obj['amount'])
                event_time = str(obj['event_time'])
            except (KeyError, ValueError, TypeError):
                num_invalid_file += 1
                continue
            if event_type not in ('purchase','view','cart'):
                num_invalid_file += 1
                continue
            if amount < 0 or amount > 100000:
                num_invalid_file += 1
                continue
            dt = parse_iso(event_time)
            if dt is None:
                num_invalid_file += 1
                continue
            # future check
            if dt > datetime.now(timezone.utc) + timedelta(hours=1):
                num_invalid_file += 1
                continue
            existing = existing_times.get(event_id)
            if existing:
                _, existing_dt = existing
                if existing_dt and dt < existing_dt:
                    num_valid_file += 1
                    continue
            processed_at = now_iso()
            conn.execute("""
                INSERT OR REPLACE INTO events (event_id, user_id, event_type, amount, event_time, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (event_id, user_id, event_type, amount, event_time, processed_at))
            existing_times[event_id] = (event_time, dt)
            processed_count += 1
            num_valid_file += 1
            if max_event_time is None or (dt and (max_event_time[1] is None or dt > max_event_time[1])):
                max_event_time = (event_time, dt)
        new_offset = last_offset_bytes + bytes_to_advance
    return new_offset, processed_count, max_event_time, num_valid_file, num_invalid_file

def recompute_aggregates(conn):
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
            conn.execute("INSERT INTO daily_top_users (date, user_id, total_amount) VALUES (?, ?, ?)", (date, user_id, total))
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
    user_events = defaultdict(list)
    for user_id, event_id, event_time, event_type, amount in events_rows:
        dt = parse_iso(event_time)
        if dt is None:
            continue
        user_events[user_id].append((dt, event_time, event_id, event_type, amount))
    for user_id, ev_list in user_events.items():
        ev_list_sorted = sorted(ev_list, key=lambda x: x[0])
        idx = 0
        while idx < len(ev_list_sorted):
            start_dt, start_str, _, _, _ = ev_list_sorted[idx]
            sess_events = [ev_list_sorted[idx]]
            last_dt = start_dt
            nxt = idx + 1
            while nxt < len(ev_list_sorted):
                cur_dt, _, _, _, _ = ev_list_sorted[nxt]
                if (cur_dt - last_dt).total_seconds() > 1800:
                    break
                sess_events.append(ev_list_sorted[nxt])
                last_dt = cur_dt
                nxt += 1
            end_time = sess_events[-1][1]
            duration = int((sess_events[-1][0] - sess_events[0][0]).total_seconds())
            total_amount = sum(ev[4] for ev in sess_events if ev[3] == 'purchase')
            session_id = f"{user_id}_{sess_events[0][1]}"
            base = session_id
            suffix = 0
            while True:
                try:
                    conn.execute("INSERT INTO sessions (session_id, user_id, start_time, end_time, event_count, total_amount, duration_sec) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (session_id, user_id, sess_events[0][1], end_time, len(sess_events), total_amount, duration))
                    break
                except sqlite3.IntegrityError:
                    suffix += 1
                    session_id = f"{base}_{suffix}"
                    if suffix > 10:
                        break
            idx = nxt
    # fraud_alerts
    conn.execute("DELETE FROM fraud_alerts")
    for user_id, ev_list in user_events.items():
        purchases = [(dt, et_str, amt) for dt, et_str, _, et_type, amt in ev_list if et_type == 'purchase']
        if len(purchases) <= 5:
            continue
        purchases_sorted = sorted(purchases, key=lambda x: x[0])
        left = 0
        best = {}
        for right in range(len(purchases_sorted)):
            while purchases_sorted[right][0] - purchases_sorted[left][0] > timedelta(seconds=3600):
                left += 1
            cnt = right - left + 1
            if cnt > 5:
                ws_str = purchases_sorted[left][1]
                we_str = purchases_sorted[right][1]
                total_amt = sum(p[2] for p in purchases_sorted[left:right+1])
                existing = best.get(ws_str)
                if existing is None or cnt > existing[0] or (cnt == existing[0] and total_amt > existing[1]):
                    best[ws_str] = (cnt, total_amt, we_str)
        for ws_str, (cnt, total_amt, we_str) in best.items():
            conn.execute("INSERT INTO fraud_alerts (user_id, window_start, window_end, purchase_count, total_amount) VALUES (?, ?, ?, ?, ?)",
                         (user_id, ws_str, we_str, cnt, total_amt))
    conn.commit()

def list_valid_files(incoming_dir):
    try:
        all_files = os.listdir(incoming_dir)
    except OSError:
        return []
    valid = []
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
        valid.append(fn)
    return sorted(valid)

def backfill_once(cfg):
    incoming_dir = cfg['incoming_dir']
    warehouse_db = cfg['warehouse_db']
    metrics_path = cfg['metrics_path']
    offsets_file = cfg['stream']['offsets_file']
    os.makedirs(incoming_dir, exist_ok=True)
    os.makedirs(os.path.dirname(warehouse_db), exist_ok=True)
    os.makedirs(os.path.dirname(offsets_file), exist_ok=True)
    os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)
    valid_files = list_valid_files(incoming_dir)
    conn = sqlite3.connect(warehouse_db)
    ensure_tables(conn)
    existing_times = get_existing_event_times(conn)
    offsets = load_offsets(offsets_file)
    total_new = 0
    latest_max_time = None
    for fn in valid_files:
        fp = os.path.join(incoming_dir, fn)
        try:
            with open(fp, 'rb') as hf:
                file_hash = hashlib.sha256(hf.read()).hexdigest()
        except Exception:
            file_hash = ""
        prev = offsets.get(fn, {})
        last_offset = prev.get('offset', 0) if isinstance(prev, dict) else 0
        new_offset, count, max_time, num_valid, num_invalid = process_new_lines_for_file(fp, conn, existing_times, last_offset, is_new_file=(fn not in offsets))
        try:
            actual_size = os.path.getsize(fp)
            if new_offset < actual_size:
                with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(new_offset)
                    remaining = f.read()
                    for line in remaining.splitlines():
                        line=line.strip()
                        if not line:
                            continue
                        try:
                            obj=json.loads(line)
                            event_id=str(obj['event_id'])
                            user_id=str(obj['user_id'])
                            event_type=str(obj['event_type'])
                            amount=float(obj['amount'])
                            event_time=str(obj['event_time'])
                        except Exception:
                            num_invalid+=1
                            continue
                        if event_type not in ('purchase','view','cart'):
                            num_invalid+=1
                            continue
                        if amount <0 or amount>100000:
                            num_invalid+=1
                            continue
                        dt=parse_iso(event_time)
                        if not dt:
                            num_invalid+=1
                            continue
                        if dt > datetime.now(timezone.utc) + timedelta(hours=1):
                            num_invalid+=1
                            continue
                        existing = existing_times.get(event_id)
                        if existing:
                            _, existing_dt = existing
                            if existing_dt and dt < existing_dt:
                                num_valid+=1
                                continue
                        conn.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                                     (event_id, user_id, event_type, amount, event_time, now_iso()))
                        existing_times[event_id]=(event_time, dt)
                        count+=1
                        num_valid+=1
                        if latest_max_time is None or dt > latest_max_time[1]:
                            latest_max_time = (event_time, dt)
                new_offset = actual_size
        except Exception as e:
            print(f"backfill extra error {fp}: {e}", file=sys.stderr)
        conn.commit()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO ingestion_manifest (file_name, file_hash, file_size, num_lines, num_valid, num_invalid, num_future, num_outlier, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (fn, file_hash, os.path.getsize(fp), 0, num_valid, num_invalid, 0, 0, now_iso()))
            conn.commit()
        except Exception:
            pass
        try:
            stat = os.stat(fp)
            offsets[fn] = {"offset": new_offset, "lines": 0, "mtime": stat.st_mtime, "size": stat.st_size, "hash": file_hash}
        except OSError:
            offsets[fn] = {"offset": new_offset, "lines": 0, "mtime": time.time(), "size": new_offset, "hash": file_hash}
        total_new += count
        if max_time and (latest_max_time is None or (max_time[1] and latest_max_time[1] and max_time[1] > latest_max_time[1])):
            latest_max_time = max_time
        if count > 0:
            with open("/app/logs/stream.log", "a") as logf:
                logf.write(f"{now_iso()} backfill {fn} {count} events offset {new_offset}\n")
    recompute_aggregates(conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    total_events = cur.fetchone()[0]
    cur.execute("SELECT MAX(event_time) FROM events")
    max_et_row = cur.fetchone()
    max_event_time_str = max_et_row[0] if max_et_row else (latest_max_time[0] if latest_max_time else None)
    conn.close()
    write_offsets(offsets_file, offsets)
    write_metrics(metrics_path, total_events, len(valid_files), max_event_time_str, last_delay_ms=0, warehouse_db=warehouse_db)
    print(f"Backfill done: {len(valid_files)} files, total_events={total_events}, new_processed={total_new}")
    return total_events

def stream_loop(cfg):
    incoming_dir = cfg['incoming_dir']
    warehouse_db = cfg['warehouse_db']
    metrics_path = cfg['metrics_path']
    offsets_file = cfg['stream']['offsets_file']
    poll_ms = cfg['stream'].get('poll_interval_ms', 200)
    poll_sec = poll_ms / 1000.0
    pid_path = "/app/stream.pid"
    try:
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"Warning: pid {e}", file=sys.stderr)
    conn = sqlite3.connect(warehouse_db, check_same_thread=False, timeout=10.0)
    ensure_tables(conn)
    existing_times = get_existing_event_times(conn)
    offsets = load_offsets(offsets_file)
    print(f"Starting streaming loop poll_interval={poll_ms}ms incoming={incoming_dir}")
    try:
        while RUNNING:
            loop_start = time.time()
            valid_files = list_valid_files(incoming_dir)
            any_new = 0
            latest_delay_ms = None
            latest_max = None
            for fn in valid_files:
                fp = os.path.join(incoming_dir, fn)
                prev = offsets.get(fn, {})
                last_offset = prev.get('offset', 0) if isinstance(prev, dict) else 0
                try:
                    stat = os.stat(fp)
                    curr_size = stat.st_size
                    curr_mtime = stat.st_mtime
                    with open(fp, 'rb') as hf:
                        file_hash = hashlib.sha256(hf.read()).hexdigest()
                except OSError:
                    continue
                if fn not in offsets:
                    detection_delay = time.time() - curr_mtime
                    latest_delay_ms = int(detection_delay * 1000) if latest_delay_ms is None else min(latest_delay_ms, int(detection_delay*1000))
                else:
                    if curr_size < last_offset:
                        last_offset = 0
                    elif curr_size == last_offset:
                        continue
                new_offset, count, max_time, num_valid, num_invalid = process_new_lines_for_file(fp, conn, existing_times, last_offset, is_new_file=(fn not in offsets))
                if count > 0 or num_valid > 0:
                    conn.commit()
                    any_new += count
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO ingestion_manifest (file_name, file_hash, file_size, num_lines, num_valid, num_invalid, num_future, num_outlier, processed_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (fn, file_hash, curr_size, 0, num_valid, num_invalid, 0, 0, now_iso()))
                        conn.commit()
                    except Exception:
                        pass
                    try:
                        with open("/app/logs/stream.log", "a") as logf:
                            logf.write(f"{now_iso()} stream {fn} +{count} events offset {last_offset}->{new_offset}\n")
                    except Exception:
                        pass
                    detection_time = time.time()
                    delay_ms = int((detection_time - curr_mtime) * 1000)
                    if delay_ms < 0:
                        delay_ms = 0
                    if latest_delay_ms is None or delay_ms < latest_delay_ms:
                        latest_delay_ms = delay_ms
                    if max_time:
                        if latest_max is None or max_time[1] > latest_max[1]:
                            latest_max = max_time
                offsets[fn] = {"offset": new_offset, "mtime": curr_mtime, "size": curr_size, "lines": 0, "hash": file_hash}
            if any_new > 0:
                recompute_aggregates(conn)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM events")
                total_events = cur.fetchone()[0]
                cur.execute("SELECT MAX(event_time) FROM events")
                row = cur.fetchone()
                max_et = row[0] if row else (latest_max[0] if latest_max else None)
                write_offsets(offsets_file, offsets)
                write_metrics(metrics_path, total_events, len(valid_files), max_et, last_delay_ms=latest_delay_ms if latest_delay_ms is not None else 0, warehouse_db=warehouse_db)
            else:
                try:
                    mtime = os.path.getmtime(metrics_path)
                    if time.time() - mtime > 1.0:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM events")
                        total_events = cur.fetchone()[0]
                        cur.execute("SELECT MAX(event_time) FROM events")
                        row = cur.fetchone()
                        max_et = row[0] if row else None
                        write_metrics(metrics_path, total_events, len(valid_files), max_et, last_delay_ms=0, warehouse_db=warehouse_db)
                except Exception:
                    pass
            elapsed = time.time() - loop_start
            to_sleep = poll_sec - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)
    except Exception as e:
        print(f"Streaming loop error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        try:
            conn.commit()
            conn.close()
        except Exception:
            pass
        try:
            write_offsets(offsets_file, offsets)
        except Exception:
            pass
        try:
            if os.path.exists(pid_path):
                os.remove(pid_path)
        except Exception:
            pass
        print("Streaming shutdown complete", file=sys.stderr)

def ensure_config_updated():
    config_path = "/app/config.yaml"
    data = {}
    if os.path.exists(config_path) and yaml:
        try:
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}
    data['mode'] = 'stream'
    if 'incoming_dir' not in data:
        data['incoming_dir'] = '/app/data/incoming'
    if 'warehouse_db' not in data:
        data['warehouse_db'] = '/app/warehouse.db'
    if 'checkpoint_dir' not in data:
        data['checkpoint_dir'] = '/app/state'
    if 'metrics_path' not in data:
        data['metrics_path'] = '/app/metrics/freshness.json'
    if 'dead_letter_path' not in data:
        data['dead_letter_path'] = '/app/state/dead_letter.jsonl'
    if 'archive_dir' not in data:
        data['archive_dir'] = '/app/data/archive'
    if 'stream' not in data or not isinstance(data['stream'], dict):
        data['stream'] = {}
    data['stream']['poll_interval_ms'] = data['stream'].get('poll_interval_ms', 200)
    data['stream']['freshness_sla_sec'] = 2
    data['stream']['offsets_file'] = data['stream'].get('offsets_file', '/app/state/stream_offsets.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as out:
        if yaml:
            yaml.safe_dump(data, out, sort_keys=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/app/config.yaml')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    cfg = load_config(args.config)
    os.makedirs(cfg['incoming_dir'], exist_ok=True)
    os.makedirs(cfg['checkpoint_dir'], exist_ok=True)
    os.makedirs(os.path.dirname(cfg['metrics_path']), exist_ok=True)
    os.makedirs(os.path.dirname(cfg['stream']['offsets_file']), exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)
    os.makedirs(os.path.dirname(cfg['warehouse_db']), exist_ok=True)
    os.makedirs(os.path.dirname(cfg.get('dead_letter_path', '/app/state/dead_letter.jsonl')), exist_ok=True)
    os.makedirs(cfg.get('archive_dir', '/app/data/archive'), exist_ok=True)
    ensure_config_updated()
    cfg = load_config(args.config)
    backfill_once(cfg)
    if args.once:
        print("Once mode: exiting after backfill")
        return
    stream_loop(cfg)

if __name__ == "__main__":
    main()
PYEOF

chmod +x /app/stream_ingest.py

cat > /app/config.yaml << 'YAML'
mode: stream
incoming_dir: /app/data/incoming
warehouse_db: /app/warehouse.db
checkpoint_dir: /app/state
metrics_path: /app/metrics/freshness.json
dead_letter_path: /app/state/dead_letter.jsonl
archive_dir: /app/data/archive
stream:
  poll_interval_ms: 200
  freshness_sla_sec: 2
  offsets_file: /app/state/stream_offsets.json
batch:
  dedup_strategy: latest_event_time
YAML

echo "Stream ingestor v4 extra hard installed"
