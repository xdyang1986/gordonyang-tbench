"""
Tests for Step 1 - Batch ingestion
"""

import os
import json
import glob
import sqlite3
import subprocess
import time
from datetime import datetime, timezone

INCOMING = "/app/data/incoming"
DB = "/app/warehouse.db"
CHECKPOINT = "/app/state/checkpoint.json"
METRICS = "/app/metrics/freshness.json"
BATCH_SCRIPT = "/app/batch_ingest.py"


def clean_env():
    for p in [DB, CHECKPOINT, METRICS]:
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(INCOMING, exist_ok=True)
    for f in glob.glob(os.path.join(INCOMING, "events_*.jsonl")):
        os.remove(f)
    os.makedirs("/app/state", exist_ok=True)
    os.makedirs("/app/metrics", exist_ok=True)


def write_events(filename, events):
    path = os.path.join(INCOMING, filename)
    with open(path, "w") as out:
        for e in events:
            if isinstance(e, str):
                out.write(e + "\n")
            else:
                out.write(json.dumps(e) + "\n")


def run_batch():
    import sys

    py = sys.executable
    result = subprocess.run(
        [py, BATCH_SCRIPT], capture_output=True, text=True, timeout=30
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


def test_batch_file_exists():
    assert os.path.exists(BATCH_SCRIPT), (
        "batch_ingest.py must exist at /app/batch_ingest.py"
    )


def test_no_streaming_artifacts_in_batch():
    """
    Negative test against over-execution: Step 1 is batch-only.
    streaming artifacts explicitly named in Step 2 instruction must NOT exist after Step 1.
    """
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
    assert result.returncode == 0, f"batch failed in negative test: {result.stderr}"
    assert not os.path.exists("/app/stream_ingest.py"), (
        "stream_ingest.py must NOT exist after Step 1"
    )
    assert not os.path.exists("/app/state/stream_offsets.json"), (
        "stream_offsets.json must NOT exist after Step 1"
    )
    assert not os.path.exists("/app/stream.pid"), (
        "stream.pid must NOT exist in batch mode"
    )
    if os.path.exists("/app/config.yaml"):
        with open("/app/config.yaml") as f:
            content = f.read()
        assert "mode: stream" not in content, (
            "config.yaml mode must not be stream in Step 1"
        )
    if os.path.exists(METRICS):
        with open(METRICS) as f:
            try:
                m = json.load(f)
                assert m.get("mode") != "stream", (
                    "metrics mode stream is Step 2 artifact"
                )
                assert m.get("streaming") != True, (
                    "streaming flag must not be True in Step 1"
                )
            except Exception:
                pass
    if os.path.exists(test_file):
        os.remove(test_file)


def test_basic_ingestion():
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
                "event_time": "2024-01-01T03:00:00Z",
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
    result = run_batch()
    assert result.returncode == 0, f"batch failed: {result.stdout} {result.stderr}"
    assert os.path.exists(DB), "warehouse.db not created"
    events, sales = query_db()
    assert len(events) == 5, f"expected 5 distinct, got {len(events)}"
    ev_map = {e["event_id"]: e for e in events}
    assert ev_map["e1"]["amount"] == 15.0
    assert ev_map["e2"]["event_type"] == "purchase"
    assert ev_map["e2"]["amount"] == 7.0
    sales_map = {s["date"]: s for s in sales}
    assert "2024-01-01" in sales_map
    assert abs(sales_map["2024-01-01"]["total_amount"] - 42.0) < 0.01
    assert sales_map["2024-01-01"]["event_count"] == 3
    assert "2024-01-02" in sales_map
    assert abs(sales_map["2024-01-02"]["total_amount"] - 5.5) < 0.01
    assert sales_map["2024-01-02"]["event_count"] == 1


def test_checkpoint_and_metrics():
    assert os.path.exists(CHECKPOINT), "checkpoint.json missing"
    with open(CHECKPOINT) as f:
        cp = json.load(f)
    assert cp.get("mode") == "batch"
    assert "files_processed" in cp
    assert len(cp["files_processed"]) == 2
    assert cp["total_events"] == 5
    assert os.path.exists(METRICS), "freshness.json missing"
    with open(METRICS) as f:
        m = json.load(f)
    assert m.get("mode") == "batch"
    assert m.get("total_events") == 5
    assert m.get("total_files_processed") == 2
    assert "last_batch_time" in m


def test_idempotency():
    events_before, _ = query_db()
    result = run_batch()
    assert result.returncode == 0
    events_after, _ = query_db()
    assert len(events_before) == len(events_after)
    for e1, e2 in zip(events_before, events_after):
        assert e1["event_id"] == e2["event_id"]
        assert abs(e1["amount"] - e2["amount"]) < 0.001


def test_empty_and_invalid_handling():
    clean_env()
    result = run_batch()
    assert result.returncode == 0
    assert os.path.exists(DB)
    write_events(
        "events_003.jsonl",
        [
            '{"bad":}',
            "not json at all",
            "",
            "   ",
            {
                "event_id": "bad",
                "user_id": "u",
                "event_type": "invalid_type",
                "amount": 10,
                "event_time": "2024-01-01T00:00:00Z",
            },
            {
                "event_id": "e6",
                "user_id": "u",
                "event_type": "purchase",
                "amount": -5,
                "event_time": "2024-01-01T00:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, _ = query_db()
    assert len(events) == 0
    clean_env()
    write_events(
        "events_004.jsonl",
        [
            "invalid line to skip",
            {
                "event_id": "good1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 100,
                "event_time": "2024-03-01T10:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, _ = query_db()
    assert len(events) == 1
    assert events[0]["event_id"] == "good1"


def test_dedup_across_files_latest_wins():
    clean_env()
    write_events(
        "events_005.jsonl",
        [
            {
                "event_id": "dup",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 1,
                "event_time": "2024-01-01T01:00:00Z",
            },
        ],
    )
    write_events(
        "events_006.jsonl",
        [
            {
                "event_id": "dup",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 2,
                "event_time": "2024-01-01T02:00:00Z",
            },
        ],
    )
    write_events(
        "events_007.jsonl",
        [
            {
                "event_id": "dup",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 3,
                "event_time": "2024-01-01T01:30:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, _ = query_db()
    assert len(events) == 1
    assert events[0]["amount"] == 2.0
    clean_env()
    write_events(
        "events_008.jsonl",
        [
            {
                "event_id": "dup2",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 10,
                "event_time": "2024-01-01T01:00:00Z",
            },
            {
                "event_id": "dup2",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 20,
                "event_time": "2024-01-01T01:00:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, _ = query_db()
    assert events[0]["amount"] == 20.0
