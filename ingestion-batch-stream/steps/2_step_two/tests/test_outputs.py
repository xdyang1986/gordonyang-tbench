"""
Tests for Step 2 - Streaming ingestion with freshness SLA
"""

import os
import json
import glob
import sqlite3
import subprocess
import time
import signal
from datetime import datetime, timezone

INCOMING = "/app/data/incoming"
DB = "/app/warehouse.db"
CHECKPOINT = "/app/state/checkpoint.json"
METRICS = "/app/metrics/freshness.json"
OFFSETS = "/app/state/stream_offsets.json"
BATCH_SCRIPT = "/app/batch_ingest.py"
STREAM_SCRIPT = "/app/stream_ingest.py"
CONFIG = "/app/config.yaml"
PID_FILE = "/app/stream.pid"
LOG_FILE = "/app/logs/stream.log"


def clean_for_stream_test(keep_scripts=True):
    for p in [DB, METRICS, OFFSETS, PID_FILE]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass
    os.makedirs(INCOMING, exist_ok=True)
    for f in glob.glob(os.path.join(INCOMING, "events_*.jsonl")):
        os.remove(f)
    os.makedirs("/app/state", exist_ok=True)
    os.makedirs("/app/metrics", exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)
    if os.path.exists(LOG_FILE):
        try:
            os.remove(LOG_FILE)
        except:
            pass


def write_events(filename, events):
    path = os.path.join(INCOMING, filename)
    with open(path, "w") as out:
        for e in events:
            if isinstance(e, str):
                out.write(e + "\n")
            else:
                out.write(json.dumps(e) + "\n")


def append_events(filename, events):
    path = os.path.join(INCOMING, filename)
    with open(path, "a") as out:
        for e in events:
            if isinstance(e, str):
                out.write(e + "\n")
            else:
                out.write(json.dumps(e) + "\n")


def get_py():
    import sys

    return sys.executable


def run_cmd(cmd, timeout=30):
    import sys

    py = sys.executable
    cmd = cmd.replace("python ", py + " ")
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result


def query_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM events ORDER BY event_id")
    events = cur.fetchall()
    cur.execute("SELECT * FROM daily_sales ORDER BY date")
    sales = cur.fetchall()
    conn.close()
    return list(events), list(sales)


def test_streaming_script_exists():
    assert os.path.exists(STREAM_SCRIPT), (
        "stream_ingest.py must exist at /app/stream_ingest.py"
    )
    assert os.path.exists(BATCH_SCRIPT), "batch_ingest.py from step1 must still exist"


def test_config_mode_stream():
    assert os.path.exists(CONFIG), "config.yaml must exist"
    with open(CONFIG) as f:
        content = f.read()
    import yaml

    data = yaml.safe_load(content)
    assert data.get("mode") == "stream", f"mode must be stream, got {data.get('mode')}"
    assert "stream" in data
    assert data["stream"].get("freshness_sla_sec") == 2
    assert data["stream"].get("poll_interval_ms", 200) <= 500


def test_once_backfill():
    clean_for_stream_test()
    write_events(
        "events_001.jsonl",
        [
            {
                "event_id": "h1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T01:00:00Z",
            },
            {
                "event_id": "h2",
                "user_id": "u2",
                "event_type": "view",
                "amount": 0,
                "event_time": "2024-01-01T02:00:00Z",
            },
        ],
    )
    write_events(
        "events_002.jsonl",
        [
            {
                "event_id": "h3",
                "user_id": "u3",
                "event_type": "purchase",
                "amount": 20,
                "event_time": "2024-01-02T01:00:00Z",
            },
            {
                "event_id": "h1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 15,
                "event_time": "2024-01-01T03:00:00Z",
            },
        ],
    )
    result = run_cmd(f"python {STREAM_SCRIPT} --once", timeout=15)
    assert result.returncode == 0, (
        f"stream --once failed: {result.stdout} {result.stderr}"
    )
    assert os.path.exists(DB)
    events, sales = query_db()
    assert len(events) == 3
    ev_map = {e["event_id"]: e for e in events}
    assert ev_map["h1"]["amount"] == 15.0
    assert os.path.exists(OFFSETS)
    with open(OFFSETS) as f:
        off = json.load(f)
    assert "events_001.jsonl" in off and "events_002.jsonl" in off
    assert os.path.exists(METRICS)
    with open(METRICS) as f:
        m = json.load(f)
    assert m.get("mode") == "stream"
    assert m.get("streaming") == True
    assert m.get("total_events") == 3


def test_streaming_freshness_sla():
    clean_for_stream_test()
    write_events(
        "events_hist.jsonl",
        [
            {
                "event_id": "hist1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 5,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ],
    )
    result = run_cmd(f"python {STREAM_SCRIPT} --once", timeout=15)
    assert result.returncode == 0
    events, _ = query_db()
    assert len(events) == 1
    proc = subprocess.Popen(
        [get_py(), STREAM_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    time.sleep(2.5)
    assert os.path.exists(DB)
    assert proc.poll() is None
    fresh_events = [
        {
            "event_id": "fresh1",
            "user_id": "u99",
            "event_type": "purchase",
            "amount": 99.9,
            "event_time": "2024-06-01T12:00:01Z",
        },
        {
            "event_id": "fresh2",
            "user_id": "u100",
            "event_type": "view",
            "amount": 0,
            "event_time": "2024-06-01T12:00:02Z",
        },
    ]
    write_events("events_fresh_001.jsonl", fresh_events)
    time.sleep(3.5)
    events, _ = query_db()
    ev_ids = set(e["event_id"] for e in events)
    assert "fresh1" in ev_ids
    assert "fresh2" in ev_ids
    assert os.path.exists(METRICS)
    with open(METRICS) as f:
        m = json.load(f)
    assert m.get("mode") == "stream"
    assert m.get("streaming") == True
    assert m.get("total_events") >= 3
    last_proc_str = m.get("last_processed_time")
    assert last_proc_str is not None
    try:
        dt = datetime.strptime(last_proc_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        delta = (now - dt).total_seconds()
        assert delta < 10
    except Exception:
        pass
    delay = m.get("last_event_delay_ms", 0)
    assert delay < 5000
    append_events(
        "events_fresh_001.jsonl",
        [
            {
                "event_id": "fresh3",
                "user_id": "u101",
                "event_type": "purchase",
                "amount": 50,
                "event_time": "2024-06-01T12:00:10Z",
            },
        ],
    )
    time.sleep(3)
    events, _ = query_db()
    ev_ids = set(e["event_id"] for e in events)
    assert "fresh3" in ev_ids
    assert os.path.exists(LOG_FILE)
    with open(LOG_FILE) as lf:
        assert len(lf.read()) > 0
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except:
            pass
        proc.wait(timeout=2)
    assert os.path.exists(OFFSETS)
    with open(OFFSETS) as f:
        off = json.load(f)
    assert "events_fresh_001.jsonl" in off


def test_crash_resilience_restart():
    events_before, _ = query_db()
    count_before = len(events_before)
    result = run_cmd(f"python {STREAM_SCRIPT} --once", timeout=15)
    assert result.returncode == 0
    events_after, _ = query_db()
    count_after = len(events_after)
    assert count_after == count_before
    assert os.path.exists(OFFSETS)


def test_dedup_in_streaming():
    clean_for_stream_test()
    write_events(
        "events_dedup.jsonl",
        [
            {
                "event_id": "dup1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T01:00:00Z",
            },
        ],
    )
    result = run_cmd(f"python {STREAM_SCRIPT} --once", timeout=10)
    assert result.returncode == 0
    proc = subprocess.Popen(
        [get_py(), STREAM_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    time.sleep(2)
    append_events(
        "events_dedup.jsonl",
        [
            {
                "event_id": "dup1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 999,
                "event_time": "2023-12-31T00:00:00Z",
            },
        ],
    )
    time.sleep(2.5)
    events, _ = query_db()
    ev_map = {e["event_id"]: e for e in events}
    assert ev_map["dup1"]["amount"] == 10.0
    append_events(
        "events_dedup.jsonl",
        [
            {
                "event_id": "dup1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 20,
                "event_time": "2024-01-02T00:00:00Z",
            },
        ],
    )
    time.sleep(2.5)
    events, _ = query_db()
    ev_map = {e["event_id"]: e for e in events}
    assert ev_map["dup1"]["amount"] == 20.0
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except:
            pass
