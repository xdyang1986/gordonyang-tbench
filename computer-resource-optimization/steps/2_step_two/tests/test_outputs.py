"""
Turn2 moderate 40 tests for efficient cluster.
"""
import os, json, hashlib, subprocess, time, threading
import pytest

APP = "/app"
BIN = "/app/cluster-manager"
CONFIG_PATH = "/app/config.json"
DATA_DIR = "/app/data"

GO_ENV = {**os.environ, "GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod", "GOCACHE": "/tmp/gocache", "GOPATH": "/tmp/gopath"}

@pytest.fixture(scope="session", autouse=True)
def built():
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(["go", "mod", "init", "cluster-manager"], cwd=APP, env=GO_ENV, capture_output=True, text=True)
    def _build(pkg):
        return subprocess.run(["go", "build", "-o", BIN, pkg], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=240)
    r = _build(".")
    if r.returncode != 0:
        for root, _, files in os.walk(APP):
            for f in files:
                if f.endswith(".go") and "func main(" in open(os.path.join(root,f)).read():
                    rel = os.path.relpath(root, APP)
                    pkg = "." if rel=="." else "./"+rel
                    r = _build(pkg)
                    break
    assert r.returncode == 0
    assert os.path.exists(BIN)
    yield

def write_config(cfg, path=CONFIG_PATH):
    json.dump(cfg, open(path, "w"), indent=2)

def default_config():
    return {
        "shard_count": 4,
        "shards": [
            {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
            {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
            {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
            {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}
        ],
        "rate_limit": {"allocations_per_second": 1000, "burst": 10000},
        "node_heartbeat_ttl_seconds": 60,
        "ops_log": "/app/data/cluster_ops.log",
        "jobs_path": "/app/data/jobs.json",
        "presence_path": "/app/data/presence.json",
        "rate_limit_path": "/app/data/rate_limit.json",
        "counter_path": "/app/data/counter.json"
    }

def clean_all():
    for p in ["/app/data/cluster.json", "/app/data/cluster.json.lock", "/app/data/global.lock",
              "/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json",
              "/app/data/jobs.json", "/app/data/presence.json", "/app/data/rate_limit.json", "/app/data/counter.json",
              "/app/data/cluster_ops.log", "/app/data/nodes_index.json"]:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    try:
        for fname in os.listdir(DATA_DIR):
            if ".corrupt." in fname or fname.endswith(".lock") or fname.endswith(".bak"):
                try:
                    os.remove(os.path.join(DATA_DIR, fname))
                except:
                    pass
    except FileNotFoundError:
        pass
    for fb in ["/tmp/backup", "/tmp/backup.json"]:
        try:
            if os.path.isdir(fb):
                import shutil
                shutil.rmtree(fb)
            else:
                os.remove(fb)
        except FileNotFoundError:
            pass
    write_config(default_config())

def run_config(*args, config_path=CONFIG_PATH, timeout=15):
    cmd = [BIN, "--config", config_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def checksum_valid_generic(path):
    if not os.path.exists(path):
        return True
    txt = open(path, "r", encoding="utf-8").read().strip()
    if txt == "":
        return True
    try:
        obj = json.loads(txt)
    except:
        return False
    if "data" not in obj or "checksum" not in obj or not obj["checksum"]:
        return False
    canonical = json.dumps(obj["data"], sort_keys=True, separators=(',', ':'))
    return obj["checksum"] == hashlib.md5(canonical.encode()).hexdigest()

def test_help_sharded():
    clean_all()
    r = run_config()
    assert r.returncode == 0
    out = r.stdout.lower()
    for kw in ["add-node", "get-shard-id", "get-shard-path", "distribution", "heartbeat", "get-node-health", "list-healthy", "snapshot", "restore", "ops-log", "optimize", "data", "checksum", "shard", "weight"]:
        assert kw in out

def test_config_validation():
    clean_all()
    bad = default_config()
    bad["shard_count"] = 0
    write_config(bad)
    r = run_config("list-nodes")
    assert r.returncode == 2 and r.stdout.strip() == ""
    bad = default_config()
    bad["shards"] = [{"id": 0, "path": "/app/data/shard_0.json"}, {"id": 0, "path": "/app/data/shard_1.json"}]
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    bad = default_config()
    bad["shards"][0]["path"] = ""
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    with open(CONFIG_PATH, "w") as f:
        f.write("{ invalid")
    assert run_config("list-nodes").returncode == 2
    clean_all()

def test_unknown_fields_tolerance():
    clean_all()
    cfg = default_config()
    cfg["future_field"] = 123
    cfg["shards"][0]["future_shard_field"] = "x"
    write_config(cfg)
    assert run_config("add-node", "node1", "4", "1024", "0").returncode == 0

def test_weighted_sharding():
    clean_all()
    def hash_weighted(key, shards):
        import hashlib
        tot = sum(s.get("weight",1) for s in shards)
        if key.startswith("global:"):
            return -1
        h = int(hashlib.md5(key.encode()).hexdigest(), 16)
        idx = h % tot
        for s in sorted(shards, key=lambda s: s["id"]):
            w = s.get("weight",1)
            if idx < w:
                return s["id"]
            idx -= w
        return sorted(shards, key=lambda s: s["id"])[-1]["id"]
    cfg = default_config()
    for key in [f"node-{i}" for i in range(15)]:
        assert int(run_config("get-shard-id", key).stdout.strip()) == hash_weighted(key, cfg["shards"])
    assert int(run_config("get-shard-id", "global:cfg1").stdout.strip()) == -1

def test_distribution():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    assert sum(dist.values()) == 20

def test_turn1_works_in_sharded():
    clean_all()
    assert run_config("add-node", "nodeA", "4", "1024", "1").returncode == 0
    assert run_config("add-job", "jobA", "1", "256", "0").returncode == 0
    assert run_config("allocate", "jobA", "nodeA").returncode == 0
    assert "jobA" in json.loads(run_config("get-node", "nodeA").stdout)["jobs"]

def test_pagination_nodes():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i:02d}", "4", "1024", "0")
    assert len(json.loads(run_config("list-nodes", "5", "0").stdout)) == 5
    assert len(json.loads(run_config("list-nodes", "5", "5").stdout)) == 5
    assert json.loads(run_config("list-nodes", "5", "100").stdout) == []
    assert run_config("list-nodes", "-1", "0").returncode == 2

def test_pagination_jobs():
    clean_all()
    run_config("add-node", "node1", "10", "10240", "0")
    for i in range(15):
        run_config("add-job", f"job-{i:02d}", "1", "256", "0")
    assert len(json.loads(run_config("list-jobs", "5", "0").stdout)) == 5

def test_best_fit_efficient():
    clean_all()
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "job1", "2", "512", "0")
    assert json.loads(run_config("schedule", "job1").stdout)["node_id"] == "nodeB"

def test_heartbeat_presence():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    assert run_config("heartbeat", "nodeA").returncode == 0
    assert json.loads(run_config("get-node-health", "nodeA").stdout)["online"] is True
    assert "nodeA" in json.loads(run_config("list-healthy").stdout)

def test_concurrent_sharded():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_config("add-job", f"job{i}", "1", "100", "0")
    def alloc(j):
        run_config("allocate", f"job{j}", "node1")
    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_config("get-node", "node1").stdout)["jobs"]) == 20
    assert not os.path.exists("/app/data/global.lock")

def test_checksum_all():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("heartbeat", "node1")
    for p in ["/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json", "/app/data/jobs.json", "/app/data/presence.json", "/app/data/rate_limit.json"]:
        if os.path.exists(p):
            assert checksum_valid_generic(p)

def test_raw_unescaped_and_unicode():
    clean_all()
    run_config("add-node", "node<>&", "4", "1024", "0")
    found = False
    for s in default_config()["shards"]:
        if os.path.exists(s["path"]) and "node<>&" in open(s["path"]).read():
            assert "<" in open(s["path"]).read()
            found = True
            break
    assert found
    run_config("add-node", "node-🌍", "4", "1024", "0")
    assert "🌍" in json.loads(run_config("get-node", "node-🌍").stdout)["id"]

def test_config_missing_count_empty_shards():
    clean_all()
    bad = {"shards": [{"id": 0, "path": "/app/data/shard_0.json"}]}
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    bad = {"shard_count": 2, "shards": []}
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    clean_all()

def test_get_shard_id_empty_string():
    clean_all()
    r = run_config("get-shard-id", "")
    assert r.returncode == 0
    assert int(r.stdout.strip()) in [0,1,2,3]

def test_get_shard_path_normal():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    sid = int(run_config("get-shard-id", "nodeA").stdout.strip())
    r = run_config("get-shard-path", "nodeA")
    assert r.returncode == 0
    cfg = default_config()
    for s in cfg["shards"]:
        if s["id"] == sid:
            assert r.stdout.strip() == s["path"]

def test_heartbeat_nonexist_fails():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    assert run_config("heartbeat", "noexist").returncode == 2

def test_file_lock_cleanup_sharded():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists("/app/data/global.lock")
    run_config("add-job", "job1", "1", "256", "0")
    assert not os.path.exists("/app/data/global.lock")
    run_config("allocate", "job1", "node1")
    assert not os.path.exists("/app/data/global.lock")

def test_idempotent_sharded():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    assert run_config("add-node", "node1", "8", "2048", "0").returncode == 0
    assert json.loads(run_config("get-node", "node1").stdout)["total"]["cpu"] == 4

def test_edge_validation_sharded():
    clean_all()
    assert run_config("add-node", "", "4", "1024", "0").returncode == 2
    assert run_config("get-node", "nonexist-shard-node").returncode == 2
    assert run_config("list-nodes", "-1", "0").returncode == 2
