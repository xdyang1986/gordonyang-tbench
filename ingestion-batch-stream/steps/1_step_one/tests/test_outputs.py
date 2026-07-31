"""
Tests for Step 1 - Batch ingestion - Extra Hard v4 with sessions and fraud
"""

import os
import json
import glob
import hashlib
import sqlite3
import subprocess
import re
import shutil
from datetime import datetime, timezone, timedelta

INCOMING = "/app/data/incoming"
ARCHIVE = "/app/data/archive"
DB = "/app/warehouse.db"
CHECKPOINT = "/app/state/checkpoint.json"
METRICS = "/app/metrics/freshness.json"
DEAD_LETTER = "/app/state/dead_letter.jsonl"
BATCH_SCRIPT = "/app/batch_ingest.py"

VALID_RE = re.compile(r"^events_[A-Za-z0-9_.\-]+\.jsonl$")


def clean_env():
    for p in [
        DB,
        CHECKPOINT,
        METRICS,
        DEAD_LETTER,
        "/app/state/stream_offsets.json",
        "/app/stream.pid",
        "/app/logs/stream.log",
    ]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass
    os.makedirs(INCOMING, exist_ok=True)
    os.makedirs(ARCHIVE, exist_ok=True)
    for f in glob.glob(os.path.join(INCOMING, "*")):
        try:
            os.remove(f)
        except:
            pass
    for f in glob.glob(os.path.join(ARCHIVE, "*")):
        try:
            os.remove(f)
        except:
            pass
    os.makedirs("/app/state", exist_ok=True)
    os.makedirs("/app/metrics", exist_ok=True)
    os.makedirs("/app/logs", exist_ok=True)


def write_events(filename, events):
    path = os.path.join(INCOMING, filename)
    with open(path, "w") as out:
        for e in events:
            if isinstance(e, str):
                out.write(e + "\n")
            else:
                out.write(json.dumps(e) + "\n")


def write_raw(filename, content):
    path = os.path.join(INCOMING, filename)
    with open(path, "w") as out:
        out.write(content)


def run_batch(extra_args=""):
    import sys

    py = sys.executable
    cmd = [py, BATCH_SCRIPT]
    if extra_args:
        cmd += extra_args.split()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result


def query_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM events ORDER BY event_id")
    events = cur.fetchall()
    cur.execute("SELECT * FROM daily_sales ORDER BY date")
    sales = cur.fetchall()
    try:
        cur.execute("SELECT * FROM ingestion_manifest ORDER BY file_name")
        manifest = cur.fetchall()
    except sqlite3.OperationalError:
        manifest = []
    try:
        cur.execute("SELECT * FROM user_stats ORDER BY user_id")
        user_stats = cur.fetchall()
    except sqlite3.OperationalError:
        user_stats = []
    try:
        cur.execute("SELECT * FROM daily_top_users ORDER BY date, total_amount DESC")
        top_users = cur.fetchall()
    except sqlite3.OperationalError:
        top_users = []
    try:
        cur.execute("SELECT * FROM hourly_sales ORDER BY hour")
        hourly = cur.fetchall()
    except sqlite3.OperationalError:
        hourly = []
    try:
        cur.execute("SELECT * FROM sessions ORDER BY user_id, start_time")
        sessions = cur.fetchall()
    except sqlite3.OperationalError:
        sessions = []
    try:
        cur.execute("SELECT * FROM fraud_alerts ORDER BY user_id, window_start")
        fraud = cur.fetchall()
    except sqlite3.OperationalError:
        fraud = []
    conn.close()
    return (
        list(events),
        list(sales),
        list(manifest),
        list(user_stats),
        list(top_users),
        list(hourly),
        list(sessions),
        list(fraud),
    )


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_batch_file_exists():
    assert os.path.exists(BATCH_SCRIPT)


def test_no_streaming_artifacts_in_batch():
    clean_env()
    for p in [
        "/app/stream_ingest.py",
        "/app/state/stream_offsets.json",
        "/app/stream.pid",
        "/app/logs/stream.log",
    ]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass
    write_events(
        "events_negative_check.jsonl",
        [
            {
                "event_id": "neg1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 1,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    assert not os.path.exists("/app/stream_ingest.py")
    assert not os.path.exists("/app/state/stream_offsets.json")
    assert not os.path.exists("/app/stream.pid")
    if os.path.exists("/app/config.yaml"):
        with open("/app/config.yaml") as f:
            content = f.read()
        assert "mode: stream" not in content
    for f in glob.glob(os.path.join(INCOMING, "*")):
        try:
            os.remove(f)
        except:
            pass
    for f in glob.glob(os.path.join(ARCHIVE, "*")):
        try:
            os.remove(f)
        except:
            pass


def test_basic_ingestion_hard_v4():
    clean_env()
    write_events(
        "events_001.jsonl",
        [
            {
                "event_id": "e1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10.0,
                "event_time": "2024-01-01T01:00:00Z",
            },
            {
                "event_id": "e2",
                "user_id": "u2",
                "event_type": "view",
                "amount": 0,
                "event_time": "2024-01-01T02:00:00Z",
            },
            {
                "event_id": "e1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 15.0,
                "event_time": "2024-01-01T03:00:00+00:00",
            },
            "",
            '{"invalid json',
            {
                "event_id": "e3",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 20.0,
                "event_time": "2024-01-01T04:00:00Z",
            },
            {
                "event_id": "future1",
                "user_id": "u5",
                "event_type": "purchase",
                "amount": 5,
                "event_time": "2099-01-01T00:00:00Z",
            },
            {
                "event_id": "outlier1",
                "user_id": "u6",
                "event_type": "purchase",
                "amount": 200000,
                "event_time": "2024-01-01T05:00:00Z",
            },
        ],
    )
    write_events(
        "events_002.jsonl",
        [
            {
                "event_id": "e4",
                "user_id": "u3",
                "event_type": "purchase",
                "amount": 5.5,
                "event_time": "2024-01-02T01:00:00Z",
            },
            {
                "event_id": "e2",
                "user_id": "u2",
                "event_type": "purchase",
                "amount": 7.0,
                "event_time": "2024-01-01T05:00:00Z",
            },
            {
                "event_id": "e5",
                "user_id": "u4",
                "event_type": "cart",
                "amount": 0,
                "event_time": "2024-01-02T02:00:00Z",
            },
        ],
    )
    write_raw(
        "events_003.jsonl.tmp",
        '{"event_id":"ignored","user_id":"x","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_004.jsonl.part",
        '{"event_id":"ignored2","user_id":"x","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_005.jsonl.gz",
        '{"event_id":"ignored3","user_id":"x","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_.jsonl",
        '{"event_id":"ignored4","user_id":"x","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        ".events_hidden.jsonl",
        '{"event_id":"ignored5","user_id":"x","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_..evil.jsonl",
        '{"event_id":"ignored6","user_id":"x","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )

    hash_001 = sha256_of_file(os.path.join(INCOMING, "events_001.jsonl"))
    result = run_batch()
    assert result.returncode == 0, f"{result.stdout} {result.stderr}"
    assert os.path.exists(DB)
    assert os.path.exists(os.path.join(ARCHIVE, "events_001.jsonl"))
    assert os.path.exists(os.path.join(ARCHIVE, "events_002.jsonl"))
    assert not os.path.exists(os.path.join(INCOMING, "events_001.jsonl"))
    assert not os.path.exists(os.path.join(ARCHIVE, "events_003.jsonl.tmp"))

    events, sales, manifest, user_stats, top_users, hourly, sessions, fraud = query_db()
    assert len(events) == 5
    ev_map = {e["event_id"]: e for e in events}
    assert "future1" not in ev_map
    assert "outlier1" not in ev_map
    assert ev_map["e1"]["amount"] == 15.0
    assert len(manifest) == 2
    mf_map = {m["file_name"]: m for m in manifest}
    assert mf_map["events_001.jsonl"]["file_hash"] == hash_001
    assert mf_map["events_001.jsonl"]["num_future"] == 1
    assert mf_map["events_001.jsonl"]["num_outlier"] == 1
    assert len(user_stats) >= 3
    us_map = {u["user_id"]: u for u in user_stats}
    assert us_map["u1"]["total_purchases"] == 2
    assert abs(us_map["u1"]["total_amount"] - 35.0) < 0.01
    # top users
    assert len(top_users) >= 2
    # hourly
    assert len(hourly) >= 2
    # sessions: u1 has events at 03:00 and 04:00 gap 1h >30min so 2 sessions? Actually 03 and 04 gap 1h = 3600 sec >1800 so 2 sessions
    # Check sessions table exists and has rows
    assert len(sessions) >= 4, f"sessions should have at least 4, got {len(sessions)}"
    # checkpoint version 2
    assert os.path.exists(CHECKPOINT)
    with open(CHECKPOINT) as f:
        cp = json.load(f)
    assert cp.get("version") == 2
    assert "files" in cp
    assert cp["files"]["events_001.jsonl"]["hash"] == hash_001
    assert os.path.exists(DEAD_LETTER)
    with open(DEAD_LETTER) as f:
        dl_lines = [json.loads(l) for l in f if l.strip()]
    reasons = [d["reason"] for d in dl_lines]
    assert "invalid_json" in reasons
    assert "future_event" in reasons
    assert "outlier_amount" in reasons
    assert dl_lines == sorted(dl_lines, key=lambda x: (x["file"], x["line_no"]))
    assert os.path.exists(METRICS)
    with open(METRICS) as f:
        m = json.load(f)
    assert m.get("version") == 2
    assert m.get("total_events") == 5
    assert m.get("total_files_processed") == 2
    assert m.get("total_future") == 1
    assert m.get("total_outlier") == 1
    assert "sessions_count" in m
    assert "fraud_alerts_count" in m


def test_sessions_logic():
    clean_env()
    # u1: events with 10 min gaps -> 1 session, then 40 min gap -> new session
    write_events(
        "events_sess.jsonl",
        [
            {
                "event_id": "s1",
                "user_id": "u1",
                "event_type": "view",
                "amount": 0,
                "event_time": "2024-01-01T00:00:00Z",
            },
            {
                "event_id": "s2",
                "user_id": "u1",
                "event_type": "view",
                "amount": 0,
                "event_time": "2024-01-01T00:10:00Z",
            },
            {
                "event_id": "s3",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T00:20:00Z",
            },
            {
                "event_id": "s4",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 20,
                "event_time": "2024-01-01T01:00:00Z",
            },  # 40 min gap from previous
            {
                "event_id": "s5",
                "user_id": "u1",
                "event_type": "view",
                "amount": 0,
                "event_time": "2024-01-01T01:05:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, _, _, _, _, _, sessions, _ = query_db()
    assert len(events) == 5
    # sessions for u1 should be 2
    u1_sessions = [s for s in sessions if s["user_id"] == "u1"]
    assert len(u1_sessions) == 2, f"expected 2 sessions for u1, got {len(u1_sessions)}"
    # first session: 3 events, total amount 10, duration 20 min = 1200 sec
    sess_sorted = sorted(u1_sessions, key=lambda x: x["start_time"])
    assert sess_sorted[0]["event_count"] == 3
    assert abs(sess_sorted[0]["total_amount"] - 10.0) < 0.01
    assert sess_sorted[0]["duration_sec"] == 1200
    # second session: 2 events, amount 20, duration 5 min = 300
    assert sess_sorted[1]["event_count"] == 2
    assert sess_sorted[1]["duration_sec"] == 300


def test_fraud_detection():
    clean_env()
    # u1: 6 purchases within 1 hour -> fraud
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    events = []
    for i in range(6):
        dt = base + timedelta(minutes=i * 10)  # 0,10,20,30,40,50 = within 1h
        events.append(
            {
                "event_id": f"f{i}",
                "user_id": "fraud_u",
                "event_type": "purchase",
                "amount": 10.0,
                "event_time": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    # add one outside window
    events.append(
        {
            "event_id": "f6",
            "user_id": "fraud_u",
            "event_type": "purchase",
            "amount": 10.0,
            "event_time": "2024-01-01T02:00:00Z",
        }
    )
    write_events("events_fraud.jsonl", events)
    result = run_batch()
    assert result.returncode == 0
    _, _, _, _, _, _, _, fraud = query_db()
    assert len(fraud) >= 1, f"should detect fraud, got {len(fraud)}"
    fraud_u = [f for f in fraud if f["user_id"] == "fraud_u"]
    assert len(fraud_u) >= 1
    assert fraud_u[0]["purchase_count"] > 5
    # test no fraud when only 5 purchases in 1h
    clean_env()
    events2 = []
    for i in range(5):
        dt = base + timedelta(minutes=i * 10)
        events2.append(
            {
                "event_id": f"nf{i}",
                "user_id": "ok_u",
                "event_type": "purchase",
                "amount": 10.0,
                "event_time": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    write_events("events_ok.jsonl", events2)
    result = run_batch()
    assert result.returncode == 0
    _, _, _, _, _, _, _, fraud2 = query_db()
    ok_fraud = [f for f in fraud2 if f["user_id"] == "ok_u"]
    assert len(ok_fraud) == 0, "5 purchases in 1h should NOT trigger fraud"


def test_dead_letter_reasons_v4():
    clean_env()
    write_events(
        "events_bad.jsonl",
        [
            '{"bad json}',
            {
                "user_id": "u",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T00:00:00Z",
            },
            {
                "event_id": "x",
                "user_id": "u",
                "event_type": "badtype",
                "amount": 10,
                "event_time": "2024-01-01T00:00:00Z",
            },
            {
                "event_id": "y",
                "user_id": "u",
                "event_type": "purchase",
                "amount": -5,
                "event_time": "2024-01-01T00:00:00Z",
            },
            {
                "event_id": "z",
                "user_id": "u",
                "event_type": "purchase",
                "amount": 200000,
                "event_time": "2024-01-01T00:00:00Z",
            },
            {
                "event_id": "f1",
                "user_id": "u",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2099-01-01T00:00:00Z",
            },
            {
                "event_id": "z2",
                "user_id": "u",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "not-a-time",
            },
            {
                "event_id": "good",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    assert os.path.exists(DEAD_LETTER)
    with open(DEAD_LETTER) as f:
        entries = [json.loads(l) for l in f if l.strip()]
    reasons = set(e["reason"] for e in entries)
    assert "invalid_json" in reasons
    assert "invalid_fields" in reasons
    assert "invalid_type" in reasons
    assert "negative_amount" in reasons
    assert "outlier_amount" in reasons
    assert "future_event" in reasons
    assert "invalid_time" in reasons
    assert len(entries) == 7
    events, _, _, _, _, _, _, _ = query_db()
    assert len(events) == 1


def test_archiving_and_idempotency():
    clean_env()
    write_events(
        "events_001.jsonl",
        [
            {
                "event_id": "a1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 1,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events1, _, manifest1, _, _, _, _, _ = query_db()
    assert os.path.exists(os.path.join(ARCHIVE, "events_001.jsonl"))
    assert not os.path.exists(os.path.join(INCOMING, "events_001.jsonl"))
    result = run_batch()
    assert result.returncode == 0
    events2, _, _, _, _, _, _, _ = query_db()
    assert len(events1) == len(events2)
    shutil.copy(
        os.path.join(ARCHIVE, "events_001.jsonl"),
        os.path.join(INCOMING, "events_001.jsonl"),
    )
    result = run_batch()
    assert result.returncode == 0
    events3, _, _, _, _, _, _, _ = query_db()
    assert len(events3) == 1
