#!/bin/bash
set -e

cat > /app/stream_ingest.py << 'PYEOF'
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import glob
import sqlite3
import time
import signal
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
    "stream": {
        "poll_interval_ms": 200,
        "freshness_sla_sec": 2,
        "offsets_file": "/app/state/stream_offsets.json"
    }
}

RUNNING = True
START_TIME = time.time()

def handle_signal(signum, frame):
    global RUNNING
    print(f"Received signal {signum}, shutting down...", file=sys.stderr)
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
                    for k in ["incoming_dir", "warehouse_db", "checkpoint_dir", "metrics_path", "mode"]:
                        if k in data:
                            cfg[k] = data[k]
                    if "stream" in data and isinstance(data["stream"], dict):
                        for sk, sv in data["stream"].items():
                            cfg["stream"][sk] = sv
        except Exception as e:
            print(f"Warning: could not read config {path}: {e}", file=sys.stderr)
    return cfg

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        return last_offset_bytes, 0, None
    if file_size < last_offset_bytes:
        last_offset_bytes = 0
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
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"Warning: invalid JSON in {filepath}: {line[:100]}", file=sys.stderr)
                continue
            try:
                event_id = str(obj['event_id'])
                user_id = str(obj['user_id'])
                event_type = str(obj['event_type'])
                amount = float(obj['amount'])
                event_time = str(obj['event_time'])
            except (KeyError, ValueError, TypeError) as e:
                print(f"Warning: invalid fields in {filepath}: {e}", file=sys.stderr)
                continue
            if event_type not in ('purchase','view','cart'):
                continue
            if amount < 0:
                continue
            dt = parse_iso(event_time)
            if dt is None:
                continue
            existing = existing_times.get(event_id)
            if existing:
                _, existing_dt = existing
                if existing_dt and dt < existing_dt:
                    continue
            processed_at = now_iso()
            conn.execute("""
                INSERT OR REPLACE INTO events (event_id, user_id, event_type, amount, event_time, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (event_id, user_id, event_type, amount, event_time, processed_at))
            existing_times[event_id] = (event_time, dt)
            processed_count += 1
            if max_event_time is None or (dt and (max_event_time[1] is None or dt > max_event_time[1])):
                max_event_time = (event_time, dt)
        new_offset = last_offset_bytes + bytes_to_advance
    return new_offset, processed_count, max_event_time

def recompute_daily_sales(conn):
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
    pattern = os.path.join(incoming_dir, 'events_*.jsonl')
    files = sorted(glob.glob(pattern))
    conn = sqlite3.connect(warehouse_db)
    ensure_tables(conn)
    existing_times = get_existing_event_times(conn)
    offsets = load_offsets(offsets_file)
    total_new = 0
    latest_max_time = None
    for fp in files:
        fname = os.path.basename(fp)
        prev = offsets.get(fname, {})
        last_offset = prev.get('offset', 0) if isinstance(prev, dict) else 0
        new_offset, count, max_time = process_new_lines_for_file(fp, conn, existing_times, last_offset, is_new_file=(fname not in offsets))
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
                            continue
                        if event_type not in ('purchase','view','cart'):
                            continue
                        dt=parse_iso(event_time)
                        if not dt:
                            continue
                        existing = existing_times.get(event_id)
                        if existing:
                            _, existing_dt = existing
                            if existing_dt and dt < existing_dt:
                                continue
                        conn.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                                     (event_id, user_id, event_type, amount, event_time, now_iso()))
                        existing_times[event_id]=(event_time, dt)
                        total_new+=1
                        if latest_max_time is None or dt > latest_max_time[1]:
                            latest_max_time = (event_time, dt)
                new_offset = actual_size
        except Exception as e:
            print(f"backfill extra processing error {fp}: {e}", file=sys.stderr)
        conn.commit()
        try:
            stat = os.stat(fp)
            offsets[fname] = {"offset": new_offset, "lines": 0, "mtime": stat.st_mtime, "size": stat.st_size}
        except OSError:
            offsets[fname] = {"offset": new_offset, "lines": 0, "mtime": time.time(), "size": new_offset}
        total_new += count
        if max_time and (latest_max_time is None or (max_time[1] and latest_max_time[1] and max_time[1] > latest_max_time[1])):
            latest_max_time = max_time
        if count > 0:
            with open("/app/logs/stream.log", "a") as logf:
                logf.write(f"{now_iso()} backfill {fname} {count} events offset {new_offset}\n")
    recompute_daily_sales(conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    total_events = cur.fetchone()[0]
    cur.execute("SELECT MAX(event_time) FROM events")
    max_et_row = cur.fetchone()
    max_event_time_str = max_et_row[0] if max_et_row else (latest_max_time[0] if latest_max_time else None)
    conn.close()
    write_offsets(offsets_file, offsets)
    write_metrics(metrics_path, total_events, len(files), max_event_time_str, last_delay_ms=0, warehouse_db=warehouse_db)
    print(f"Backfill done: {len(files)} files, total_events={total_events}, new_processed={total_new}")
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
        print(f"Warning: could not write pid file {pid_path}: {e}", file=sys.stderr)
    conn = sqlite3.connect(warehouse_db, check_same_thread=False, timeout=10.0)
    ensure_tables(conn)
    existing_times = get_existing_event_times(conn)
    offsets = load_offsets(offsets_file)
    print(f"Starting streaming loop poll_interval={poll_ms}ms incoming={incoming_dir}")
    try:
        while RUNNING:
            loop_start = time.time()
            pattern = os.path.join(incoming_dir, 'events_*.jsonl')
            files = sorted(glob.glob(pattern))
            any_new = 0
            latest_delay_ms = None
            latest_max = None
            for fp in files:
                fname = os.path.basename(fp)
                prev = offsets.get(fname, {})
                last_offset = prev.get('offset', 0) if isinstance(prev, dict) else 0
                try:
                    stat = os.stat(fp)
                    curr_size = stat.st_size
                    curr_mtime = stat.st_mtime
                except OSError:
                    continue
                if fname not in offsets:
                    detection_delay = time.time() - curr_mtime
                    latest_delay_ms = int(detection_delay * 1000) if latest_delay_ms is None else min(latest_delay_ms, int(detection_delay*1000))
                else:
                    if curr_size < last_offset:
                        last_offset = 0
                    elif curr_size == last_offset:
                        continue
                new_offset, count, max_time = process_new_lines_for_file(fp, conn, existing_times, last_offset, is_new_file=(fname not in offsets))
                if count > 0:
                    conn.commit()
                    any_new += count
                    try:
                        with open("/app/logs/stream.log", "a") as logf:
                            logf.write(f"{now_iso()} stream {fname} +{count} events offset {last_offset}->{new_offset}\n")
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
                offsets[fname] = {"offset": new_offset, "mtime": curr_mtime, "size": curr_size, "lines": 0}
            if any_new > 0:
                recompute_daily_sales(conn)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM events")
                total_events = cur.fetchone()[0]
                cur.execute("SELECT MAX(event_time) FROM events")
                row = cur.fetchone()
                max_et = row[0] if row else (latest_max[0] if latest_max else None)
                write_offsets(offsets_file, offsets)
                write_metrics(metrics_path, total_events, len(files), max_et, last_delay_ms=latest_delay_ms if latest_delay_ms is not None else 0, warehouse_db=warehouse_db)
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
                        write_metrics(metrics_path, total_events, len(files), max_et, last_delay_ms=0, warehouse_db=warehouse_db)
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
    if 'stream' not in data or not isinstance(data['stream'], dict):
        data['stream'] = {}
    data['stream']['poll_interval_ms'] = data['stream'].get('poll_interval_ms', 200)
    data['stream']['freshness_sla_sec'] = 2
    data['stream']['offsets_file'] = data['stream'].get('offsets_file', '/app/state/stream_offsets.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as out:
        if yaml:
            yaml.safe_dump(data, out, sort_keys=False)
        else:
            out.write(f"mode: {data['mode']}\n")
            out.write(f"incoming_dir: {data['incoming_dir']}\n")
            out.write(f"warehouse_db: {data['warehouse_db']}\n")
            out.write(f"checkpoint_dir: {data['checkpoint_dir']}\n")
            out.write(f"metrics_path: {data['metrics_path']}\n")
            out.write("stream:\n")
            out.write(f"  poll_interval_ms: {data['stream']['poll_interval_ms']}\n")
            out.write(f"  freshness_sla_sec: {data['stream']['freshness_sla_sec']}\n")
            out.write(f"  offsets_file: {data['stream']['offsets_file']}\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/app/config.yaml')
    parser.add_argument('--once', action='store_true', help='backfill only and exit')
    args = parser.parse_args()
    cfg = load_config(args.config)
    os.makedirs(cfg['incoming_dir'], exist_ok=True)
    os.makedirs(cfg['checkpoint_dir'], exist_ok=True)
    os.makedirs(os.path.dirname(cfg['metrics_path']), exist_ok=True)
    os.makedirs(os.path.dirname(cfg['stream']['offsets_file']), exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)
    os.makedirs(os.path.dirname(cfg['warehouse_db']), exist_ok=True)
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
stream:
  poll_interval_ms: 200
  freshness_sla_sec: 2
  offsets_file: /app/state/stream_offsets.json
batch:
  dedup_strategy: latest_event_time
YAML

echo "Stream ingestor oracle installed"
echo "Config updated to stream mode"
