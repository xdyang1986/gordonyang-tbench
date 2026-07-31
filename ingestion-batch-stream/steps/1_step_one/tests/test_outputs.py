"""
Tests for Step 1 - Batch ingestion - Hard Mode
"""

import os
import json
import glob
import hashlib
import sqlite3
import subprocess
import re

INCOMING = "/app/data/incoming"
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
    for f in glob.glob(os.path.join(INCOMING, "*")):
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
    conn.close()
    return list(events), list(sales), list(manifest), list(user_stats)


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_batch_file_exists():
    assert os.path.exists(BATCH_SCRIPT), "batch_ingest.py must exist"


def test_no_streaming_artifacts_in_batch():
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
    os.makedirs(INCOMING, exist_ok=True)
    test_file = os.path.join(INCOMING, "events_negative_check.jsonl")
    if os.path.exists(test_file):
        os.remove(test_file)
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
    assert result.returncode == 0, f"batch failed: {result.stderr}"
    assert not os.path.exists("/app/stream_ingest.py")
    assert not os.path.exists("/app/state/stream_offsets.json")
    assert not os.path.exists("/app/stream.pid")
    if os.path.exists("/app/config.yaml"):
        with open("/app/config.yaml") as f:
            content = f.read()
        assert "mode: stream" not in content
    if os.path.exists(METRICS):
        with open(METRICS) as f:
            try:
                m = json.load(f)
                assert m.get("mode") != "stream"
                assert m.get("streaming") != True
            except:
                pass
    if os.path.exists(test_file):
        os.remove(test_file)


def test_basic_ingestion_hard():
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

    result = run_batch()
    assert result.returncode == 0, f"batch failed: {result.stdout} {result.stderr}"
    assert os.path.exists(DB)
    events, sales, manifest, user_stats = query_db()
    assert len(events) == 5, f"expected 5, got {len(events)}"
    ev_map = {e["event_id"]: e for e in events}
    assert ev_map["e1"]["amount"] == 15.0
    assert ev_map["e2"]["event_type"] == "purchase"
    assert "ignored" not in ev_map
    assert "ignored2" not in ev_map
    sales_map = {s["date"]: s for s in sales}
    assert "2024-01-01" in sales_map
    assert abs(sales_map["2024-01-01"]["total_amount"] - 42.0) < 0.01
    assert len(manifest) == 2
    mf_map = {m["file_name"]: m for m in manifest}
    expected_hash = sha256_of_file(os.path.join(INCOMING, "events_001.jsonl"))
    assert mf_map["events_001.jsonl"]["file_hash"] == expected_hash
    for fn, rec in mf_map.items():
        assert rec["num_valid"] + rec["num_invalid"] <= rec["num_lines"]
    assert len(user_stats) >= 3
    us_map = {u["user_id"]: u for u in user_stats}
    assert us_map["u1"]["total_purchases"] == 2
    assert abs(us_map["u1"]["total_amount"] - 35.0) < 0.01
    assert os.path.exists(CHECKPOINT)
    with open(CHECKPOINT) as f:
        cp = json.load(f)
    assert cp.get("mode") == "batch"
    assert "files" in cp
    assert cp["files"]["events_001.jsonl"]["hash"] == expected_hash
    assert os.path.exists(DEAD_LETTER)
    with open(DEAD_LETTER) as f:
        dl_lines = [json.loads(l) for l in f if l.strip()]
    reasons = [d["reason"] for d in dl_lines]
    assert "invalid_json" in reasons
    assert os.path.exists(METRICS)
    with open(METRICS) as f:
        m = json.load(f)
    assert m.get("mode") == "batch"
    assert m.get("total_events") == 5
    assert m.get("total_files_processed") == 2
    assert "total_invalid" in m
    assert m["manifest_count"] == 2


def test_dead_letter_reasons():
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
    assert "invalid_time" in reasons
    assert len(entries) == 5
    events, _, _, _ = query_db()
    assert len(events) == 1
    assert events[0]["event_id"] == "good"


def test_idempotency_and_hash():
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
    events1, _, manifest1, _ = query_db()
    hash1 = manifest1[0]["file_hash"]
    result = run_batch()
    assert result.returncode == 0
    events2, _, manifest2, _ = query_db()
    assert len(events1) == len(events2)
    assert manifest2[0]["file_hash"] == hash1
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
            {
                "event_id": "a2",
                "user_id": "u2",
                "event_type": "purchase",
                "amount": 2,
                "event_time": "2024-01-01T01:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events3, _, manifest3, _ = query_db()
    assert len(events3) == 2
    assert manifest3[0]["file_hash"] != hash1


def test_ignore_tmp_part_gz():
    clean_env()
    write_events(
        "events_valid.jsonl",
        [
            {
                "event_id": "v1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ],
    )
    write_raw(
        "events_valid.jsonl.tmp",
        '{"event_id":"tmp1","user_id":"u","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_valid.jsonl.part",
        '{"event_id":"part1","user_id":"u","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_valid.jsonl.gz",
        '{"event_id":"gz1","user_id":"u","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    result = run_batch()
    assert result.returncode == 0
    events, _, manifest, _ = query_db()
    assert len(events) == 1
    assert events[0]["event_id"] == "v1"
    assert len(manifest) == 1
    assert manifest[0]["file_name"] == "events_valid.jsonl"


def test_time_parsing_plus_offset():
    clean_env()
    write_events(
        "events_tz.jsonl",
        [
            {
                "event_id": "tz1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T01:00:00+00:00",
            },
            {
                "event_id": "tz2",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 20,
                "event_time": "2024-01-01T02:00:00.123Z",
            },
            {
                "event_id": "tz1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 30,
                "event_time": "2024-01-01T03:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, _, _, _ = query_db()
    assert len(events) == 2
    ev_map = {e["event_id"]: e for e in events}
    assert ev_map["tz1"]["amount"] == 30.0
