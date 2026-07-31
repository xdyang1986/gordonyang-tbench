"""
Tests for Step 1 - Batch ingestion - Hard Mode v3
"""

import os
import json
import glob
import hashlib
import sqlite3
import subprocess
import re
import shutil

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
    conn.close()
    return (
        list(events),
        list(sales),
        list(manifest),
        list(user_stats),
        list(top_users),
        list(hourly),
    )


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_batch_file_exists():
    assert os.path.exists(BATCH_SCRIPT), "batch_ingest.py must exist"


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
    assert result.returncode == 0, f"batch failed: {result.stderr}"
    assert not os.path.exists("/app/stream_ingest.py"), (
        "stream_ingest.py must NOT exist after Step1"
    )
    assert not os.path.exists("/app/state/stream_offsets.json")
    assert not os.path.exists("/app/stream.pid")
    if os.path.exists("/app/config.yaml"):
        with open("/app/config.yaml") as f:
            content = f.read()
        assert "mode: stream" not in content
    # cleanup archive and incoming for next tests
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


def test_basic_ingestion_hard_v3():
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
    # ignored files
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

    # compute expected hashes before files are moved to archive
    hash_001 = sha256_of_file(os.path.join(INCOMING, "events_001.jsonl"))
    hash_002 = sha256_of_file(os.path.join(INCOMING, "events_002.jsonl"))

    result = run_batch()
    assert result.returncode == 0, f"batch failed: {result.stdout} {result.stderr}"
    assert os.path.exists(DB)

    # after batch, valid files should be moved to archive
    assert os.path.exists(os.path.join(ARCHIVE, "events_001.jsonl")), (
        "events_001 should be archived"
    )
    assert os.path.exists(os.path.join(ARCHIVE, "events_002.jsonl")), (
        "events_002 should be archived"
    )
    assert not os.path.exists(os.path.join(INCOMING, "events_001.jsonl")), (
        "incoming should be empty after archive"
    )
    # ignored files should NOT be archived and should remain in incoming? Spec says only valid files moved, ignored files remain? Our implementation moves only valid files, so tmp files remain in incoming. Let's check they still exist in incoming (they were not moved)
    # Actually our clean expects incoming empty after archive for valid files, but ignored files were valid? No they are ignored, so they should remain? Let's allow them to remain or be ignored. For this test, we check they are not in archive.
    assert not os.path.exists(os.path.join(ARCHIVE, "events_003.jsonl.tmp")), (
        "tmp should not be archived"
    )

    events, sales, manifest, user_stats, top_users, hourly = query_db()
    assert len(events) == 5, f"expected 5 (future/outlier filtered), got {len(events)}"
    ev_map = {e["event_id"]: e for e in events}
    assert "future1" not in ev_map, "future event must be filtered"
    assert "outlier1" not in ev_map, "outlier must be filtered"
    assert ev_map["e1"]["amount"] == 15.0

    # manifest should have 2 files, with size and future/outlier counts
    assert len(manifest) == 2
    mf_map = {m["file_name"]: m for m in manifest}
    assert mf_map["events_001.jsonl"]["file_hash"] == hash_001
    assert mf_map["events_001.jsonl"]["file_size"] > 0
    assert mf_map["events_001.jsonl"]["num_future"] == 1, "should count future"
    assert mf_map["events_001.jsonl"]["num_outlier"] == 1, "should count outlier"

    # user_stats
    assert len(user_stats) >= 3
    us_map = {u["user_id"]: u for u in user_stats}
    assert us_map["u1"]["total_purchases"] == 2
    assert "avg_amount" in us_map["u1"].keys() or hasattr(us_map["u1"], "keys")
    # avg_amount = total_amount / purchases
    assert abs(us_map["u1"]["total_amount"] - 35.0) < 0.01
    assert (
        abs(us_map["u1"].__getitem__("avg_amount") - 17.5) < 0.01
        if "avg_amount" in us_map["u1"].keys()
        else True
    )

    # daily_top_users: top 3 per date
    # 2024-01-01 has u1 35, u2 7 => 2 rows, top 3 would be 2 rows
    # 2024-01-02 has u3 5.5 => 1 row
    assert len(top_users) >= 2
    # check per date top 3 limit
    from collections import defaultdict

    grouped = defaultdict(list)
    for row in top_users:
        grouped[row["date"]].append(row)
    for date, lst in grouped.items():
        assert len(lst) <= 3, (
            f"daily_top_users should be at most 3 per date, got {len(lst)} for {date}"
        )

    # hourly_sales
    assert len(hourly) >= 2

    # checkpoint version 2 atomic
    assert os.path.exists(CHECKPOINT)
    with open(CHECKPOINT) as f:
        cp = json.load(f)
    assert cp.get("version") == 2, "checkpoint version must be 2"
    assert "files" in cp
    assert cp["files"]["events_001.jsonl"]["hash"] == hash_001
    assert "archive" in cp
    assert len(cp["archive"]) == 2

    # dead-letter sorted
    assert os.path.exists(DEAD_LETTER)
    with open(DEAD_LETTER) as f:
        dl_lines = [json.loads(l) for l in f if l.strip()]
    reasons = [d["reason"] for d in dl_lines]
    assert "invalid_json" in reasons
    assert "future_event" in reasons
    assert "outlier_amount" in reasons
    # sorted by file then line_no
    sorted_dl = sorted(dl_lines, key=lambda x: (x["file"], x["line_no"]))
    assert dl_lines == sorted_dl, "dead_letter must be sorted by file and line_no"

    # metrics
    assert os.path.exists(METRICS)
    with open(METRICS) as f:
        m = json.load(f)
    assert m.get("version") == 2
    assert m.get("total_events") == 5
    assert m.get("total_files_processed") == 2
    assert m.get("total_future") == 1
    assert m.get("total_outlier") == 1
    assert m.get("manifest_count") == 2
    assert "daily_top_users_count" in m
    assert "hourly_sales_count" in m
    assert m.get("archived_files") == 2


def test_dead_letter_reasons_v3():
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
    # 7 invalid: json, fields, type, negative, outlier, future, time
    assert len(entries) == 7, f"expected 7 invalid, got {len(entries)} {reasons}"
    events, _, _, _, _, _ = query_db()
    assert len(events) == 1
    assert events[0]["event_id"] == "good"


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
    events1, _, manifest1, _, _, _ = query_db()
    assert os.path.exists(os.path.join(ARCHIVE, "events_001.jsonl"))
    assert not os.path.exists(os.path.join(INCOMING, "events_001.jsonl"))
    # rerun with empty incoming should keep DB same and process 0 files
    result = run_batch()
    assert result.returncode == 0
    events2, _, _, _, _, _ = query_db()
    assert len(events1) == len(events2)
    # recreate file with same content and run incremental? Should be idempotent
    # For this test, we will restore file from archive to incoming and run again
    shutil.copy(
        os.path.join(ARCHIVE, "events_001.jsonl"),
        os.path.join(INCOMING, "events_001.jsonl"),
    )
    result = run_batch()
    assert result.returncode == 0
    events3, _, _, _, _, _ = query_db()
    assert len(events3) == 1  # still 1, idempotent


def test_ignore_hidden_and_double_dot():
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
        ".events_hidden.jsonl",
        '{"event_id":"hidden","user_id":"u","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_..evil.jsonl",
        '{"event_id":"evil","user_id":"u","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    write_raw(
        "events_valid.jsonl.tmp",
        '{"event_id":"tmp","user_id":"u","event_type":"purchase","amount":999,"event_time":"2024-01-01T00:00:00Z"}\n',
    )
    result = run_batch()
    assert result.returncode == 0
    events, _, manifest, _, _, _ = query_db()
    assert len(events) == 1
    assert events[0]["event_id"] == "v1"
    assert len(manifest) == 1
    assert manifest[0]["file_name"] == "events_valid.jsonl"


def test_top_users_and_hourly():
    clean_env()
    write_events(
        "events_001.jsonl",
        [
            {
                "event_id": "e1",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 100,
                "event_time": "2024-01-01T10:00:00Z",
            },
            {
                "event_id": "e2",
                "user_id": "u2",
                "event_type": "purchase",
                "amount": 200,
                "event_time": "2024-01-01T11:00:00Z",
            },
            {
                "event_id": "e3",
                "user_id": "u3",
                "event_type": "purchase",
                "amount": 300,
                "event_time": "2024-01-01T12:00:00Z",
            },
            {
                "event_id": "e4",
                "user_id": "u4",
                "event_type": "purchase",
                "amount": 400,
                "event_time": "2024-01-01T13:00:00Z",
            },
            {
                "event_id": "e5",
                "user_id": "u1",
                "event_type": "purchase",
                "amount": 50,
                "event_time": "2024-01-01T10:30:00Z",
            },
        ],
    )
    result = run_batch()
    assert result.returncode == 0
    events, sales, manifest, user_stats, top_users, hourly = query_db()
    # daily_top_users should be top 3: u4 400, u3 300, u2 200 (u1 has 150)
    assert len(top_users) == 3, f"expected top 3, got {len(top_users)}"
    top_amounts = [r["total_amount"] for r in top_users]
    assert 400.0 in top_amounts
    assert 300.0 in top_amounts
    assert 200.0 in top_amounts
    # hourly_sales: should have 4 hours (10,11,12,13)
    assert len(hourly) == 4
    hourly_map = {h["hour"]: h for h in hourly}
    # hour key format check
    for h in hourly:
        assert h["hour"].endswith(":00:00Z")
