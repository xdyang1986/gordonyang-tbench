"""
Step 2 tests: high-performance optimization
"""

import os, json, time, shutil, socket, subprocess, threading, datetime, binascii, re
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


def _strip_inline_comment(s: str) -> str:
    if "#" in s:
        s = s.split("#", 1)[0]
    return s.strip()


def test_config_exists():
    assert os.path.exists(CONFIG_PATH), (
        "config.yaml must exist at /app/config.yaml for step2"
    )
    with open(CONFIG_PATH) as f:
        content = f.read()
    assert "ingest" in content.lower()
    assert "search" in content.lower()
    lower = content.lower()
    for key in ["workers", "batch_size", "cache_size", "shard_count"]:
        assert key in lower, f"config missing required key {key}"


def test_config_meets_minimums():
    assert os.path.exists(CONFIG_PATH)
    cfg = {}
    with open(CONFIG_PATH) as f:
        for line in f:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = _strip_inline_comment(v.strip())
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


def test_config_inline_comment_parsing():
    tmp_cfg = "/tmp/test_inline_config.yaml"
    with open(tmp_cfg, "w") as f:
        f.write("""
ingest:
  workers: 4  # worker pool size
  batch_size: 500  # batch flush size
  flush_interval_ms: 100  # flush interval
  queue_size: 10000  # queue size
search:
  cache_size: 100  # cache size
  cache_ttl_ms: 5000  # ttl
  shard_count: 4  # shards
persistence:
  async_writes: true
  wal_buffer_size: 1000
  sync_interval_ms: 1000
server:
  read_timeout_ms: 5000
  write_timeout_ms: 10000
  max_concurrent_search: 50
""")
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    orig_content = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as of:
            orig_content = of.read()
    shutil.copyfile(tmp_cfg, CONFIG_PATH)
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait_for_server(port, timeout=15), "server failed to start with inline-comment config"
        base = f"http://127.0.0.1:{port}"
        r = requests.get(f"{base}/metrics", timeout=5)
        assert r.status_code == 200
        j = r.json()
        assert j["ingest"]["workers"] >= 2
        assert j["index"]["shards"] >= 2
        proc.terminate()
        proc.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        if orig_content is not None:
            with open(CONFIG_PATH, "w") as f:
                f.write(orig_content)
        else:
            if os.path.exists(CONFIG_PATH):
                os.remove(CONFIG_PATH)
        if os.path.exists(tmp_cfg):
            os.remove(tmp_cfg)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_config_read_by_server(server):
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
    r = search(base, q='"login successful"')
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"c1"}
    r = search(base, service="auth")
    assert r.json()["total"] == 2
    r = search(base, level="info")
    assert r.json()["total"] == 2
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
    r = search(base, **{"from": "2026-07-20T09:00:00Z", "to": "2026-07-20T10:30:00Z"})
    assert r.json()["total"] >= 1
    r = search(base, q='""')
    assert r.status_code == 400
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
    assert elapsed < 8.0, (
        f"bulk 10k took {elapsed:.2f}s, must be <8s (relaxed from 5s for 2 CPU), got {elapsed}"
    )
    r = search(base, q="login", limit=5)
    assert r.status_code == 200
    assert r.json()["total"] == 10000


def test_concurrent_bulk_throughput(server):
    base = server

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
    assert elapsed < 15.0, f"concurrent 4x5k bulk took {elapsed:.2f}s, must be <15s (relaxed)"
    for r in results:
        assert r.status_code == 201, (
            f"concurrent bulk failed {r.status_code} {r.text[:200]}"
        )
        assert r.json()["ingested"] == 5000
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    assert r.json()["docs"] == 20000, (
        f"expected 20000 docs after concurrent bulk, got {r.json()['docs']}"
    )


def test_search_latency_idle(server):
    base = server
    docs = generate_docs(2000, 0)
    bulk_ingest_ndjson(base, docs)
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
        elapsed = (time.time() - start) * 1000
        latencies.append(elapsed)
        assert r.status_code == 200, (
            f"search failed during latency test: {r.status_code}"
        )
    avg = sum(latencies) / len(latencies)
    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[int(len(latencies_sorted) * 0.5)]
    p99 = latencies_sorted[int(len(latencies_sorted) * 0.99)]
    print(f"\nIDLE latency avg={avg:.2f}ms p50={p50:.2f}ms p99={p99:.2f}ms")
    assert avg < 100.0, f"avg search latency {avg:.2f}ms too high, must <100ms (relaxed)"
    assert p99 < 500.0, f"p99 search latency {p99:.2f}ms too high, must <500ms (relaxed)"


def test_search_latency_under_concurrent_ingest(server):
    base = server
    docs = generate_docs(2000, 0)
    bulk_ingest_ndjson(base, docs)

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

    latencies = []
    for i in range(200):
        start = time.time()
        r = search(base, q="login", limit=10)
        elapsed = (time.time() - start) * 1000
        latencies.append(elapsed)
        assert r.status_code == 200, f"search failed under load {r.status_code}"
        time.sleep(0.005)

    for t in bg_threads:
        t.join(timeout=10)

    avg = sum(latencies) / len(latencies)
    lat_sorted = sorted(latencies)
    p50 = lat_sorted[int(len(lat_sorted) * 0.5)]
    p99 = lat_sorted[int(len(lat_sorted) * 0.99)]
    print(f"\nUNDER LOAD latency avg={avg:.2f}ms p50={p50:.2f}ms p99={p99:.2f}ms")
    assert avg < 200.0, f"avg latency under load {avg:.2f}ms too high, must <200ms (relaxed)"
    assert p99 < 1000.0, f"p99 latency under load {p99:.2f}ms too high, must <1000ms (relaxed)"


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
    r = search(base, q="cache")
    assert r.status_code == 200
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
        time.sleep(1.5)
        r = requests.get(f"{base}/stats", timeout=5)
        assert r.json()["docs"] == 5000
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1)
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
    r = search(base, q='""')
    assert r.status_code == 400
    r = search(base, level="badlevel")
    assert r.status_code == 400
    r = search(base, **{"from": "not-time"})
    assert r.status_code == 400
    r = search(base, sort="invalid")
    assert r.status_code == 400
    r = search(base, limit=-1)
    assert r.status_code == 400
    r = requests.get(f"{base}/search", params={"limit": "10.5"}, timeout=5)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Additional coverage gap tests - behavioral, not source grep
# ---------------------------------------------------------------------------


def _fnv1a32(s: str) -> int:
    h = 2166136261
    prime = 16777619
    for b in s.encode('utf-8'):
        h ^= b
        h = (h * prime) & 0xFFFFFFFF
    return h


def _compute_crc32_hex(data_bytes: bytes) -> str:
    return format(binascii.crc32(data_bytes) & 0xFFFFFFFF, "08x")


def test_go_mod_forbidden_libs():
    go_mod = os.path.join(APP, "go.mod")
    if not os.path.exists(go_mod):
        pytest.skip("go.mod not found")
    with open(go_mod) as f:
        content = f.read().lower()
    forbidden = ["bleve", "elastic", "elasticsearch", "algolia", "meilisearch", "sonic", "tantivy", "lucene"]
    for lib in forbidden:
        assert lib not in content, f"go.mod contains forbidden lib {lib}"


def test_data_file_env_handling():
    port = find_free_port()
    custom_dir = "/tmp/customdata_test2"
    custom_data_file = os.path.join(custom_dir, "myindex.json")
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_data_file}
    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait_for_server(port, timeout=15), "server failed to start with DATA_FILE env"
        base = f"http://127.0.0.1:{port}"
        r = ingest(base, [{"id": "df1", "timestamp": "2026-07-20T10:00:00Z", "service": "s", "level": "info", "message": "data file test"}])
        assert r.status_code == 201
        time.sleep(0.8)
        custom_wal = os.path.join(custom_dir, "wal.log")
        assert os.path.exists(custom_data_file) or os.path.exists(custom_wal), f"DATA_FILE handling failed"
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        proc2 = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port, timeout=15), "server should recover with DATA_FILE"
        r = get_doc(f"http://127.0.0.1:{port}", "df1")
        assert r.status_code == 200, f"doc should persist via DATA_FILE, got {r.status_code}"
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


def test_wal_checksum_rejection():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait_for_server(port, timeout=15)
        base = f"http://127.0.0.1:{port}"
        r = ingest(base, [{"id": "walgood1", "timestamp": "2026-07-20T10:00:00Z", "service": "s", "level": "info", "message": "good one"}])
        assert r.status_code == 201
        time.sleep(0.5)
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        wal_path = os.path.join(DATA_DIR, "wal.log")
        assert os.path.exists(wal_path)
        with open(wal_path, "a") as f:
            from collections import OrderedDict
            bad_od = OrderedDict([("message", "should be rejected"), ("id", "badchecksum"), ("service", "s"), ("level", "info"), ("timestamp", "2026-07-20T10:00:00Z"), ("tags", [])])
            bad_doc_json = json.dumps(bad_od, separators=(",", ":"))
            bad_entry_str = '{"op":"index","doc":' + bad_doc_json + ',"ts":"2026-07-20T10:00:00Z","checksum":"deadbeef"}'
            f.write(bad_entry_str + "\n")
            good_od = OrderedDict([("message", "good two"), ("id", "walgood2"), ("service", "s"), ("level", "info"), ("timestamp", "2026-07-20T11:00:00Z"), ("tags", [])])
            doc_json_str = json.dumps(good_od, separators=(", ", ": "))  # non-canonical: spaces, different order, tags
            checksum = _compute_crc32_hex(doc_json_str.encode())
            good_entry_str = '{"op":"index","doc":' + doc_json_str + ',"ts":"2026-07-20T11:00:00Z","checksum":"' + checksum + '"}'
            f.write(good_entry_str + "\n")
        proc2 = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port, timeout=15), "server should start after bad checksum"
        base2 = f"http://127.0.0.1:{port}"
        assert get_doc(base2, "walgood1").status_code == 200
        assert get_doc(base2, "badchecksum").status_code == 404, "bad checksum should be rejected"
        assert get_doc(base2, "walgood2").status_code == 200, "valid entry after bad should be replayed"
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
    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait_for_server(port, timeout=15)
        base = f"http://127.0.0.1:{port}"
        r = ingest(base, [{"id": "corrupttest1", "timestamp": "2026-07-20T10:00:00Z", "service": "s", "level": "info", "message": "before corrupt"}])
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
            od = OrderedDict([("message", "after corrupt"), ("id", "aftercorrupt"), ("service", "s"), ("level", "info"), ("timestamp", "2026-07-20T11:00:00Z"), ("tags", [])])
            doc_json_str = json.dumps(od, separators=(", ", ": "))  # non-canonical with spaces
            checksum = _compute_crc32_hex(doc_json_str.encode())
            good_entry_str = '{"op":"index","doc":' + doc_json_str + ',"ts":"2026-07-20T11:00:00Z","checksum":"' + checksum + '"}'
            f.write(good_entry_str + "\n")
        proc2 = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port, timeout=15), "server should recover despite corrupt WAL lines"
        base2 = f"http://127.0.0.1:{port}"
        assert get_doc(base2, "corrupttest1").status_code == 200
        assert get_doc(base2, "aftercorrupt").status_code == 200
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


def test_cache_invalidation_after_writes(server):
    base = server
    r = ingest(base, [{"id": "cacheinv1", "timestamp": "2026-07-20T10:00:00Z", "service": "s", "level": "info", "message": "cache invalidation test"}])
    assert r.status_code == 201
    r = search(base, q="invalidation")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r = search(base, q="invalidation")
    assert r.status_code == 200
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200
    r = ingest(base, [{"id": "cacheinv2", "timestamp": "2026-07-20T11:00:00Z", "service": "s", "level": "info", "message": "cache invalidation test second"}])
    assert r.status_code == 201
    r = search(base, q="invalidation")
    assert r.status_code == 200
    assert r.json()["total"] == 2, f"cache should be invalidated after write, expected 2 got {r.json()['total']}"
    r = requests.delete(f"{base}/documents/cacheinv1", timeout=5)
    assert r.status_code == 200
    r = search(base, q="invalidation")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert "cacheinv1" not in ids
    assert "cacheinv2" in ids


def test_actual_sharding_behavioral(server):
    """
    Behavioral check for real sharding: verifies metrics shards==config, per-shard doc counts
    match FNV-1a hash distribution, and config change is honored. No source grep.
    """
    base = server
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert "index" in j and "shards" in j["index"] and "shard_docs" in j["index"], f"/metrics must include index.shards and index.shard_docs, got {j['index']}"
    shards_reported = j["index"]["shards"]
    shard_docs = j["index"]["shard_docs"]
    assert shards_reported >= 2
    assert isinstance(shard_docs, list)
    assert len(shard_docs) == shards_reported
    assert sum(shard_docs) == j["index"]["docs"]

    cfg_shards = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k = k.strip().lower()
                v = _strip_inline_comment(v.strip()).strip().strip('"').strip("'")
                if k == "shard_count":
                    try:
                        cfg_shards = int(v)
                    except:
                        pass
    if cfg_shards:
        assert shards_reported == cfg_shards

    # Known ids FNV distribution on fresh server with empty start (via restart)
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    with open(CONFIG_PATH) as f:
        orig = f.read()
    try:
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port, timeout=15)
        base2 = f"http://127.0.0.1:{port}"
        r = requests.get(f"{base2}/metrics", timeout=5)
        assert r.status_code == 200
        shards0 = r.json()["index"]["shards"]
        test_ids = [f"dist-{i:05d}" for i in range(200)]
        expected = [0] * shards0
        for tid in test_ids:
            expected[_fnv1a32(tid) % shards0] += 1
        docs = [{"id": tid, "timestamp": "2026-07-20T10:00:00Z", "service": "s", "level": "info", "message": "dist test"} for tid in test_ids]
        ndjson = "\n".join([json.dumps(d) for d in docs])
        r = requests.post(f"{base2}/ingest/bulk", data=ndjson, headers={"Content-Type": "application/x-ndjson"}, timeout=15)
        assert r.status_code == 201
        r = requests.get(f"{base2}/metrics", timeout=5)
        sd = r.json()["index"]["shard_docs"]
        assert sd == expected, f"shard_docs {sd} must match FNV-1a distribution {expected}"
        proc.terminate()
        proc.wait(timeout=5)
    finally:
        with open(CONFIG_PATH, 'w') as f:
            f.write(orig)
        try:
            proc.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)

    # Bulk 2k quick
    docs2 = generate_docs(2000, 100000)
    start = time.time()
    r = bulk_ingest_ndjson(base, docs2)
    assert r.status_code == 201
    assert r.json()["ingested"] == 2000
    assert time.time() - start < 5.0

    # Config change to 8
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    with open(CONFIG_PATH) as f:
        orig = f.read()
    try:
        new_cfg = re.sub(r'shard_count\s*:\s*\d+', 'shard_count: 8', orig, flags=re.IGNORECASE)
        with open(CONFIG_PATH, 'w') as f:
            f.write(new_cfg)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port, timeout=15)
        base2 = f"http://127.0.0.1:{port}"
        r = requests.get(f"{base2}/metrics", timeout=5)
        assert r.json()["index"]["shards"] == 8
        assert len(r.json()["index"]["shard_docs"]) == 8
        proc.terminate()
        proc.wait(timeout=5)
    finally:
        with open(CONFIG_PATH, 'w') as f:
            f.write(orig)
        try:
            proc.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_worker_pool_and_batching_config_semantics(server):
    base = server
    assert os.path.exists(CONFIG_PATH)
    with open(CONFIG_PATH) as f:
        config_content = f.read().lower()
    required_keys = ["workers", "batch_size", "queue_size", "flush_interval_ms", "cache_size", "cache_ttl_ms", "shard_count", "wal_buffer_size", "sync_interval_ms"]
    for key in required_keys:
        assert key in config_content, f"config missing key {key}"
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200
    j = r.json()
    workers = j["ingest"]["workers"]
    assert workers >= 2
    assert "queue_depth" in j["ingest"]
    assert isinstance(j["ingest"]["queue_depth"], int)
    docs = generate_docs(1500, 99999)
    r = bulk_ingest_ndjson(base, docs)
    assert r.status_code == 201
    assert r.json()["ingested"] == 1500


def test_config_all_fields_present():
    assert os.path.exists(CONFIG_PATH)
    with open(CONFIG_PATH) as f:
        content = f.read()
    lower = content.lower()
    for k in ["ingest:", "workers", "batch_size", "flush_interval_ms", "queue_size"]:
        assert k in lower, f"config missing ingest field {k}"
    for k in ["search:", "cache_size", "cache_ttl_ms", "shard_count"]:
        assert k in lower, f"config missing search field {k}"
    for k in ["persistence:", "async_writes", "wal_buffer_size", "sync_interval_ms"]:
        assert k in lower, f"config missing persistence field {k}"
    for k in ["server:", "read_timeout_ms", "write_timeout_ms"]:
        assert k in lower, f"config missing server field {k}"


def test_search_unclosed_quote_400(server):
    base = server
    # Unclosed quote should be 400, not 200 or 500
    r = search(base, q='"unclosed')
    assert r.status_code == 400, f"unclosed quote should 400, got {r.status_code}"
    r = search(base, q=' "login ')
    assert r.status_code == 400
    r = search(base, q='"')
    assert r.status_code == 400


def test_bulk_application_json_content_type(server):
    base = server
    docs = generate_docs(100, 300000)
    ndjson = "\n".join([json.dumps(d) for d in docs])
    # Send with application/json content-type but body is still NDJSON lines
    r = requests.post(
        f"{base}/ingest/bulk",
        data=ndjson,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    assert r.status_code == 201, f"bulk with application/json should be accepted, got {r.status_code} {r.text[:200]}"
    assert r.json()["ingested"] == 100


def test_persistence_sigkill_immediate():
    """
    SIGKILL immediately after ack durability: kill -9 right after bulk ingest returns 201,
    without sleep, then verify WAL replay recovers.
    """
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait_for_server(port, timeout=15)
        base = f"http://127.0.0.1:{port}"
        docs = generate_docs(2000, 400000)
        ndjson = "\n".join([json.dumps(d) for d in docs])
        r = requests.post(
            f"{base}/ingest/bulk",
            data=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=10,
        )
        assert r.status_code == 201
        assert r.json()["ingested"] == 2000
        # Immediate SIGKILL, no sleep
        proc.kill()
        proc.wait(timeout=5)
        time.sleep(1)
        proc2 = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port, timeout=15), "server should restart after SIGKILL"
        base2 = f"http://127.0.0.1:{port}"
        r = requests.get(f"{base2}/stats", timeout=5)
        assert r.status_code == 200
        # At least 2000 docs should be recovered via WAL, even without graceful shutdown
        assert r.json()["docs"] >= 2000, f"SIGKILL durability failed, expected >=2000 got {r.json()['docs']}"
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
        try:
            proc.terminate()
        except:
            pass
        try:
            proc.kill()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_worker_pool_backpressure(server):
    base = server
    # Burst of bulk requests without waiting, should not crash and queue_depth metric should exist
    def burst_bulk(start_id):
        docs = generate_docs(500, start_id)
        ndjson = "\n".join([json.dumps(d) for d in docs])
        return requests.post(
            f"{base}/ingest/bulk",
            data=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=15,
        )

    threads = []
    results = []

    def worker(sid):
        r = burst_bulk(sid)
        results.append(r)

    # 10 concurrent bursts
    for i in range(10):
        t = threading.Thread(target=worker, args=(500000 + i * 500,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    for r in results:
        assert r.status_code == 201, f"backpressure test failed {r.status_code}"
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    assert r.json()["docs"] >= 5000
    r = requests.get(f"{base}/metrics", timeout=5)
    assert r.status_code == 200
    assert "queue_depth" in r.json()["ingest"]
