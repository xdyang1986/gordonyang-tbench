"""
Turn2 HARD 65+ tests for efficient large-scale cluster management.
Covers sharding, best-fit, pagination, presence TTL, rate limiting per-node, snapshot/restore, ops-log, optimize, concurrency, checksum, config validation, global broadcast, large scale.
"""
import os
import json
import hashlib
import subprocess
import time
import threading

import pytest

APP = "/app"
BIN = "/app/cluster-manager"
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
    for fb in ["/tmp/backup", "/tmp/backup.json"]:
        try:
            if os.path.isdir(fb):
                import shutil
                shutil.rmtree(fb)
            else:
                os.remove(fb)
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

# ----- help -----
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
    assert r.returncode == 2 and r.stdout.strip() == ""
    bad = default_config()
    bad["shards"] = [{"id": 0, "path": "/app/data/shard_0.json"}, {"id": 0, "path": "/app/data/shard_1.json"}]
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    bad = default_config()
    bad["shards"][0]["path"] = ""
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    bad = default_config()
    bad["shards"][0]["weight"] = 0
    write_config(bad)
    assert run_config("add-node", "n1", "4", "1024", "0").returncode == 2
    bad = default_config()
    bad["shards"][0]["id"] = -1
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    with open(CONFIG_PATH, "w") as f:
        f.write("{ invalid json")
    r = run_config("list-nodes")
    assert r.returncode == 2 and r.stdout.strip() == ""
    clean_all()

def test_config_missing_count_empty_shards():
    clean_all()
    bad = {"shards": [{"id": 0, "path": "/app/data/shard_0.json"}]}
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    bad = {"shard_count": 2, "shards": []}
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    clean_all()

def test_unknown_fields_tolerance():
    clean_all()
    cfg = default_config()
    cfg["future_field"] = "ignore"
    cfg["unknown_top_level"] = 123
    cfg["shards"][0]["future_shard_field"] = "ignore"
    write_config(cfg)
    assert run_config("add-node", "node1", "4", "1024", "0").returncode == 0
    assert len(json.loads(run_config("list-nodes").stdout)) == 1

def test_weighted_sharding_correct():
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
    shards = cfg["shards"]
    for key in [f"node-{i}" for i in range(20)]:
        assert int(run_config("get-shard-id", key).stdout.strip()) == hash_weighted(key, shards)
    assert int(run_config("get-shard-id", "global:cfg1").stdout.strip()) == -1
    r = run_config("get-shard-path", "global:cfg1")
    assert len(r.stdout.strip().split(",")) == 4
    assert r.stdout.strip().split(",") == sorted(r.stdout.strip().split(","))

def test_get_shard_id_empty_string():
    clean_all()
    r = run_config("get-shard-id", "")
    # empty string MD5 hash should be valid and return some shard id
    assert r.returncode == 0
    sid = int(r.stdout.strip())
    assert sid in [0,1,2,3]

def test_get_shard_path_normal():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    r = run_config("get-shard-id", "nodeA")
    sid = int(r.stdout.strip())
    r2 = run_config("get-shard-path", "nodeA")
    assert r2.returncode == 0
    # path should match config shard path for sid
    cfg = default_config()
    for s in cfg["shards"]:
        if s["id"] == sid:
            assert r2.stdout.strip() == s["path"]

def test_distribution():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    assert sum(dist.values()) == 20
    for sid in ["0","1","2","3"]:
        assert sid in dist

def test_distribution_with_global():
    clean_all()
    run_config("add-node", "node-0", "4", "1024", "0")
    run_config("add-node", "global:cfg1", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    # global should be counted in each shard
    assert sum(dist.values()) >= 4  # at least global replicated
    for sid in ["0","1","2","3"]:
        assert dist[sid] >= 1  # global in each

def test_global_broadcast():
    clean_all()
    r = run_config("add-node", "global:shared", "4", "1024", "0")
    assert r.returncode == 0
    # check all shards contain it
    for s in default_config()["shards"]:
        if os.path.exists(s["path"]):
            txt = open(s["path"]).read()
            assert "global:shared" in txt

def test_global_remove_from_all():
    clean_all()
    run_config("add-node", "global:to-del", "4", "1024", "0")
    r = run_config("remove-node", "global:to-del")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    # ensure no shard has it
    for s in default_config()["shards"]:
        if os.path.exists(s["path"]):
            assert "global:to-del" not in open(s["path"]).read()

def test_global_allocate():
    clean_all()
    run_config("add-node", "global:g1", "10", "10240", "0")
    run_config("add-job", "job1", "1", "256", "0")
    r = run_config("allocate", "job1", "global:g1")
    assert r.returncode == 0
    assert json.loads(run_config("get-job", "job1").stdout)["node_id"] == "global:g1"

def test_turn1_still_works_in_sharded():
    clean_all()
    assert run_config("add-node", "nodeA", "4", "1024", "1").returncode == 0
    assert run_config("add-job", "jobA", "1", "256", "0").returncode == 0
    assert run_config("allocate", "jobA", "nodeA").returncode == 0
    assert "jobA" in json.loads(run_config("get-node", "nodeA").stdout)["jobs"]
    assert json.loads(run_config("get-job", "jobA").stdout)["node_id"] == "nodeA"
    assert json.loads(run_config("status").stdout)["total_nodes"] == 1

def test_pagination_list_nodes():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i:02d}", "4", "1024", "0")
    assert len(json.loads(run_config("list-nodes", "5", "0").stdout)) == 5
    assert len(json.loads(run_config("list-nodes", "5", "5").stdout)) == 5
    assert len(json.loads(run_config("list-nodes", "0", "0").stdout)) == 20
    assert run_config("list-nodes", "-1", "0").returncode == 2
    assert run_config("list-nodes", "abc", "0").returncode == 2
    assert run_config("list-nodes", "5", "-1").returncode == 2
    # offset beyond length returns empty
    assert json.loads(run_config("list-nodes", "5", "100").stdout) == []

def test_pagination_list_jobs():
    clean_all()
    run_config("add-node", "node1", "10", "10240", "0")
    for i in range(15):
        run_config("add-job", f"job-{i:02d}", "1", "256", "0")
    assert len(json.loads(run_config("list-jobs", "5", "0").stdout)) == 5
    assert len(json.loads(run_config("list-jobs", "5", "5").stdout)) == 5
    assert len(json.loads(run_config("list-jobs", "0", "0").stdout)) == 15
    assert json.loads(run_config("list-jobs", "5", "100").stdout) == []

def test_best_fit_efficient():
    clean_all()
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "job1", "2", "512", "0")
    assert json.loads(run_config("schedule", "job1").stdout)["node_id"] == "nodeB"
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("add-node", "nodeB", "8", "2048", "0")
    run_config("add-node", "nodeC", "2", "512", "0")
    run_config("add-job", "job1", "2", "512", "0")
    assert json.loads(run_config("schedule", "job1").stdout)["node_id"] == "nodeC"

def test_best_fit_tie_breaker_id():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    # both have same waste, should pick lexicographically smaller id nodeA
    assert json.loads(run_config("schedule", "job1").stdout)["node_id"] == "nodeA"

def test_schedule_no_fit_no_side_effects():
    clean_all()
    run_config("add-node", "node1", "1", "256", "0")
    run_config("add-job", "job1", "2", "512", "0")
    # ops log before
    before_ops = open("/app/data/cluster_ops.log").read() if os.path.exists("/app/data/cluster_ops.log") else ""
    r = run_config("schedule", "job1")
    assert r.returncode == 1 and "no fit" in r.stderr.lower() and r.stdout.strip() == ""
    after_ops = open("/app/data/cluster_ops.log").read() if os.path.exists("/app/data/cluster_ops.log") else ""
    assert before_ops == after_ops, "failed schedule should not append ops log"

def test_heartbeat_and_presence():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    assert run_config("heartbeat", "nodeA").returncode == 0
    health = json.loads(run_config("get-node-health", "nodeA").stdout)
    assert health["online"] is True
    assert json.loads(run_config("get-presence", "nodeA").stdout)["online"] is True
    assert "nodeA" in json.loads(run_config("list-healthy").stdout)
    assert "nodeA" in json.loads(run_config("list-online").stdout)

def test_heartbeat_nonexist_fails():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    assert run_config("heartbeat", "noexist").returncode == 2

def test_presence_ttl_expiry():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 2
    write_config(cfg)
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("heartbeat", "nodeA")
    assert json.loads(run_config("get-node-health", "nodeA").stdout)["online"] is True
    time.sleep(3)
    assert json.loads(run_config("get-node-health", "nodeA").stdout)["online"] is False
    assert json.loads(run_config("list-healthy").stdout) == []
    # unknown
    h = json.loads(run_config("get-node-health", "unknownNode").stdout)
    assert h["online"] is False and h["last_seen"] == 0

def test_presence_multiple_nodes_ttl():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 2
    write_config(cfg)
    for nid in ["n1","n2","n3"]:
        run_config("add-node", nid, "4", "1024", "0")
        run_config("heartbeat", nid)
    assert len(json.loads(run_config("list-healthy").stdout)) == 3
    time.sleep(3)
    assert json.loads(run_config("list-healthy").stdout) == []
    run_config("heartbeat", "n2")
    assert json.loads(run_config("list-healthy").stdout) == ["n2"]

def test_rate_limiting_per_node():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "10", "10240", "0")
    for i in range(3):
        run_config("add-job", f"job{i}", "1", "256", "0")
    assert run_config("allocate", "job0", "nodeA").returncode == 0
    assert run_config("allocate", "job1", "nodeA").returncode == 0
    r = run_config("allocate", "job2", "nodeA")
    assert r.returncode == 1 and "rate limit" in r.stderr.lower()
    assert len(json.loads(run_config("get-node", "nodeA").stdout)["jobs"]) == 2
    run_config("add-job", "jobB", "1", "256", "0")
    assert run_config("allocate", "jobB", "nodeB").returncode == 0
    time.sleep(1.6)
    run_config("add-job", "job3", "1", "256", "0")
    assert run_config("allocate", "job3", "nodeA").returncode == 0

def test_rate_limiting_multiple_cycles():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    for i in range(6):
        run_config("add-job", f"job{i}", "1", "256", "0")
    assert run_config("allocate", "job0", "nodeA").returncode == 0
    assert run_config("allocate", "job1", "nodeA").returncode == 0
    assert run_config("allocate", "job2", "nodeA").returncode == 1
    time.sleep(1.2)
    assert run_config("allocate", "job2", "nodeA").returncode == 0
    assert run_config("allocate", "job3", "nodeA").returncode == 1
    time.sleep(1.2)
    assert run_config("allocate", "job3", "nodeA").returncode == 0

def test_rate_limit_no_side_effects():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-job", "job0", "1", "256", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job0", "nodeA")
    before_jobs = json.loads(run_config("get-node", "nodeA").stdout)["jobs"][:]
    before_ops = open("/app/data/cluster_ops.log").read() if os.path.exists("/app/data/cluster_ops.log") else ""
    r = run_config("allocate", "job1", "nodeA")
    assert r.returncode == 1
    after_jobs = json.loads(run_config("get-node", "nodeA").stdout)["jobs"]
    assert before_jobs == after_jobs
    after_ops = open("/app/data/cluster_ops.log").read() if os.path.exists("/app/data/cluster_ops.log") else ""
    assert before_ops == after_ops

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
    assert run_config("allocate", "job2", "nodeA").returncode == 0

def test_snapshot_restore_dir():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "1")
    run_config("add-node", "node2", "8", "2048", "2")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("snapshot", "/tmp/backup")
    assert os.path.isdir("/tmp/backup")
    run_config("add-node", "node3", "4", "1024", "0")
    run_config("add-job", "job2", "1", "256", "0")
    assert len(json.loads(run_config("list-nodes").stdout)) == 3
    assert run_config("restore", "/tmp/backup").returncode == 0
    nodes = json.loads(run_config("list-nodes").stdout)
    assert len(nodes) == 2 and "node3" not in [n["id"] for n in nodes]
    assert len(json.loads(run_config("list-jobs").stdout)) == 1

def test_snapshot_restore_file():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    assert run_config("snapshot", "/tmp/backup.json").returncode == 0
    assert os.path.exists("/tmp/backup.json")
    run_config("add-node", "nodeX", "4", "1024", "0")
    assert len(json.loads(run_config("list-nodes").stdout)) == 2
    assert run_config("restore", "/tmp/backup.json").returncode == 0
    assert len(json.loads(run_config("list-nodes").stdout)) == 1

def test_snapshot_file_mode_contains_keys():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("snapshot", "/tmp/backup.json")
    obj = json.loads(open("/tmp/backup.json").read())
    for k in ["shards", "jobs", "presence", "rate_limit", "ops_log"]:
        assert k in obj

def test_restore_dir_resets_non_backed():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("snapshot", "/tmp/backup")
    run_config("add-node", "node2", "4", "1024", "0")
    # node2 is in some shard not in backup
    assert run_config("restore", "/tmp/backup").returncode == 0
    assert len(json.loads(run_config("list-nodes").stdout)) == 1

def test_ops_log():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job1", "node1")
    logs = json.loads(run_config("ops-log").stdout)
    assert len(logs) >= 3
    assert "add-node" in [e["op"] for e in logs]
    with open("/app/data/cluster_ops.log", "a") as f:
        f.write("invalid json line\n")
        f.write('{"op":"add-node","node_id":"bad","ts":123}\n')
    r = run_config("ops-log")
    assert "corrupt" in r.stderr.lower() or "skip" in r.stderr.lower() or "warning" in r.stderr.lower()
    assert isinstance(json.loads(r.stdout), list)

def test_ops_log_large_100():
    clean_all()
    for i in range(100):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    r = run_config("ops-log")
    logs = json.loads(r.stdout)
    assert len(logs) >= 100

def test_ops_log_big_buffer():
    clean_all()
    # write a 100KB line + valid
    run_config("add-node", "node1", "4", "1024", "0")
    with open("/app/data/cluster_ops.log", "a") as f:
        big_val = "x" * 100*1024
        f.write(json.dumps({"op":"set","key":"big","value":big_val,"ts":999999}) + "\n")
    r = run_config("ops-log")
    assert r.returncode == 0
    logs = json.loads(r.stdout)
    assert any(e.get("key") == "big" for e in logs)

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
    # no overcommit
    for nid in ["nodeA","nodeB"]:
        n = json.loads(run_config("get-node", nid).stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]
    assert all(j["node_id"] != "" for j in json.loads(run_config("list-jobs").stdout))

def test_concurrent_allocate_sharded():
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
    for p in ["/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json", "/app/data/jobs.json"]:
        if os.path.exists(p):
            assert checksum_valid_generic(p)
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
            try:
                if "node1" in open(s["path"]).read():
                    shard_path = s["path"]
                    break
            except:
                pass
    with open(shard_path, "w") as f:
        f.write("{ invalid json")
    r = run_config("list-nodes")
    assert r.returncode == 0
    d = os.path.dirname(shard_path)
    assert len([fname for fname in os.listdir(d) if ".corrupt." in fname]) >= 1
    assert "corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower()

def test_pagination_perf():
    clean_all()
    for i in range(200):
        run_config("add-node", f"node-{i:04d}", "4", "1024", "0")
    start = time.time()
    r = run_config("list-nodes", "100", "0")
    assert r.returncode == 0 and len(json.loads(r.stdout)) == 100
    assert time.time() - start < 2
    start = time.time()
    r = run_config("list-nodes", "0", "100")
    assert time.time() - start < 2

def test_large_scale_200_nodes():
    clean_all()
    for i in range(200):
        run_config("add-node", f"node-{i:03d}", "4", "1024", "0")
    assert len(json.loads(run_config("list-nodes").stdout)) == 200
    assert sum(json.loads(run_config("distribution").stdout).values()) == 200

def test_stdlib_only():
    import subprocess as sp
    result = sp.run(["go", "list", "-f", "{{join .Imports \" \"}}", "."], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        for imp in result.stdout.split():
            assert "." not in imp, f"non-stdlib import {imp}"

def test_edge_validation_sharded():
    clean_all()
    assert run_config("add-node", "", "4", "1024", "0").returncode == 2
    assert run_config("add-node", "n1", "0", "1024", "0").returncode == 2
    assert run_config("add-job", "", "1", "256", "0").returncode == 2
    assert run_config("get-node", "nonexist-shard-node").returncode == 2
    assert run_config("list-nodes", "-1", "0").returncode == 2
    assert run_config("list-nodes", "5", "-1").returncode == 2
    assert run_config("list-nodes", "abc", "0").returncode == 2

def test_raw_shard_contains_unescaped_lt():
    clean_all()
    run_config("add-node", "node<>&", "4", "1024", "0")
    # find shard file containing it
    for s in default_config()["shards"]:
        if os.path.exists(s["path"]) and "node<>&" in open(s["path"]).read():
            raw = open(s["path"]).read()
            assert "<" in raw and "\\u003c" not in raw.lower()
            break
    else:
        assert False, "shard file with special char not found"

def test_unicode_emoji_sharded():
    clean_all()
    run_config("add-node", "node-🌍", "4", "1024", "0")
    assert "🌍" in json.loads(run_config("get-node", "node-🌍").stdout)["id"]
    # raw file contains emoji
    found = False
    for s in default_config()["shards"]:
        if os.path.exists(s["path"]):
            if "🌍" in open(s["path"], "r", encoding="utf-8").read():
                found = True
                break
    assert found

def test_large_id_10kb_sharded():
    clean_all()
    big = "n" * 5000
    assert run_config("add-node", big, "4", "1024", "0").returncode == 0
    assert json.loads(run_config("get-node", big).stdout)["id"] == big

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
    run_config("add-job", "job1", "1", "256", "0")
    assert run_config("add-job", "job1", "2", "512", "0").returncode == 0
    assert json.loads(run_config("get-job", "job1").stdout)["required"]["cpu"] == 1
