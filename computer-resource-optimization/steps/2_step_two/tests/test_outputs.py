"""
Turn2 tests for efficient large-scale cluster management.
Covers sharding, best-fit, pagination, presence TTL, rate limiting per-node, snapshot/restore, ops-log, optimize, concurrency, checksum, config validation.
"""
import os
import json
import hashlib
import subprocess
import time
import threading
import tempfile
import shutil

import pytest

APP = "/app"
BIN = "/tmp/cluster_manager"
CONFIG_PATH = "/app/config.json"
DATA_DIR = "/app/data"

GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}

def _find_main_pkg():
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                try:
                    if "func main(" in open(os.path.join(root, f)).read():
                        rel = os.path.relpath(root, APP)
                        return "." if rel == "." else "./" + rel
                except:
                    pass
    return None

@pytest.fixture(scope="session", autouse=True)
def built():
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(["go", "mod", "init", "cluster-manager"], cwd=APP, env=GO_ENV, capture_output=True, text=True)
    def _build(pkg):
        return subprocess.run(["go", "build", "-o", BIN, pkg], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=240)
    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, f"build failed\n{r.stdout}\n{r.stderr}"
    assert os.path.exists(BIN)
    yield

def write_config(cfg_dict, path=CONFIG_PATH):
    with open(path, "w") as f:
        json.dump(cfg_dict, f, indent=2)

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
            if ".corrupt." in fname or fname.endswith(".lock"):
                try:
                    os.remove(os.path.join(DATA_DIR, fname))
                except:
                    pass
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
    if "data" not in obj or "checksum" not in obj:
        return False
    data = obj["data"]
    cs = obj["checksum"]
    if not cs:
        return False
    canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
    exp = hashlib.md5(canonical.encode()).hexdigest()
    return cs == exp

def test_help_sharded_contains_keywords():
    clean_all()
    r = run_config()
    assert r.returncode == 0
    out = r.stdout.lower()
    for kw in ["add-node", "remove-node", "list-nodes", "get-node", "add-job", "remove-job", "list-jobs", "get-job", "allocate", "deallocate", "schedule", "status",
               "get-shard-id", "get-shard-path", "distribution", "heartbeat", "get-presence", "get-node-health", "list-healthy", "list-online", "snapshot", "restore", "ops-log", "optimize",
               "data", "checksum", "shard", "weight", "global"]:
        assert kw in out, f"help missing {kw}"

def test_config_validation_exit2():
    clean_all()
    bad = default_config()
    bad["shard_count"] = 0
    write_config(bad)
    r = run_config("list-nodes")
    assert r.returncode == 2
    assert r.stdout.strip() == ""

    bad = default_config()
    bad["shards"] = [{"id": 0, "path": "/app/data/shard_0.json"}, {"id": 0, "path": "/app/data/shard_1.json"}]
    write_config(bad)
    r = run_config("list-nodes")
    assert r.returncode == 2
    assert r.stdout.strip() == ""

    bad = default_config()
    bad["shards"][0]["path"] = ""
    write_config(bad)
    r = run_config("list-nodes")
    assert r.returncode == 2

    bad = default_config()
    bad["shards"][0]["weight"] = 0
    write_config(bad)
    r = run_config("add-node", "n1", "4", "1024", "0")
    assert r.returncode == 2

    bad = default_config()
    bad["shards"][0]["id"] = -1
    write_config(bad)
    r = run_config("list-nodes")
    assert r.returncode == 2

    with open(CONFIG_PATH, "w") as f:
        f.write("{ invalid json")
    r = run_config("list-nodes")
    assert r.returncode == 2
    assert r.stdout.strip() == ""

    clean_all()

def test_unknown_fields_tolerance():
    clean_all()
    cfg = default_config()
    cfg["future_field"] = "ignore"
    cfg["unknown_top_level"] = 123
    cfg["shards"][0]["future_shard_field"] = "ignore"
    write_config(cfg)
    r = run_config("add-node", "node1", "4", "1024", "0")
    assert r.returncode == 0, f"unknown fields should be tolerated: {r.stderr}"
    r = run_config("list-nodes")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) == 1

def test_weighted_sharding_correct():
    clean_all()
    def hash_weighted(key, shards):
        import hashlib
        tot = sum(s.get("weight",1) for s in shards)
        if key.startswith("global:"):
            return -1
        h = int(hashlib.md5(key.encode()).hexdigest(), 16)
        idx = h % tot
        sorted_shards = sorted(shards, key=lambda s: s["id"])
        for s in sorted_shards:
            w = s.get("weight",1)
            if idx < w:
                return s["id"]
            idx -= w
        return sorted_shards[-1]["id"]

    cfg = default_config()
    shards = cfg["shards"]
    for key in [f"node-{i}" for i in range(20)]:
        expected = hash_weighted(key, shards)
        r = run_config("get-shard-id", key)
        assert r.returncode == 0
        got = int(r.stdout.strip())
        assert got == expected, f"shard id mismatch for {key}: got {got} expected {expected}"

    r = run_config("get-shard-id", "global:cfg1")
    assert r.returncode == 0
    assert int(r.stdout.strip()) == -1

    r = run_config("get-shard-path", "global:cfg1")
    assert r.returncode == 0
    paths = r.stdout.strip().split(",")
    assert len(paths) == 4
    assert paths == sorted(paths)

def test_distribution():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    r = run_config("distribution")
    assert r.returncode == 0
    dist = json.loads(r.stdout)
    assert isinstance(dist, dict)
    for sid in ["0","1","2","3"]:
        assert sid in dist
    total = sum(dist.values())
    assert total == 20

def test_turn1_still_works_in_sharded():
    clean_all()
    r = run_config("add-node", "nodeA", "4", "1024", "1")
    assert r.returncode == 0
    r = run_config("add-job", "jobA", "1", "256", "0")
    assert r.returncode == 0
    r = run_config("allocate", "jobA", "nodeA")
    assert r.returncode == 0
    r = run_config("get-node", "nodeA")
    assert r.returncode == 0
    node = json.loads(r.stdout)
    assert node["id"] == "nodeA"
    assert "jobA" in node["jobs"]
    r = run_config("get-job", "jobA")
    assert r.returncode == 0
    job = json.loads(r.stdout)
    assert job["node_id"] == "nodeA"
    r = run_config("status")
    assert r.returncode == 0
    st = json.loads(r.stdout)
    assert st["total_nodes"] == 1

def test_pagination_list_nodes():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i:02d}", "4", "1024", "0")
    r = run_config("list-nodes", "5", "0")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) == 5
    r2 = run_config("list-nodes", "5", "5")
    arr2 = json.loads(r2.stdout)
    assert len(arr2) == 5
    ids1 = [n["id"] for n in arr]
    ids2 = [n["id"] for n in arr2]
    assert ids1 != ids2
    r3 = run_config("list-nodes", "0", "0")
    assert len(json.loads(r3.stdout)) == 20
    r = run_config("list-nodes", "-1", "0")
    assert r.returncode == 2
    r = run_config("list-nodes", "abc", "0")
    assert r.returncode == 2
    r = run_config("list-nodes", "5", "-1")
    assert r.returncode == 2

def test_pagination_list_jobs():
    clean_all()
    run_config("add-node", "node1", "10", "10240", "0")
    for i in range(15):
        run_config("add-job", f"job-{i:02d}", "1", "256", "0")
    r = run_config("list-jobs", "5", "0")
    assert r.returncode == 0
    assert len(json.loads(r.stdout)) == 5
    r = run_config("list-jobs", "5", "5")
    assert len(json.loads(r.stdout)) == 5
    r = run_config("list-jobs", "0", "0")
    assert len(json.loads(r.stdout)) == 15

def test_best_fit_efficient():
    clean_all()
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "job1", "2", "512", "0")
    r = run_config("schedule", "job1")
    assert r.returncode == 0
    sched = json.loads(r.stdout)
    assert sched["node_id"] == "nodeB", f"best-fit should pick nodeB, got {sched['node_id']}"

    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("add-node", "nodeB", "8", "2048", "0")
    run_config("add-node", "nodeC", "2", "512", "0")
    run_config("add-job", "job1", "2", "512", "0")
    r = run_config("schedule", "job1")
    sched = json.loads(r.stdout)
    assert sched["node_id"] == "nodeC"

def test_heartbeat_and_presence():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    r = run_config("heartbeat", "nodeA")
    assert r.returncode == 0
    r = run_config("get-node-health", "nodeA")
    assert r.returncode == 0
    health = json.loads(r.stdout)
    assert health["online"] is True
    assert health["node_id"] == "nodeA"
    r = run_config("get-presence", "nodeA")
    assert r.returncode == 0
    assert json.loads(r.stdout)["online"] is True

    r = run_config("list-healthy")
    assert r.returncode == 0
    assert "nodeA" in json.loads(r.stdout)

    r = run_config("list-online")
    assert r.returncode == 0

def test_presence_ttl_expiry():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 2
    write_config(cfg)
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("heartbeat", "nodeA")
    r = run_config("get-node-health", "nodeA")
    assert json.loads(r.stdout)["online"] is True
    time.sleep(3)
    r = run_config("get-node-health", "nodeA")
    health = json.loads(r.stdout)
    assert health["online"] is False
    r = run_config("list-healthy")
    assert json.loads(r.stdout) == []

    r = run_config("get-node-health", "unknownNode")
    assert r.returncode == 0
    h = json.loads(r.stdout)
    assert h["online"] is False
    assert h["last_seen"] == 0

def test_rate_limiting_per_node():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "10", "10240", "0")
    for i in range(3):
        run_config("add-job", f"job{i}", "1", "256", "0")

    r = run_config("allocate", "job0", "nodeA")
    assert r.returncode == 0
    r = run_config("allocate", "job1", "nodeA")
    assert r.returncode == 0
    r = run_config("allocate", "job2", "nodeA")
    assert r.returncode == 1
    assert "rate limit" in r.stderr.lower()
    r = run_config("get-node", "nodeA")
    node = json.loads(r.stdout)
    assert len(node["jobs"]) == 2

    run_config("add-job", "jobB", "1", "256", "0")
    r = run_config("allocate", "jobB", "nodeB")
    assert r.returncode == 0

    time.sleep(1.6)
    run_config("add-job", "job3", "1", "256", "0")
    r = run_config("allocate", "job3", "nodeA")
    assert r.returncode == 0

def test_rate_limiting_multiple_cycles():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    for i in range(6):
        run_config("add-job", f"job{i}", "1", "256", "0")

    r = run_config("allocate", "job0", "nodeA")
    assert r.returncode == 0
    r = run_config("allocate", "job1", "nodeA")
    assert r.returncode == 0
    r = run_config("allocate", "job2", "nodeA")
    assert r.returncode == 1
    time.sleep(1.2)
    r = run_config("allocate", "job2", "nodeA")
    assert r.returncode == 0
    r = run_config("allocate", "job3", "nodeA")
    assert r.returncode == 1
    time.sleep(1.2)
    r = run_config("allocate", "job3", "nodeA")
    assert r.returncode == 0

def test_rate_limit_persistence_and_corruption():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-job", "job0", "1", "256", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job0", "nodeA")
    run_config("allocate", "job1", "nodeA")
    assert os.path.exists("/app/data/rate_limit.json")
    assert checksum_valid_generic("/app/data/rate_limit.json")
    with open("/app/data/rate_limit.json", "w") as f:
        f.write("{ invalid")
    run_config("add-job", "job2", "1", "256", "0")
    r = run_config("allocate", "job2", "nodeA")
    assert r.returncode == 0

def test_snapshot_restore_dir():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "1")
    run_config("add-node", "node2", "8", "2048", "2")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("snapshot", "/tmp/backup")
    assert os.path.isdir("/tmp/backup")
    run_config("add-node", "node3", "4", "1024", "0")
    run_config("add-job", "job2", "1", "256", "0")
    r = run_config("list-nodes")
    assert len(json.loads(r.stdout)) == 3
    r = run_config("restore", "/tmp/backup")
    assert r.returncode == 0
    r = run_config("list-nodes")
    nodes = json.loads(r.stdout)
    assert len(nodes) == 2
    ids = [n["id"] for n in nodes]
    assert "node3" not in ids
    r = run_config("list-jobs")
    jobs = json.loads(r.stdout)
    assert len(jobs) == 1
    assert jobs[0]["id"] == "job1"

def test_snapshot_restore_file():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    r = run_config("snapshot", "/tmp/backup.json")
    assert r.returncode == 0
    assert os.path.exists("/tmp/backup.json")
    run_config("add-node", "nodeX", "4", "1024", "0")
    r = run_config("list-nodes")
    assert len(json.loads(r.stdout)) == 2
    r = run_config("restore", "/tmp/backup.json")
    assert r.returncode == 0
    r = run_config("list-nodes")
    assert len(json.loads(r.stdout)) == 1
    assert json.loads(r.stdout)[0]["id"] == "node1"

def test_ops_log():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job1", "node1")
    r = run_config("ops-log")
    assert r.returncode == 0
    logs = json.loads(r.stdout)
    assert isinstance(logs, list)
    assert len(logs) >= 3
    ops = [e["op"] for e in logs]
    assert "add-node" in ops
    assert "add-job" in ops
    with open("/app/data/cluster_ops.log", "a") as f:
        f.write("invalid json line\n")
        f.write('{"op":"add-node","node_id":"bad","ts":123}\n')
    r = run_config("ops-log")
    assert r.returncode == 0
    assert "corrupt" in r.stderr.lower() or "skip" in r.stderr.lower() or "warning" in r.stderr.lower()
    logs = json.loads(r.stdout)
    assert isinstance(logs, list)

def test_optimize():
    clean_all()
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "10", "10240", "0")
    for i in range(4):
        run_config("add-job", f"job{i}", "2", "512", "0")
        run_config("allocate", f"job{i}", "nodeA" if i < 2 else "nodeB")
    r = run_config("optimize")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    for k in ["fragmentation_before", "fragmentation_after", "moves", "total_nodes", "used_nodes"]:
        assert k in out
    r = run_config("get-node", "nodeA")
    nodeA = json.loads(r.stdout)
    assert nodeA["used"]["cpu"] <= nodeA["total"]["cpu"]
    r = run_config("get-node", "nodeB")
    nodeB = json.loads(r.stdout)
    assert nodeB["used"]["cpu"] <= nodeB["total"]["cpu"]
    r = run_config("list-jobs")
    jobs = json.loads(r.stdout)
    assert all(j["node_id"] != "" for j in jobs)

def test_concurrent_allocate_sharded():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_config("add-job", f"job{i}", "1", "100", "0")

    def alloc_job(j):
        run_config("allocate", f"job{j}", "node1")

    threads = []
    for i in range(20):
        t = threading.Thread(target=alloc_job, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    r = run_config("get-node", "node1")
    assert r.returncode == 0
    node = json.loads(r.stdout)
    assert len(node["jobs"]) == 20
    for path in ["/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json", "/app/data/jobs.json"]:
        if os.path.exists(path):
            assert checksum_valid_generic(path)
    assert not os.path.exists("/app/data/global.lock")

def test_checksum_all_sharded_files():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("heartbeat", "node1")
    for p in ["/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json",
              "/app/data/jobs.json", "/app/data/presence.json", "/app/data/rate_limit.json"]:
        if os.path.exists(p):
            assert checksum_valid_generic(p), f"checksum invalid for {p}"

def test_corruption_handling_sharded():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    shard_path = "/app/data/shard_0.json"
    for s in default_config()["shards"]:
        if os.path.exists(s["path"]):
            with open(s["path"], "r") as f:
                try:
                    d = json.load(f)
                    if "node1" in json.dumps(d):
                        shard_path = s["path"]
                        break
                except:
                    pass
    with open(shard_path, "w") as f:
        f.write("{ invalid json")
    r = run_config("list-nodes")
    assert r.returncode == 0
    d = os.path.dirname(shard_path)
    backups = [fname for fname in os.listdir(d) if ".corrupt." in fname]
    assert len(backups) >= 1
    assert "corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower()

def test_pagination_perf():
    clean_all()
    for i in range(200):
        run_config("add-node", f"node-{i:04d}", "4", "1024", "0")
    start = time.time()
    r = run_config("list-nodes", "100", "0")
    elapsed_list = time.time() - start
    assert r.returncode == 0
    assert len(json.loads(r.stdout)) == 100
    assert elapsed_list < 2

    start = time.time()
    r = run_config("list-nodes", "0", "100")
    elapsed_list = time.time() - start
    assert r.returncode == 0
    assert elapsed_list < 2

def test_large_scale_200_nodes():
    clean_all()
    for i in range(200):
        run_config("add-node", f"node-{i:03d}", "4", "1024", "0")
    r = run_config("list-nodes")
    assert len(json.loads(r.stdout)) == 200
    r = run_config("distribution")
    dist = json.loads(r.stdout)
    assert sum(dist.values()) == 200

def test_stdlib_only():
    result = subprocess.run(["go", "list", "-f", "{{join .Imports \" \"}}", "."], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        for imp in result.stdout.split():
            assert "." not in imp, f"non-stdlib import found: {imp}"

def test_edge_validation_sharded():
    clean_all()
    r = run_config("add-node", "", "4", "1024", "0")
    assert r.returncode == 2
    r = run_config("add-node", "n1", "0", "1024", "0")
    assert r.returncode == 2
    r = run_config("add-job", "", "1", "256", "0")
    assert r.returncode == 2
    r = run_config("get-node", "nonexist-shard-node")
    assert r.returncode == 2
    r = run_config("list-nodes", "-1", "0")
    assert r.returncode == 2
    r = run_config("list-nodes", "5", "-1")
    assert r.returncode == 2
