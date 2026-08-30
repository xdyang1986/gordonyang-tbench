"""
Step 1 tests for high-performance ingest-search.
Builds Go project at /app and runs HTTP server.
"""

import os, json, time, shutil, socket, subprocess, threading, binascii
import pytest, requests

APP = "/app"
BIN = "/tmp/search-server"
DATA_DIR = "/app/data"
GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}


def find_free_port():
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_for_server(port, timeout=15):
    start = time.time()
    url = f"http://127.0.0.1:{port}/health"
    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


@pytest.fixture(scope="session", autouse=True)
def build_binary():
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs("/tmp", exist_ok=True)
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(
            ["go", "mod", "init", "ingestsearch"],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
        )
    subprocess.run(
        ["go", "mod", "tidy"], cwd=APP, env=GO_ENV, capture_output=True, timeout=60
    )
    res = subprocess.run(
        ["go", "build", "-o", BIN, "."],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, (
        f"go build failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}"
    )
    assert os.path.exists(BIN), "binary not produced"
    yield


@pytest.fixture()
def server():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15), f"server failed to start on port {port}"
    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)
        time.sleep(0.2)


def ingest(base, entries):
    return requests.post(f"{base}/ingest", json={"entries": entries}, timeout=5)


def bulk_ingest(base, ndjson_str):
    return requests.post(
        f"{base}/ingest/bulk",
        data=ndjson_str,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=10,
    )


def search(base, **params):
    return requests.get(f"{base}/search", params=params, timeout=5)


def get_doc(base, doc_id):
    return requests.get(f"{base}/documents/{doc_id}", timeout=5)


def delete_doc(base, doc_id):
    return requests.delete(f"{base}/documents/{doc_id}", timeout=5)


# ---------------------------------------------------------------------------
# Basic tests
# ---------------------------------------------------------------------------


def test_health(server):
    r = requests.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") == "ok"


def test_ingest_and_get(server):
    base = server
    entries = [
        {
            "id": "log1",
            "timestamp": "2026-07-20T10:00:00Z",
            "service": "auth-service",
            "level": "info",
            "message": "User login successful for user_42",
            "tags": ["auth", "login"],
        },
        {
            "id": "log2",
            "timestamp": "2026-07-20T11:00:00Z",
            "service": "payment",
            "level": "error",
            "message": "Payment failed for order 123",
            "tags": ["payment"],
        },
    ]
    r = ingest(base, entries)
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    j = r.json()
    assert j["ingested"] == 2

    r = get_doc(base, "log1")
    assert r.status_code == 200
    doc = r.json()
    assert doc["id"] == "log1"
    assert doc["service"] == "auth-service"  # lowercased original is already lower
    assert doc["level"] == "info"
    assert "User login successful" in doc["message"]
    # tags lowercased
    assert "auth" in [t.lower() for t in doc.get("tags", [])]


def test_upsert_overwrites(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "d1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "a",
                "level": "info",
                "message": "old message",
            }
        ],
    )
    ingest(
        base,
        [
            {
                "id": "d1",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "a",
                "level": "info",
                "message": "new message",
            }
        ],
    )
    r = get_doc(base, "d1")
    assert r.status_code == 200
    assert "new message" in r.json()["message"]
    # old term should not be searchable
    r = search(base, q="old")
    assert r.status_code == 200
    assert r.json()["total"] == 0
    r = search(base, q="new")
    assert r.json()["total"] == 1


def test_delete(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "del1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "to be deleted",
            }
        ],
    )
    assert get_doc(base, "del1").status_code == 200
    r = delete_doc(base, "del1")
    assert r.status_code == 200
    assert get_doc(base, "del1").status_code == 404
    r = search(base, q="deleted")
    assert r.json()["total"] == 0
    r = delete_doc(base, "del1")
    assert r.status_code == 404


def test_search_and_semantics(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "auth",
                "level": "info",
                "message": "User login successful",
            },
            {
                "id": "2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "auth",
                "level": "info",
                "message": "User login failed",
            },
            {
                "id": "3",
                "timestamp": "2026-07-20T12:00:00Z",
                "service": "payment",
                "level": "error",
                "message": "Payment successful",
            },
        ],
    )
    r = search(base, q="login")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r = search(base, q="login successful")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "1"
    r = search(base, q="user login")
    assert r.json()["total"] == 2


def test_phrase_query_adjacent(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "p1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "a",
                "level": "info",
                "message": "User login successful",
            },
            {
                "id": "p2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "a",
                "level": "info",
                "message": "User login and then successful",
            },
            {
                "id": "p3",
                "timestamp": "2026-07-20T12:00:00Z",
                "service": "a",
                "level": "info",
                "message": "successful login for user",
            },
        ],
    )
    # phrase "login successful" should only match p1 (adjacent in order)
    r = search(base, q='"login successful"')
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"p1"}, f"phrase adjacency failed, got {ids}"
    # reverse order should not match p1
    r = search(base, q='"successful login"')
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"p3"}, f"reverse phrase should match p3, got {ids}"
    # non-adjacent should not match
    r = search(base, q='"login and"')
    # p2 has "login and" adjacent
    ids = {x["id"] for x in r.json()["results"]}
    assert "p2" in ids


def test_phrase_non_adjacent_no_match(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "a1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "login successful",
            },
            {
                "id": "a2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "s",
                "level": "info",
                "message": "login is successful",
            },
        ],
    )
    r = search(base, q='"login successful"')
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"a1"}, f"non-adjacent should not match, got {ids}"


def test_empty_phrase_400(server):
    base = server
    r = search(base, q='""')
    assert r.status_code == 400, f"empty phrase should 400, got {r.status_code}"
    r = search(base, q='"   "')
    assert r.status_code == 400


def test_empty_query_match_all(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "e1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s1",
                "level": "info",
                "message": "msg1",
            },
            {
                "id": "e2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "s2",
                "level": "error",
                "message": "msg2",
            },
        ],
    )
    r = search(base)
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r = search(base, q="")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r = search(base, q="   ")
    assert r.json()["total"] == 2


def test_service_filter(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "s1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "auth",
                "level": "info",
                "message": "auth message",
            },
            {
                "id": "s2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "payment",
                "level": "info",
                "message": "payment message",
            },
        ],
    )
    r = search(base, service="auth")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "s1"
    r = search(base, service="AUTH")
    assert r.json()["total"] == 1, "service filter case-insensitive"
    r = search(base, q="message", service="payment")
    assert r.json()["total"] == 1


def test_level_filter(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "l1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "info msg",
            },
            {
                "id": "l2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "s",
                "level": "error",
                "message": "error msg",
            },
        ],
    )
    r = search(base, level="info")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "l1"
    r = search(base, level="ERROR")
    assert r.json()["total"] == 1
    r = search(base, level="invalid")
    assert r.status_code == 400


def test_tags_filter_and(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "t1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
                "tags": ["auth", "login"],
            },
            {
                "id": "t2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
                "tags": ["auth"],
            },
            {
                "id": "t3",
                "timestamp": "2026-07-20T12:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
                "tags": ["login"],
            },
        ],
    )
    r = search(base, tags="auth")
    assert r.json()["total"] == 2
    r = search(base, tags="auth,login")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "t1"
    r = search(base, tags="AUTH,LOGIN")
    assert r.json()["total"] == 1, "tags filter case-insensitive"


def test_time_range_filter(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "time1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "first",
            },
            {
                "id": "time2",
                "timestamp": "2026-07-20T12:00:00Z",
                "service": "s",
                "level": "info",
                "message": "second",
            },
            {
                "id": "time3",
                "timestamp": "2026-07-20T14:00:00Z",
                "service": "s",
                "level": "info",
                "message": "third",
            },
        ],
    )
    r = search(base, **{"from": "2026-07-20T11:00:00Z", "to": "2026-07-20T13:00:00Z"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "time2"
    r = search(base, **{"from": "2026-07-20T09:00:00Z"})
    assert r.json()["total"] == 3
    r = search(base, **{"to": "2026-07-20T11:00:00Z"})
    assert r.json()["total"] == 1
    # invalid from
    r = search(base, **{"from": "invalid"})
    assert r.status_code == 400
    r = search(base, **{"to": "invalid"})
    assert r.status_code == 400


def test_sorting(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "a",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
            },
            {
                "id": "b",
                "timestamp": "2026-07-20T12:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
            },
            {
                "id": "c",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
            },
        ],
    )
    r = search(base, sort="timestamp:asc")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["results"]]
    assert ids == ["a", "c", "b"], f"asc sort failed: {ids}"
    r = search(base, sort="timestamp:desc")
    ids = [x["id"] for x in r.json()["results"]]
    assert ids == ["b", "c", "a"], f"desc sort failed: {ids}"
    r = search(base, sort="invalid")
    assert r.status_code == 400


def test_pagination(server):
    base = server
    entries = [
        {
            "id": f"doc{i}",
            "timestamp": f"2026-07-20T10:{i:02d}:00Z",
            "service": "s",
            "level": "info",
            "message": f"message {i}",
        }
        for i in range(5)
    ]
    ingest(base, entries)
    r = search(base, q="message", limit=2, offset=0)
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 5
    assert len(j["results"]) == 2
    r2 = search(base, q="message", limit=2, offset=2)
    assert len(r2.json()["results"]) == 2
    # ensure no overlap
    ids1 = {x["id"] for x in j["results"]}
    ids2 = {x["id"] for x in r2.json()["results"]}
    assert ids1.isdisjoint(ids2)
    # limit >100 clamped to 100, not 400
    r = search(base, limit=200)
    assert r.status_code == 200
    assert len(r.json()["results"]) <= 100
    # negative limit 400
    r = search(base, limit=-1)
    assert r.status_code == 400
    # invalid offset
    r = search(base, offset=-5)
    assert r.status_code == 400
    # float limit 400
    r = search(base, limit="10.5")
    assert r.status_code == 400


def test_stats_endpoint(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "st1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "auth",
                "level": "info",
                "message": "test message one",
            },
            {
                "id": "st2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "payment",
                "level": "error",
                "message": "test message two",
            },
        ],
    )
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert "docs" in j and "services" in j and "levels" in j and "terms" in j
    assert j["docs"] == 2
    assert j["services"] == 2
    assert j["levels"]["info"] == 1
    assert j["levels"]["error"] == 1
    assert j["terms"] > 0
    # ensure exact keys only
    assert set(j.keys()) == {"docs", "services", "levels", "terms"}


def test_bulk_endpoint(server):
    base = server
    ndjson = '{"id":"b1","timestamp":"2026-07-20T10:00:00Z","service":"s","level":"info","message":"bulk one"}\n{"id":"b2","timestamp":"2026-07-20T11:00:00Z","service":"s","level":"info","message":"bulk two"}\n'
    r = bulk_ingest(base, ndjson)
    # bulk may be 404 if not implemented in step1, but we encourage implementation
    if r.status_code == 404:
        pytest.skip("bulk endpoint not implemented in step1 (optional)")
    assert r.status_code == 201, f"bulk failed {r.status_code} {r.text}"
    j = r.json()
    assert j["ingested"] == 2
    # verify searchable
    r = search(base, q="bulk")
    assert r.json()["total"] == 2


def test_invalid_ingest(server):
    base = server
    # missing id
    r = ingest(
        base,
        [
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
            }
        ],
    )
    assert r.status_code == 400
    # invalid level
    r = ingest(
        base,
        [
            {
                "id": "x",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "invalid",
                "message": "msg",
            }
        ],
    )
    assert r.status_code == 400
    # invalid timestamp
    r = ingest(
        base,
        [
            {
                "id": "x",
                "timestamp": "not-a-time",
                "service": "s",
                "level": "info",
                "message": "msg",
            }
        ],
    )
    assert r.status_code == 400
    # invalid json
    r = requests.post(
        f"{base}/ingest",
        data="not json",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 400
    # entries not array
    r = requests.post(f"{base}/ingest", json={"entries": "notarray"}, timeout=5)
    assert r.status_code == 400
    # atomicity: if one invalid in batch, none ingested
    ingest(
        base,
        [
            {
                "id": "good1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "good",
            }
        ],
    )
    r = ingest(
        base,
        [
            {
                "id": "good2",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "good",
            },
            {
                "id": "bad",
                "timestamp": "invalid",
                "service": "s",
                "level": "info",
                "message": "bad",
            },
        ],
    )
    assert r.status_code == 400
    r = search(base, q="good")
    # should only have good1, not good2
    ids = {x["id"] for x in r.json()["results"]}
    assert "good2" not in ids


def test_persistence_and_recovery():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15), "server start failed first"
    base = f"http://127.0.0.1:{port}"
    try:
        r = ingest(
            base,
            [
                {
                    "id": "persist1",
                    "timestamp": "2026-07-20T10:00:00Z",
                    "service": "s",
                    "level": "info",
                    "message": "persist me",
                }
            ],
        )
        assert r.status_code == 201
        time.sleep(1.0)  # allow async flush if any
        # check data files exist
        assert os.path.exists(os.path.join(DATA_DIR, "index.json")) or os.path.exists(
            os.path.join(DATA_DIR, "wal.log")
        ), "persistence files not created"
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)
        # restart
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port, timeout=15), "server second start failed"
        base2 = f"http://127.0.0.1:{port}"
        r = get_doc(base2, "persist1")
        assert r.status_code == 200, (
            f"doc should persist after restart, got {r.status_code}"
        )
        r = search(base2, q="persist")
        assert r.json()["total"] == 1
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_truncated_recovery():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15)
    base = f"http://127.0.0.1:{port}"
    try:
        ingest(
            base,
            [
                {
                    "id": "rec1",
                    "timestamp": "2026-07-20T10:00:00Z",
                    "service": "s",
                    "level": "info",
                    "message": "recover",
                }
            ],
        )
        time.sleep(1.0)
        data_file = os.path.join(DATA_DIR, "index.json")
        if os.path.exists(data_file):
            with open(data_file, "ab") as f:
                f.write(b'{"truncated":')
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)
        port2 = find_free_port()
        env2 = {**os.environ, "PORT": str(port2)}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port2, timeout=15), (
            "server should recover from truncated index.json"
        )
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/search", timeout=5)
        assert r.status_code == 200
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_wal_replay():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15)
    base = f"http://127.0.0.1:{port}"
    try:
        ingest(
            base,
            [
                {
                    "id": "wal1",
                    "timestamp": "2026-07-20T10:00:00Z",
                    "service": "s",
                    "level": "info",
                    "message": "wal test",
                }
            ],
        )
        time.sleep(0.5)
        # delete index.json, keep wal.log
        idx_path = os.path.join(DATA_DIR, "index.json")
        if os.path.exists(idx_path):
            os.remove(idx_path)
        assert os.path.exists(os.path.join(DATA_DIR, "wal.log")), "wal.log should exist"
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port, timeout=15), "server should replay WAL"
        r = get_doc(base, "wal1")
        assert r.status_code == 200, "WAL replay failed"
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_concurrency(server):
    base = server
    errors = []

    def do_ingest(start, count):
        for i in range(count):
            try:
                r = ingest(
                    base,
                    [
                        {
                            "id": f"conc{start}_{i}",
                            "timestamp": "2026-07-20T10:00:00Z",
                            "service": "s",
                            "level": "info",
                            "message": f"concurrent message {start} {i} stress",
                        }
                    ],
                )
                if r.status_code != 201:
                    errors.append(f"ingest failed {r.status_code}")
            except Exception as e:
                errors.append(str(e))

    def do_search():
        for _ in range(20):
            try:
                r = search(base, q="concurrent", limit=10)
                if r.status_code != 200:
                    errors.append(f"search failed {r.status_code}")
            except Exception as e:
                errors.append(str(e))

    threads = []
    for t in range(5):
        th = threading.Thread(target=do_ingest, args=(t, 10))
        threads.append(th)
        th.start()
    for _ in range(3):
        th = threading.Thread(target=do_search)
        threads.append(th)
        th.start()
    for th in threads:
        th.join()
    assert not errors, f"concurrency errors: {errors}"
    r = search(base, q="concurrent", limit=100)
    assert r.status_code == 200
    assert r.json()["total"] >= 50


# ---------------------------------------------------------------------------
# AFTR coverage gap tests (were missing)
# ---------------------------------------------------------------------------


def test_go_mod_forbidden_libs():
    go_mod = os.path.join(APP, "go.mod")
    if not os.path.exists(go_mod):
        pytest.skip("go.mod not found")
    with open(go_mod) as f:
        content = f.read().lower()
    forbidden = [
        "bleve",
        "elastic",
        "elasticsearch",
        "algolia",
        "meilisearch",
        "sonic",
        "tantivy",
        "lucene",
    ]
    for lib in forbidden:
        assert lib not in content, f"go.mod contains forbidden lib {lib}"


def test_data_file_env_handling():
    port = find_free_port()
    custom_dir = "/tmp/customdata_test"
    custom_data_file = os.path.join(custom_dir, "myindex.json")
    # clean
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_data_file}
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert wait_for_server(port, timeout=15), (
            "server failed to start with DATA_FILE env"
        )
        base = f"http://127.0.0.1:{port}"
        r = ingest(
            base,
            [
                {
                    "id": "df1",
                    "timestamp": "2026-07-20T10:00:00Z",
                    "service": "s",
                    "level": "info",
                    "message": "data file test",
                }
            ],
        )
        assert r.status_code == 201
        time.sleep(0.8)
        # should have created custom file or WAL at same dir
        custom_wal = os.path.join(custom_dir, "wal.log")
        assert os.path.exists(custom_data_file) or os.path.exists(custom_wal), (
            f"DATA_FILE handling failed: neither {custom_data_file} nor {custom_wal} exists. ls {custom_dir}: {os.listdir(custom_dir) if os.path.exists(custom_dir) else 'no dir'}"
        )
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        # restart with same DATA_FILE, should recover
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port, timeout=15), "server should recover with DATA_FILE"
        r = get_doc(f"http://127.0.0.1:{port}", "df1")
        assert r.status_code == 200, (
            f"doc should persist via DATA_FILE, got {r.status_code}"
        )
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir, ignore_errors=True)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def _compute_crc32_hex(data_bytes: bytes) -> str:
    return format(binascii.crc32(data_bytes) & 0xFFFFFFFF, "08x")


def test_wal_checksum_rejection():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert wait_for_server(port, timeout=15)
        base = f"http://127.0.0.1:{port}"
        r = ingest(
            base,
            [
                {
                    "id": "walgood1",
                    "timestamp": "2026-07-20T10:00:00Z",
                    "service": "s",
                    "level": "info",
                    "message": "good one",
                }
            ],
        )
        assert r.status_code == 201
        time.sleep(0.5)
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        wal_path = os.path.join(DATA_DIR, "wal.log")
        assert os.path.exists(wal_path), "wal.log should exist"
        with open(wal_path, "a") as f:
            from collections import OrderedDict
            # invalid checksum line
            bad_od = OrderedDict([("id", "badchecksum"), ("timestamp", "2026-07-20T10:00:00Z"), ("service", "s"), ("level", "info"), ("message", "should be rejected")])
            bad_doc_json = json.dumps(bad_od, separators=(",", ":"))
            bad_entry_str = '{"op":"index","doc":' + bad_doc_json + ',"ts":"2026-07-20T10:00:00Z","checksum":"deadbeef"}'
            f.write(bad_entry_str + "\n")
            # valid entry with correct checksum
            good_od = OrderedDict([("id", "walgood2"), ("timestamp", "2026-07-20T11:00:00Z"), ("service", "s"), ("level", "info"), ("message", "good two")])
            doc_json_str = json.dumps(good_od, separators=(",", ":"))
            checksum = _compute_crc32_hex(doc_json_str.encode())
            good_entry_str = '{"op":"index","doc":' + doc_json_str + ',"ts":"2026-07-20T11:00:00Z","checksum":"' + checksum + '"}'
            f.write(good_entry_str + "\n")
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port, timeout=15), (
            "server should start after injecting bad checksum"
        )
        base2 = f"http://127.0.0.1:{port}"
        r = get_doc(base2, "walgood1")
        assert r.status_code == 200, "original WAL doc should survive"
        r = get_doc(base2, "badchecksum")
        assert r.status_code == 404, (
            f"WAL line with bad checksum should be rejected, but got {r.status_code}"
        )
        r = get_doc(base2, "walgood2")
        assert r.status_code == 200, (
            f"valid WAL entry after bad line should be replayed, got {r.status_code}"
        )
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_wal_corrupt_line_replay():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert wait_for_server(port, timeout=15)
        base = f"http://127.0.0.1:{port}"
        r = ingest(
            base,
            [
                {
                    "id": "corrupttest1",
                    "timestamp": "2026-07-20T10:00:00Z",
                    "service": "s",
                    "level": "info",
                    "message": "before corrupt",
                }
            ],
        )
        assert r.status_code == 201
        time.sleep(0.5)
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        wal_path = os.path.join(DATA_DIR, "wal.log")
        assert os.path.exists(wal_path)
        with open(wal_path, "a") as f:
            f.write("this is not json at all\n")
            f.write('{"truncated":\n')
            f.write("\n")
            from collections import OrderedDict
            od = OrderedDict([("id", "aftercorrupt"), ("timestamp", "2026-07-20T11:00:00Z"), ("service", "s"), ("level", "info"), ("message", "after corrupt")])
            doc_json_str = json.dumps(od, separators=(",", ":"))
            checksum = _compute_crc32_hex(doc_json_str.encode())
            good_entry_str = '{"op":"index","doc":' + doc_json_str + ',"ts":"2026-07-20T11:00:00Z","checksum":"' + checksum + '"}'
            f.write(good_entry_str + "\n")
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port, timeout=15), (
            "server should recover despite corrupt WAL lines"
        )
        base2 = f"http://127.0.0.1:{port}"
        r = get_doc(base2, "corrupttest1")
        assert r.status_code == 200, "doc before corrupt line should survive"
        r = get_doc(base2, "aftercorrupt")
        assert r.status_code == 200, (
            "valid WAL entry after corrupt lines should be replayed"
        )
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)

