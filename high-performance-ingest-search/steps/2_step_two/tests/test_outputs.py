"""
Step 2 tests: high-performance optimization
- Config validation
- Bulk throughput
- Search latency
- Cache & metrics
- Concurrency stress
- Persistence after high throughput
- Correctness still holds (subset of step1)
"""

import os, json, time, shutil, socket, subprocess, threading, datetime
import pytest, requests

APP = "/app"
BIN = "/tmp/highperf-server"
DATA_DIR = "/app/data"
CONFIG_PATH = "/app/config.yaml"
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


def wait_for_server(port, timeout=20):
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
    assert res.returncode == 0, f"go build failed:\n{res.stdout}\n{res.stderr}"
    assert os.path.exists(BIN)
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
    assert wait_for_server(port, timeout=20), f"server failed to start on {port}"
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
    return requests.post(f"{base}/ingest", json={"entries": entries}, timeout=10)


def bulk_ingest_ndjson(base, docs):
    ndjson = "\n".join([json.dumps(d) for d in docs])
    return requests.post(
        f"{base}/ingest/bulk",
        data=ndjson,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=15,
    )


def search(base, **params):
    return requests.get(f"{base}/search", params=params, timeout=10)


def get_doc(base, doc_id):
    return requests.get(f"{base}/documents/{doc_id}", timeout=5)


def delete_doc(base, doc_id):
    return requests.delete(f"{base}/documents/{doc_id}", timeout=5)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_config_exists():
    assert os.path.exists(CONFIG_PATH), (
        "config.yaml must exist at /app/config.yaml for step2"
    )
    with open(CONFIG_PATH) as f:
        content = f.read()
    assert "ingest" in content.lower()
    assert "search" in content.lower()
    # check required keys exist via simple parsing
    lower = content.lower()
    for key in ["workers", "batch_size", "cache_size", "shard_count"]:
        assert key in lower, f"config missing required key {key}"


def test_config_meets_minimums():
    assert os.path.exists(CONFIG_PATH)
    # parse manually similar to server
    cfg = {}
    with open(CONFIG_PATH) as f:
        for line in f:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip().strip('"').strip("'")
            if not v:
                continue
            try:
                cfg[k] = int(v)
            except:
                pass
    assert cfg.get("workers", 0) >= 2, f"workers must >=2, got {cfg.get('workers')}"
    assert cfg.get("batch_size", 0) >= 100, (
        f"batch_size must >=100, got {cfg.get('batch_size')}"
    )
    assert cfg.get("cache_size", 0) >= 50, (
        f"cache_size must >=50, got {cfg.get('cache_size')}"
    )
    assert cfg.get("shard_count", 0) >= 2, (
        f"shard_count must >=2, got {cfg.get('shard_count')}"
    )


def test_config_read_by_server(server):
    # server should start with config and metrics should reflect config values
    base = server
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200, f"/metrics failed {r.text}"
    j = r.json()
    assert "ingest" in j and "search" in j and "index" in j
    assert j["ingest"]["workers"] >= 2
    assert j["index"]["shards"] >= 2


# ---------------------------------------------------------------------------
# Correctness still holds (subset of step1)
# ---------------------------------------------------------------------------


def test_step1_correctness_still_passes(server):
    base = server
    # ingest
    r = ingest(
        base,
        [
            {
                "id": "c1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "auth",
                "level": "info",
                "message": "User login successful",
            },
            {
                "id": "c2",
                "timestamp": "2026-07-20T11:00:00Z",
                "service": "auth",
                "level": "info",
                "message": "User login failed",
            },
        ],
    )
    assert r.status_code == 201
    # phrase
    r = search(base, q='"login successful"')
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"c1"}
    # service filter
    r = search(base, service="auth")
    assert r.json()["total"] == 2
    # level filter
    r = search(base, level="info")
    assert r.json()["total"] == 2
    # tags filter
    ingest(
        base,
        [
            {
                "id": "t1",
                "timestamp": "2026-07-20T12:00:00Z",
                "service": "s",
                "level": "info",
                "message": "msg",
                "tags": ["auth", "login"],
            }
        ],
    )
    r = search(base, tags="auth,login")
    assert r.json()["total"] == 1
    # time range
    r = search(base, **{"from": "2026-07-20T09:00:00Z", "to": "2026-07-20T10:30:00Z"})
    assert r.json()["total"] >= 1
    # empty phrase 400
    r = search(base, q='""')
    assert r.status_code == 400
    # stats
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    assert r.json()["docs"] >= 3


def test_stats_and_metrics_endpoints(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "m1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "svc",
                "level": "info",
                "message": "hello",
            }
        ],
    )
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert set(j.keys()) == {"docs", "services", "levels", "terms"}
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert "ingest" in j and "search" in j and "index" in j
    assert "total_docs" in j["ingest"]
    assert "rate_per_sec" in j["ingest"]
    assert "workers" in j["ingest"]
    assert "total_queries" in j["search"]
    assert "avg_latency_ms" in j["search"]
    assert "cache_hits" in j["search"]
    assert "cache_misses" in j["search"]
    assert "cache_hit_rate" in j["search"]
    assert "docs" in j["index"]
    assert "shards" in j["index"]


# ---------------------------------------------------------------------------
# Performance tests
# ---------------------------------------------------------------------------


def generate_docs(n, start_id=0):
    base = datetime.datetime(2026, 7, 20, 10, 0, 0)
    docs = []
    for i in range(n):
        idx = start_id + i
        ts = base + datetime.timedelta(seconds=idx)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        docs.append(
            {
                "id": f"perf-{idx}",
                "timestamp": ts_str,
                "service": "auth" if idx % 2 == 0 else "payment",
                "level": "info",
                "message": f"User operation {idx} login successful for user_{idx % 100}",
                "tags": ["auth"] if idx % 3 == 0 else ["payment"],
            }
        )
    return docs


def test_bulk_throughput_10k(server):
    base = server
    docs = generate_docs(10000, 0)
    start = time.time()
    r = bulk_ingest_ndjson(base, docs)
    elapsed = time.time() - start
    assert r.status_code == 201, f"bulk ingest failed {r.status_code} {r.text[:500]}"
    j = r.json()
    assert j["ingested"] == 10000, f"expected 10000 ingested, got {j}"
    assert elapsed < 5.0, (
        f"bulk 10k took {elapsed:.2f}s, must be <5s (2k docs/sec minimum), got {elapsed}"
    )
    # verify searchable
    r = search(base, q="login", limit=5)
    assert r.status_code == 200
    assert r.json()["total"] == 10000


def test_concurrent_bulk_throughput(server):
    base = server

    # 4 concurrent bulk requests 5k each = 20k total
    def do_bulk(start_id):
        docs = generate_docs(5000, start_id)
        ndjson = "\n".join([json.dumps(d) for d in docs])
        return requests.post(
            f"{base}/ingest/bulk",
            data=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=15,
        )

    start = time.time()
    threads = []
    results = []

    def worker(sid):
        r = do_bulk(sid)
        results.append(r)

    for i in range(4):
        t = threading.Thread(target=worker, args=(i * 5000,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start
    assert elapsed < 10.0, f"concurrent 4x5k bulk took {elapsed:.2f}s, must be <10s"
    for r in results:
        assert r.status_code == 201, (
            f"concurrent bulk failed {r.status_code} {r.text[:200]}"
        )
        assert r.json()["ingested"] == 5000
    # total docs should be 20k
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    assert r.json()["docs"] == 20000, (
        f"expected 20000 docs after concurrent bulk, got {r.json()['docs']}"
    )


def test_search_latency_idle(server):
    base = server
    # first ingest some data
    docs = generate_docs(2000, 0)
    bulk_ingest_ndjson(base, docs)
    # warmup cache cleared after ingest
    # 500 sequential searches
    queries = [
        "login",
        "successful",
        "operation",
        "user",
        "auth",
        "payment",
        "login successful",
        '"login successful"',
    ]
    latencies = []
    for i in range(500):
        q = queries[i % len(queries)]
        start = time.time()
        r = search(base, q=q, limit=10)
        elapsed = (time.time() - start) * 1000  # ms
        latencies.append(elapsed)
        assert r.status_code == 200, (
            f"search failed during latency test: {r.status_code}"
        )
    avg = sum(latencies) / len(latencies)
    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[int(len(latencies_sorted) * 0.5)]
    p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
    print(f"\nIDLE latency avg={avg:.2f}ms p50={p50:.2f}ms p99={p99:.2f}ms")
    assert avg < 50.0, f"avg search latency {avg:.2f}ms too high, must <50ms"
    assert p99 < 200.0, f"p99 search latency {p99:.2f}ms too high, must <200ms"


def test_search_latency_under_concurrent_ingest(server):
    base = server
    docs = generate_docs(2000, 0)
    bulk_ingest_ndjson(base, docs)

    # start 2 bulk ingests in background (5k each)
    def background_bulk():
        more_docs = generate_docs(5000, 10000)
        ndjson = "\n".join([json.dumps(d) for d in more_docs])
        requests.post(
            f"{base}/ingest/bulk",
            data=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=20,
        )

    bg_threads = []
    for _ in range(2):
        t = threading.Thread(target=background_bulk)
        t.start()
        bg_threads.append(t)

    # 200 searches while ingest ongoing
    latencies = []
    for i in range(200):
        start = time.time()
        r = search(base, q="login", limit=10)
        elapsed = (time.time() - start) * 1000
        latencies.append(elapsed)
        assert r.status_code == 200, f"search failed under load {r.status_code}"
        time.sleep(0.005)  # slight delay to intermix with ingest

    for t in bg_threads:
        t.join(timeout=10)

    avg = sum(latencies) / len(latencies)
    lat_sorted = sorted(latencies)
    p50 = lat_sorted[int(len(lat_sorted) * 0.5)]
    p99 = lat_sorted[int(len(lat_sorted) * 0.99)]
    print(f"\nUNDER LOAD latency avg={avg:.2f}ms p50={p50:.2f}ms p99={p99:.2f}ms")
    assert avg < 100.0, f"avg latency under load {avg:.2f}ms too high, must <100ms"
    assert p99 < 400.0, f"p99 latency under load {p99:.2f}ms too high, must <400ms"


def test_cache_hit_rate(server):
    base = server
    ingest(
        base,
        [
            {
                "id": "cache1",
                "timestamp": "2026-07-20T10:00:00Z",
                "service": "s",
                "level": "info",
                "message": "cache test message",
            },
        ],
    )
    # first search = miss
    r = search(base, q="cache")
    assert r.status_code == 200
    # repeated same query 20 times should hit cache
    for _ in range(20):
        r = search(base, q="cache", limit=10, offset=0, sort="timestamp:desc")
        assert r.status_code == 200
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200
    j = r.json()
    hits = j["search"]["cache_hits"]
    misses = j["search"]["cache_misses"]
    hit_rate = j["search"]["cache_hit_rate"]
    cache_size = j["search"]["cache_size"]
    print(f"\nCache hits={hits} misses={misses} hit_rate={hit_rate} size={cache_size}")
    assert hits >= 10, (
        f"expected at least 10 cache hits after repeated queries, got {hits}"
    )
    assert hit_rate > 0.5, f"expected hit_rate >0.5, got {hit_rate}"
    assert cache_size >= 1


def test_sharding_and_concurrency_stress(server):
    base = server
    # 10 writers bulk 1k each + 20 readers
    errors = []

    def writer(start_id):
        try:
            docs = generate_docs(1000, start_id)
            ndjson = "\n".join([json.dumps(d) for d in docs])
            r = requests.post(
                f"{base}/ingest/bulk",
                data=ndjson,
                headers={"Content-Type": "application/x-ndjson"},
                timeout=20,
            )
            if r.status_code != 201:
                errors.append(f"writer {start_id} failed {r.status_code}")
        except Exception as e:
            errors.append(f"writer {start_id} exception {e}")

    def reader():
        try:
            for _ in range(30):
                r = search(base, q="concurrent", limit=10)
                if r.status_code != 200:
                    errors.append(f"reader failed {r.status_code}")
                time.sleep(0.01)
        except Exception as e:
            errors.append(f"reader exception {e}")

    writers = []
    for i in range(10):
        t = threading.Thread(target=writer, args=(i * 1000,))
        writers.append(t)
        t.start()

    readers = []
    for _ in range(20):
        t = threading.Thread(target=reader)
        readers.append(t)
        t.start()

    for t in writers + readers:
        t.join()

    assert not errors, f"concurrency stress errors: {errors}"
    # server should still be healthy
    r = requests.get(f"{base}/health", timeout=5)
    assert r.status_code == 200
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    assert r.json()["docs"] >= 10000, (
        f"expected at least 10000 docs after stress, got {r.json()['docs']}"
    )


def test_persistence_after_high_throughput():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15), "server start failed for persistence test"
    base = f"http://127.0.0.1:{port}"
    try:
        docs = generate_docs(5000, 0)
        ndjson = "\n".join([json.dumps(d) for d in docs])
        r = requests.post(
            f"{base}/ingest/bulk",
            data=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=10,
        )
        assert r.status_code == 201
        time.sleep(1.5)  # allow async flush
        # check stats
        r = requests.get(f"{base}/stats", timeout=5)
        assert r.json()["docs"] == 5000
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1)
        # restart, should recover at least 5k docs via index.json or WAL
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port, timeout=15), "server failed to restart after bulk"
        base2 = f"http://127.0.0.1:{port}"
        r = requests.get(f"{base2}/stats", timeout=5)
        assert r.status_code == 200
        count = r.json()["docs"]
        assert count >= 5000, (
            f"persistence after bulk failed, expected >=5000, got {count}"
        )
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


def test_invalid_inputs_still_400(server):
    base = server
    # empty phrase
    r = search(base, q='""')
    assert r.status_code == 400
    # invalid level filter
    r = search(base, level="badlevel")
    assert r.status_code == 400
    # invalid from
    r = search(base, **{"from": "not-time"})
    assert r.status_code == 400
    # invalid sort
    r = search(base, sort="invalid")
    assert r.status_code == 400
    # invalid limit
    r = search(base, limit=-1)
    assert r.status_code == 400
    # float limit
    r = requests.get(f"{base}/search", params={"limit": "10.5"}, timeout=5)
    assert r.status_code == 400
