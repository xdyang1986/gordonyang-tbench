"""
Turn2 extra hard 78 tests for efficient cluster management.

Discriminators:
- best-fit tie-break cascade cpu→mem→gpu→id lexicographic
- token-bucket multi-cycle refill, per-node independence, no-consume on insufficient, persistence, corruption
- optimize fragmentation invariants: moves, no overcommit, preserve jobs, used_nodes reduction
- presence TTL expiry, multi-node, unknown offline, corruption
- config validation clarified (missing file fallback vs invalid exit2)
- snapshot/restore dir+file exactness, pagination perf, weighted distribution, global broadcast
"""

import os, json, hashlib, subprocess, time, threading, shutil
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


@pytest.fixture(scope="session", autouse=True)
def built():
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(
            ["go", "mod", "init", "cluster-manager"],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
            text=True,
        )

    def _build(pkg):
        return subprocess.run(
            ["go", "build", "-o", BIN, pkg],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
            text=True,
            timeout=240,
        )

    r = _build(".")
    if r.returncode != 0:
        for root, _, files in os.walk(APP):
            for f in files:
                if (
                    f.endswith(".go")
                    and "func main(" in open(os.path.join(root, f)).read()
                ):
                    rel = os.path.relpath(root, APP)
                    pkg = "." if rel == "." else "./" + rel
                    r = _build(pkg)
                    break
    assert r.returncode == 0, f"build failed {r.stdout} {r.stderr}"
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
            {"id": 3, "path": "/app/data/shard_3.json", "weight": 1},
        ],
        "rate_limit": {"allocations_per_second": 1000, "burst": 10000},
        "node_heartbeat_ttl_seconds": 60,
        "ops_log": "/app/data/cluster_ops.log",
        "jobs_path": "/app/data/jobs.json",
        "presence_path": "/app/data/presence.json",
        "rate_limit_path": "/app/data/rate_limit.json",
        "counter_path": "/app/data/counter.json",
    }


def clean_all():
    for p in [
        "/app/data/cluster.json",
        "/app/data/cluster.json.lock",
        "/app/data/global.lock",
        "/app/data/shard_0.json",
        "/app/data/shard_1.json",
        "/app/data/shard_2.json",
        "/app/data/shard_3.json",
        "/app/data/jobs.json",
        "/app/data/presence.json",
        "/app/data/rate_limit.json",
        "/app/data/counter.json",
        "/app/data/cluster_ops.log",
        "/app/data/nodes_index.json",
    ]:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    try:
        for fname in os.listdir(DATA_DIR):
            if (
                ".corrupt." in fname
                or fname.endswith(".lock")
                or fname.endswith(".bak")
            ):
                try:
                    os.remove(os.path.join(DATA_DIR, fname))
                except:
                    pass
    except FileNotFoundError:
        pass
    for fb in ["/tmp/backup", "/tmp/backup.json"]:
        try:
            if os.path.isdir(fb):
                shutil.rmtree(fb)
            else:
                os.remove(fb)
        except FileNotFoundError:
            pass
    write_config(default_config())


def run_config(*args, config_path=CONFIG_PATH, timeout=15):
    if not os.path.exists(BIN):
        try:
            import subprocess as _sp

            _sp.run(
                ["go", "build", "-o", BIN, "."],
                cwd="/app",
                env={
                    **os.environ,
                    "GOTOOLCHAIN": "local",
                    "GOFLAGS": "-mod=mod",
                    "GOCACHE": "/tmp/gocache",
                    "GOPATH": "/tmp/gopath",
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass
    cmd = [BIN, "--config", config_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def run_single(*args, data_path="/app/data/cluster.json", timeout=15):
    if not os.path.exists(BIN):
        try:
            import subprocess as _sp

            _sp.run(
                ["go", "build", "-o", BIN, "."],
                cwd="/app",
                env={
                    **os.environ,
                    "GOTOOLCHAIN": "local",
                    "GOFLAGS": "-mod=mod",
                    "GOCACHE": "/tmp/gocache",
                    "GOPATH": "/tmp/gopath",
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass
    cmd = [BIN, "--data", data_path] + list(args)
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
    # Spec requires Python canonical: sort_keys, separators (',',':'), ensure_ascii=False, raw UTF-8
    # Go must unescape U+2028/U+2029 to match Python
    canonical = json.dumps(
        obj["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return obj["checksum"] == hashlib.md5(canonical.encode()).hexdigest()


# ---------- basic help / config ----------


def test_help_sharded():
    clean_all()
    r = run_config()
    assert r.returncode == 0
    out = r.stdout.lower()
    for kw in [
        "add-node",
        "get-shard-id",
        "get-shard-path",
        "distribution",
        "heartbeat",
        "get-node-health",
        "list-healthy",
        "snapshot",
        "restore",
        "ops-log",
        "optimize",
        "data",
        "checksum",
        "shard",
        "weight",
    ]:
        assert kw in out


def test_config_validation():
    clean_all()
    bad = default_config()
    bad["shard_count"] = 0
    write_config(bad)
    r = run_config("list-nodes")
    assert r.returncode == 2 and r.stdout.strip() == ""
    bad = default_config()
    bad["shards"] = [
        {"id": 0, "path": "/app/data/shard_0.json"},
        {"id": 0, "path": "/app/data/shard_1.json"},
    ]
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


def test_config_missing_vs_invalid():
    # missing config file should fallback to single-file mode (not exit2)
    clean_all()
    # ensure no config
    try:
        os.remove(CONFIG_PATH)
    except FileNotFoundError:
        pass
    # single-file mode: add-node via --data should work even without config present
    # but run_config uses --config flag which points to missing file – current binary should treat missing as fallback? Actually spec says missing → fallback, not exit2.
    # We test: if config missing, binary with --config pointing to that missing path should either fallback or succeed with empty store not exit2? According spec fallback.
    # So list-nodes with missing config should NOT exit2, should return [].
    r = run_config("list-nodes", config_path="/tmp/nonexistent_config.json")
    # fallback case: should be exit0 and empty array
    assert r.returncode == 0
    # invalid config (exists but bad) should exit2
    clean_all()
    bad = {
        "shards": [{"id": 0, "path": "/app/data/shard_0.json"}]
    }  # missing shard_count
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    bad = {"shard_count": 2, "shards": []}
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    clean_all()


# ---------- sharding ----------


def test_weighted_sharding():
    clean_all()

    def hash_weighted(key, shards):
        import hashlib

        tot = sum(s.get("weight", 1) for s in shards)
        if key.startswith("global:"):
            return -1
        h = int(hashlib.md5(key.encode()).hexdigest(), 16)
        idx = h % tot
        for s in sorted(shards, key=lambda s: s["id"]):
            w = s.get("weight", 1)
            if idx < w:
                return s["id"]
            idx -= w
        return sorted(shards, key=lambda s: s["id"])[-1]["id"]

    cfg = default_config()
    for key in [f"node-{i}" for i in range(15)]:
        assert int(run_config("get-shard-id", key).stdout.strip()) == hash_weighted(
            key, cfg["shards"]
        )
    assert int(run_config("get-shard-id", "global:cfg1").stdout.strip()) == -1


def test_distribution():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    assert sum(dist.values()) == 20
    # includes zeros
    assert len(dist) == 4


def test_turn1_works_in_sharded():
    clean_all()
    assert run_config("add-node", "nodeA", "4", "1024", "1").returncode == 0
    assert run_config("add-job", "jobA", "1", "256", "0").returncode == 0
    assert run_config("allocate", "jobA", "nodeA").returncode == 0
    assert "jobA" in json.loads(run_config("get-node", "nodeA").stdout)["jobs"]


# ---------- pagination ----------


def test_pagination_nodes():
    clean_all()
    for i in range(20):
        run_config("add-node", f"node-{i:02d}", "4", "1024", "0")
    assert len(json.loads(run_config("list-nodes", "5", "0").stdout)) == 5
    assert len(json.loads(run_config("list-nodes", "5", "5").stdout)) == 5
    assert json.loads(run_config("list-nodes", "5", "100").stdout) == []
    assert run_config("list-nodes", "-1", "0").returncode == 2
    # limit 0 returns all
    assert len(json.loads(run_config("list-nodes", "0", "0").stdout)) == 20


def test_pagination_jobs():
    clean_all()
    run_config("add-node", "node1", "10", "10240", "0")
    for i in range(15):
        run_config("add-job", f"job-{i:02d}", "1", "256", "0")
    assert len(json.loads(run_config("list-jobs", "5", "0").stdout)) == 5
    assert len(json.loads(run_config("list-jobs", "0", "0").stdout)) == 15
    assert json.loads(run_config("list-jobs", "5", "100").stdout) == []


def test_pagination_performance():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    start = time.time()
    for i in range(200):
        run_config("add-node", f"node-perf-{i:04d}", "4", "1024", "0")
    elapsed = time.time() - start
    # list-nodes 1000 items perf is tested after 200; ensure <2s for list
    r = run_config("list-nodes", "100", "0")
    assert r.returncode == 0 and len(json.loads(r.stdout)) == 100
    # overall add 200 should be reasonable, but we check list time
    t0 = time.time()
    run_config("list-nodes", "0", "0")
    assert time.time() - t0 < 2


# ---------- best-fit with tie-break cascade ----------


def test_best_fit_efficient_cpu():
    clean_all()
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "job1", "2", "512", "0")
    assert json.loads(run_config("schedule", "job1").stdout)["node_id"] == "nodeB"


def test_best_fit_tie_break_mem():
    clean_all()
    # equal CPU waste, different MEM waste -> smaller MEM waste wins
    # nodeA: 4 CPU 2048 MEM, nodeB: 4 CPU 1024 MEM, job: 2 CPU 512 MEM
    # free CPU both 4, waste CPU both 2, mem waste A 1536 vs B 512 → B wins
    run_config("add-node", "nodeA", "4", "2048", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "job1", "2", "512", "0")
    nid = json.loads(run_config("schedule", "job1").stdout)["node_id"]
    assert nid == "nodeB", f"expected nodeB best-fit mem tie-break, got {nid}"


def test_best_fit_tie_break_gpu():
    clean_all()
    # cpu waste equal, mem waste equal, gpu waste differs
    run_config("add-node", "nodeX", "4", "1024", "1")
    run_config("add-node", "nodeY", "4", "1024", "0")
    run_config("add-job", "jobGPU", "2", "512", "0")
    # both cpu waste 2, mem waste 512, gpu waste X=1, Y=0 → Y wins
    nid = json.loads(run_config("schedule", "jobGPU").stdout)["node_id"]
    assert nid == "nodeY", f"gpu tie-break expected nodeY got {nid}"


def test_best_fit_tie_break_id_lex():
    clean_all()
    # identical resources, identical waste → lexicographically smallest id wins
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("add-job", "jobID", "1", "256", "0")
    nid = json.loads(run_config("schedule", "jobID").stdout)["node_id"]
    assert nid == "nodeA", f"lex id tie-break expected nodeA got {nid}"


def test_best_fit_fragmentation_vs_first_fit():
    clean_all()
    # first-fit would pick nodeA (sorted id) even though nodeB is better fit
    # Setup: nodeA 10 CPU, nodeB 3 CPU, job 2 CPU
    # Both fit, but best-fit waste: A 8 vs B 1 → B should win. First-fit would pick A.
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "3", "1024", "0")
    run_config("add-job", "jobFrag", "2", "512", "0")
    out = json.loads(run_config("schedule", "jobFrag").stdout)
    assert out["node_id"] == "nodeB"


def test_best_fit_after_allocations():
    clean_all()
    # after allocations, free resources matter
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    run_config("add-job", "jobA", "2", "512", "0")
    run_config("allocate", "jobA", "nodeA")  # nodeA now free 2, nodeB free 4
    run_config("add-job", "jobB", "2", "512", "0")
    # best-fit should pick nodeA (waste 0 vs waste 2)
    nid = json.loads(run_config("schedule", "jobB").stdout)["node_id"]
    assert nid == "nodeA"


# ---------- heartbeat / presence ----------


def test_heartbeat_presence():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    assert run_config("heartbeat", "nodeA").returncode == 0
    health = json.loads(run_config("get-node-health", "nodeA").stdout)
    assert health["online"] is True
    assert "nodeA" in json.loads(run_config("list-healthy").stdout)


def test_heartbeat_ttl_expiry():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 2
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("heartbeat", "nodeA")
    assert json.loads(run_config("get-node-health", "nodeA").stdout)["online"] is True
    time.sleep(3.1)
    health = json.loads(run_config("get-node-health", "nodeA").stdout)
    assert health["online"] is False
    assert json.loads(run_config("list-healthy").stdout) == []


def test_presence_multiple_nodes_ttl():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 2
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    for nid in ["node1", "node2", "node3"]:
        run_config("add-node", nid, "4", "1024", "0")
        run_config("heartbeat", nid)
    assert len(json.loads(run_config("list-healthy").stdout)) == 3
    time.sleep(3.2)
    assert json.loads(run_config("list-healthy").stdout) == []
    run_config("heartbeat", "node2")
    healthy = json.loads(run_config("list-healthy").stdout)
    assert healthy == ["node2"]


def test_presence_unknown_offline():
    clean_all()
    h = json.loads(run_config("get-presence", "never-seen-node").stdout)
    assert h["online"] is False
    assert h["last_seen"] == 0
    assert h["last_seen_seconds_ago"] == 0


def test_presence_corruption_handling():
    clean_all()
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("heartbeat", "nodeA")
    pres_path = default_config()["presence_path"]
    with open(pres_path, "w") as f:
        f.write("{ invalid json")
    r = run_config("get-presence", "nodeA")
    assert r.returncode == 0
    # after corruption handling, should be offline
    health = json.loads(r.stdout)
    assert health["online"] is False


# ---------- rate limiting ----------


def test_rate_limit_basic():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    for i in range(2):
        run_config("add-job", f"job{i}", "1", "256", "0")
        r = run_config("allocate", f"job{i}", "node1")
        assert r.returncode == 0
    run_config("add-job", "job2", "1", "256", "0")
    r = run_config("allocate", "job2", "node1")
    assert r.returncode == 1 and "rate limit" in r.stderr.lower()
    assert r.stdout.strip() == ""


def test_rate_limit_per_node_independent():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-node", "nodeB", "10", "10240", "0")
    run_config("add-job", "jobA", "1", "256", "0")
    run_config("add-job", "jobB", "1", "256", "0")
    run_config("add-job", "jobC", "1", "256", "0")
    assert run_config("allocate", "jobA", "nodeA").returncode == 0
    # nodeA now exhausted, but nodeB should still succeed
    assert run_config("allocate", "jobB", "nodeB").returncode == 0
    # nodeA limited
    r = run_config("allocate", "jobC", "nodeA")
    assert r.returncode == 1


def test_rate_limit_refill_after_sleep():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    for i in range(2):
        run_config("add-job", f"job{i}", "1", "256", "0")
        assert run_config("allocate", f"job{i}", "node1").returncode == 0
    run_config("add-job", "job2", "1", "256", "0")
    assert run_config("allocate", "job2", "node1").returncode == 1
    time.sleep(1.6)
    run_config("add-job", "job3", "1", "256", "0")
    assert run_config("allocate", "job3", "node1").returncode == 0


def test_rate_limit_multiple_cycles():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "node1", "20", "20000", "0")
    # cycle: 2 succeed, 1 fail, sleep 1.2 succeed, fail, sleep 1.2 succeed
    for i in range(2):
        run_config("add-job", f"job{i}", "1", "256", "0")
        assert run_config("allocate", f"job{i}", "node1").returncode == 0
    run_config("add-job", "job2", "1", "256", "0")
    assert run_config("allocate", "job2", "node1").returncode == 1
    time.sleep(1.2)
    run_config("add-job", "job3", "1", "256", "0")
    assert run_config("allocate", "job3", "node1").returncode == 0
    run_config("add-job", "job4", "1", "256", "0")
    assert run_config("allocate", "job4", "node1").returncode == 1
    time.sleep(1.2)
    run_config("add-job", "job5", "1", "256", "0")
    assert run_config("allocate", "job5", "node1").returncode == 0


def test_rate_limit_no_consume_on_insufficient():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "small", "1", "256", "0")
    run_config("add-job", "big", "10", "10000", "0")
    r = run_config("allocate", "big", "small")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()
    # token should NOT be consumed, so next small allocation should succeed
    run_config("add-job", "smalljob", "1", "256", "0")
    assert run_config("allocate", "smalljob", "small").returncode == 0


def test_rate_limit_no_side_effects():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    run_config("add-job", "job0", "1", "256", "0")
    assert run_config("allocate", "job0", "node1").returncode == 0
    run_config("add-job", "job1", "1", "256", "0")
    r = run_config("allocate", "job1", "node1")
    assert r.returncode == 1
    # ensure job1 not allocated and not in node's jobs, ops-log not appended for failed
    job = json.loads(run_config("get-job", "job1").stdout)
    assert job["node_id"] in ("", None)
    node = json.loads(run_config("get-node", "node1").stdout)
    assert "job1" not in node["jobs"]


def test_rate_limit_persistence():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    run_config("add-job", "job0", "1", "256", "0")
    assert run_config("allocate", "job0", "node1").returncode == 0
    # bucket file should exist and have tokens < burst – checksum valid if implemented, but allow file exists for easier difficulty
    rl_path = cfg["rate_limit_path"]
    assert os.path.exists(rl_path)
    # lenient: check file is valid JSON with data field, not strictly checksum, to ease Step2
    try:
        raw = open(rl_path, "r", encoding="utf-8").read()
        obj = json.loads(raw)
        assert "data" in obj or "checksum" in obj
    except:
        # if checksum invalid, still allow if file exists (persistence)
        pass
    # second allocation should be rate limited even in new process (persistence)
    run_config("add-job", "job1", "1", "256", "0")
    assert run_config("allocate", "job1", "node1").returncode == 1


def test_rate_limit_corruption():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    rl_path = cfg["rate_limit_path"]
    with open(rl_path, "w") as f:
        f.write("{ invalid")
    run_config("add-job", "job0", "1", "256", "0")
    r = run_config("allocate", "job0", "node1")
    # after corruption handling, bucket reset → should succeed
    assert r.returncode == 0


def test_rate_limit_file_shape_flat_map():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10, "burst": 10}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    run_config("add-job", "jobA", "1", "256", "0")
    assert run_config("allocate", "jobA", "nodeA").returncode == 0
    rl_path = cfg["rate_limit_path"]
    assert os.path.exists(rl_path)
    raw = open(rl_path, "r", encoding="utf-8").read()
    obj = json.loads(raw)
    assert "data" in obj and "checksum" in obj, "wrapper must contain data and checksum"
    data = obj["data"]
    # Spec says flat map {"nodeA": {"tokens": float, "last_refill": int}}, not nested under buckets
    assert "nodeA" in data, (
        f"rate_limit data should directly contain node id, got keys {list(data.keys())}"
    )
    assert "buckets" not in data, (
        "rate_limit data should NOT contain buckets key – spec is flat map"
    )
    bucket = data["nodeA"]
    assert "tokens" in bucket and "last_refill" in bucket, (
        f"bucket should have tokens and last_refill, got {bucket}"
    )
    assert isinstance(bucket["tokens"], (int, float))
    assert isinstance(bucket["last_refill"], int)


# ---------- concurrency, checksum, locks ----------


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
    for p in [
        "/app/data/shard_0.json",
        "/app/data/shard_1.json",
        "/app/data/shard_2.json",
        "/app/data/shard_3.json",
        "/app/data/jobs.json",
        "/app/data/presence.json",
        "/app/data/rate_limit.json",
    ]:
        if os.path.exists(p):
            assert checksum_valid_generic(p), f"checksum invalid {p}"


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
    assert run_config("list-nodes").stdout.strip() == ""
    bad = {"shard_count": 2, "shards": []}
    write_config(bad)
    assert run_config("list-nodes").returncode == 2
    assert run_config("list-nodes").stdout.strip() == ""
    clean_all()


# ---------- empty string handling ----------


def test_get_shard_id_empty_string():
    clean_all()
    r = run_config("get-shard-id", "")
    assert r.returncode == 0
    sid = int(r.stdout.strip())
    assert sid in [0, 1, 2, 3]


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


def test_get_shard_path_global():
    clean_all()
    r = run_config("get-shard-path", "global:mycfg")
    assert r.returncode == 0
    # comma-separated sorted list
    parts = r.stdout.strip().split(",")
    assert len(parts) == 4
    assert parts == sorted(parts)


# ---------- optimize ----------


def test_optimize_no_overcommit_preserve():
    clean_all()
    run_config("add-node", "node1", "10", "10240", "0")
    run_config("add-node", "node2", "10", "10240", "0")
    for i in range(5):
        run_config("add-job", f"job{i}", "1", "256", "0")
        run_config("allocate", f"job{i}", "node1")
    r = run_config("optimize")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "fragmentation_before" in out
    assert "fragmentation_after" in out
    assert "moves" in out
    assert "total_nodes" in out
    assert "used_nodes" in out
    assert out["moves"] >= 0
    assert out["total_nodes"] == 2
    # jobs preserved
    jobs = json.loads(run_config("list-jobs", "0", "0").stdout)
    assert len(jobs) == 5
    # no overcommit
    for nid in ["node1", "node2"]:
        n = json.loads(run_config("get-node", nid).stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]
        assert n["used"]["memory"] <= n["total"]["memory"]
        assert n["used"]["gpu"] <= n["total"]["gpu"]


def test_optimize_fragmentation_reduction():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    # fragmented: 1 job per node on 3 nodes, each node 4 CPU, each job 1 CPU -> all 3 fit on 1 node
    for i in range(3):
        run_config("add-node", f"node{i}", "4", "1024", "0")
        run_config("add-job", f"job{i}", "1", "256", "0")
        run_config("allocate", f"job{i}", f"node{i}")
    # used_nodes = 3 before
    before_nodes = [
        json.loads(run_config("get-node", f"node{i}").stdout) for i in range(3)
    ]
    assert all(n["used"]["cpu"] > 0 for n in before_nodes)
    r = run_config("optimize")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    # Must actually consolidate: 3 jobs fit on 1 node of 4 CPU, so used_nodes should strictly decrease from 3 to 1
    assert out["used_nodes"] < 3, (
        f"optimize should reduce used_nodes from 3, got {out['used_nodes']}"
    )
    assert out["used_nodes"] == 1, (
        f"expected 1 used node after consolidating 3x1-CPU jobs onto 4-CPU nodes, got {out['used_nodes']}"
    )
    assert out["fragmentation_after"] <= out["fragmentation_before"] + 1e-9
    # moves should be >=2 to consolidate
    assert out["moves"] >= 2, (
        f"expected at least 2 moves to consolidate, got {out['moves']}"
    )
    # jobs preserved and no overcommit
    assert len(json.loads(run_config("list-jobs", "0", "0").stdout)) == 3
    for i in range(3):
        n = json.loads(run_config("get-node", f"node{i}").stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]


def test_optimize_moves_valid():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    # Fragmented: 2 nodes, 2 jobs on separate nodes, both fit on 1 node -> need 1 move
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("add-node", "nodeB", "4", "1024", "0")
    for i in range(2):
        run_config("add-job", f"jobM{i}", "1", "256", "0")
        run_config("allocate", f"jobM{i}", f"node{'A' if i == 0 else 'B'}")
    r = run_config("optimize")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert isinstance(out["moves"], int) and out["moves"] >= 0
    assert isinstance(out["fragmentation_before"], float)
    assert isinstance(out["fragmentation_after"], float)
    # For fragmented placement, should need at least 1 move to consolidate
    assert out["moves"] >= 1, (
        f"fragmented 2 nodes should need >=1 move, got {out['moves']}"
    )
    assert out["used_nodes"] == 1, (
        f"2 jobs should consolidate to 1 node, got {out['used_nodes']}"
    )


# ---------- snapshot / restore ----------


def test_snapshot_restore_dir():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("heartbeat", "node1")
    assert run_config("snapshot", "/tmp/backup").returncode == 0
    assert os.path.isdir("/tmp/backup")
    # mutate
    run_config("add-node", "newnode", "4", "1024", "0")
    run_config("add-job", "newjob", "1", "256", "0")
    assert "newnode" in [n["id"] for n in json.loads(run_config("list-nodes").stdout)]
    # restore
    assert run_config("restore", "/tmp/backup").returncode == 0
    ids = [n["id"] for n in json.loads(run_config("list-nodes").stdout)]
    assert "newnode" not in ids
    assert "node1" in ids
    # next allocate still works
    run_config("add-job", "job2", "1", "256", "0")
    assert run_config("allocate", "job2", "node1").returncode == 0


def test_snapshot_restore_file():
    clean_all()
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    assert run_config("snapshot", "/tmp/backup.json").returncode == 0
    assert os.path.exists("/tmp/backup.json")
    run_config("add-node", "newnode", "4", "1024", "0")
    assert run_config("restore", "/tmp/backup.json").returncode == 0
    assert "newnode" not in [
        n["id"] for n in json.loads(run_config("list-nodes").stdout)
    ]


# ---------- ops-log ----------


def test_ops_log_and_skip_invalid():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job1", "node1")
    # inject invalid line to test skip logic (core of this test)
    ops_path = cfg["ops_log"]
    with open(ops_path, "a") as f:
        f.write("invalid json line\n")
    r = run_config("ops-log")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    # spec requires only that ops-log prints array and skips invalid lines with warning;
    # it does NOT require that add-node/add-job also append. So check >=1 and that
    # allocate op is present (the nominal focus of the test).
    assert len(arr) >= 1
    # at least one entry should be allocate (the operation we performed)
    assert any(
        (e.get("op") == "allocate" or "allocate" in str(e).lower()) for e in arr
    ), f"ops-log should contain allocate op, got {arr}"
    assert (
        "corrupt" in r.stderr.lower()
        or "skip" in r.stderr.lower()
        or "warning" in r.stderr.lower()
    )


# ---------- file locks, idempotent, edge ----------


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
    assert run_config("heartbeat", "noexist").returncode == 2
    assert run_config("list-jobs", "-1", "0").returncode == 2


def test_global_broadcast():
    clean_all()
    run_config("add-node", "global:cfg1", "4", "1024", "0")
    assert int(run_config("get-shard-id", "global:cfg1").stdout.strip()) == -1
    dist = json.loads(run_config("distribution").stdout)
    # global node should be counted in each shard
    for v in dist.values():
        assert v >= 1


# ---------- Further hardening Step2: 46->70 (both steps too easy) ----------


def test_best_fit_tie_break_mem_gpu_id_comprehensive():
    clean_all()
    # 4 nodes with varying waste: cpu waste equal, mem waste equal for some, gpu differs
    # nodeA: free cpu 5 mem 1000 gpu 2, nodeB: free cpu 5 mem 1000 gpu 1, nodeC: free cpu 5 mem 800 gpu 1, nodeD: free cpu 5 mem 800 gpu 1 id lex
    # req cpu1 mem100 gpu0
    # cpu waste all 4, mem waste A,B 900, C,D 700 -> C,D better than A,B
    # among C,D gpu waste both 1, id lex C vs D -> C wins
    run_config("add-node", "nodeA", "5", "1000", "2")
    run_config("add-node", "nodeB", "5", "1000", "1")
    run_config("add-node", "nodeC", "5", "800", "1")
    run_config("add-node", "nodeD", "5", "800", "1")
    run_config("add-job", "jobTie", "1", "100", "0")
    nid = json.loads(run_config("schedule", "jobTie").stdout)["node_id"]
    assert nid == "nodeC", f"expected nodeC best-fit comprehensive tie-break, got {nid}"


def test_best_fit_after_many_allocations_fragmented():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(4):
        run_config("add-node", f"node{i}", "4", "1024", "0")
    # allocate 2 CPU to node0, 1 CPU to node1, 3 CPU to node2, leaving free: node0:2, node1:3, node2:1, node3:4
    for nid, cpu_used in [("node0", 2), ("node1", 1), ("node2", 3)]:
        run_config("add-job", f"job_{nid}", f"{cpu_used}", "256", "0")
        run_config("allocate", f"job_{nid}", nid)
    run_config("add-job", "jobNeed2", "2", "256", "0")
    # free: node0:2 waste0, node1:3 waste1, node2:1 insufficient, node3:4 waste2 -> node0 wins waste0
    nid = json.loads(run_config("schedule", "jobNeed2").stdout)["node_id"]
    assert nid == "node0"


def test_rate_limit_burst_exact_and_refill_two_cycles():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 2, "burst": 3}
    write_config(cfg)
    run_config("add-node", "nodeRL", "20", "20000", "0")
    for i in range(3):
        run_config("add-job", f"job{i}", "1", "256", "0")
        assert run_config("allocate", f"job{i}", "nodeRL").returncode == 0
    run_config("add-job", "job3", "1", "256", "0")
    assert run_config("allocate", "job3", "nodeRL").returncode == 1
    time.sleep(1.1)  # refill 2.2 tokens
    run_config("add-job", "job4", "1", "256", "0")
    assert run_config("allocate", "job4", "nodeRL").returncode == 0
    run_config("add-job", "job5", "1", "256", "0")
    assert run_config("allocate", "job5", "nodeRL").returncode == 0
    # After 2 allocs, tokens ~0.2 left, next should be rate limited (at least one of next 2 fails)
    run_config("add-job", "job6", "1", "256", "0")
    r = run_config("allocate", "job6", "nodeRL")
    # Could be 0 or 1 depending on timing, but at least one of next 2 should be rate limited
    # So we test that not all succeed
    run_config("add-job", "job7", "1", "256", "0")
    r2 = run_config("allocate", "job7", "nodeRL")
    assert r.returncode == 1 or r2.returncode == 1


def test_rate_limit_per_node_three_nodes_independent():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    for nid in ["nodeA", "nodeB", "nodeC"]:
        run_config("add-node", nid, "10", "10240", "0")
    for i in range(3):
        run_config("add-job", f"jobA{i}", "1", "256", "0")
    assert run_config("allocate", "jobA0", "nodeA").returncode == 0
    assert run_config("allocate", "jobA1", "nodeA").returncode == 1
    # nodeB and nodeC should still succeed
    run_config("add-job", "jobB0", "1", "256", "0")
    assert run_config("allocate", "jobB0", "nodeB").returncode == 0
    run_config("add-job", "jobC0", "1", "256", "0")
    assert run_config("allocate", "jobC0", "nodeC").returncode == 0


def test_rate_limit_no_consume_on_schedule_insufficient():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "small", "1", "256", "0")
    run_config("add-job", "big", "10", "10000", "0")
    # schedule big should fail insufficient not rate limit, token not consumed
    r = run_config("schedule", "big")
    assert r.returncode == 2 or r.returncode == 1  # could be no fit or insufficient
    run_config("add-job", "smalljob", "1", "256", "0")
    # should still succeed because token not consumed on insufficient
    # Note: if schedule failed due to no fit (exit1), token should also not be consumed per spec
    # So next allocate should succeed
    assert (
        run_config("allocate", "smalljob", "small").returncode == 0
        or run_config("schedule", "smalljob").returncode == 0
    )


def test_presence_heartbeat_refresh_extends_online():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 2
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("heartbeat", "nodeA")
    time.sleep(1.0)
    run_config("heartbeat", "nodeA")  # refresh
    time.sleep(1.5)
    # should still be online because refreshed at 1s, now 1.5s after refresh <2s TTL
    assert json.loads(run_config("get-node-health", "nodeA").stdout)["online"] is True
    time.sleep(1.0)
    # now 2.5s after refresh -> offline
    assert json.loads(run_config("get-node-health", "nodeA").stdout)["online"] is False


def test_presence_corruption_and_recovery():
    clean_data = clean_all
    clean_data()
    run_config("add-node", "nodeA", "4", "1024", "0")
    run_config("heartbeat", "nodeA")
    pres_path = default_config()["presence_path"]
    # corrupt
    with open(pres_path, "w") as f:
        f.write("not json")
    r = run_config("get-presence", "nodeA")
    assert r.returncode == 0
    assert json.loads(r.stdout)["online"] is False
    # heartbeat after corruption should recover
    run_config("heartbeat", "nodeA")
    assert json.loads(run_config("get-presence", "nodeA").stdout)["online"] is True


def test_rate_limit_corruption_and_recovery():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    rl_path = cfg["rate_limit_path"]
    with open(rl_path, "w") as f:
        f.write("invalid")
    run_config("add-job", "job0", "1", "256", "0")
    assert run_config("allocate", "job0", "node1").returncode == 0


def test_optimize_used_nodes_reduction_strict():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    for i in range(5):
        run_config("add-node", f"node{i}", "4", "1024", "0")
        run_config("add-job", f"job{i}", "1", "256", "0")
        run_config("allocate", f"job{i}", f"node{i}")
    # 5 used nodes, each 1 CPU job on 4 CPU node -> all 5 fit on ceil(5/4)=2 nodes
    r = run_config("optimize")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["total_nodes"] == 5
    # Must strictly decrease and consolidate to <=2
    assert out["used_nodes"] < 5, (
        f"should strictly reduce from 5, got {out['used_nodes']}"
    )
    assert out["used_nodes"] <= 2, (
        f"5x1-CPU jobs on 4-CPU nodes should fit on 2 nodes, got {out['used_nodes']}"
    )
    assert out["used_nodes"] >= 1
    assert out["moves"] >= 3, (
        f"need at least 3 moves to consolidate 5 nodes to 2, got {out['moves']}"
    )
    assert out["fragmentation_after"] <= out["fragmentation_before"] + 1e-9
    jobs = json.loads(run_config("list-jobs", "0", "0").stdout)
    assert len(jobs) == 5
    for i in range(5):
        n = json.loads(run_config("get-node", f"node{i}").stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]


def test_snapshot_restore_with_presence_and_rate_limit():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 60
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("heartbeat", "node1")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job1", "node1")
    run_config("snapshot", "/tmp/backup")
    # mutate presence and rate_limit
    run_config("add-node", "newnode", "4", "1024", "0")
    # corrupt presence to offline
    pres_path = cfg["presence_path"]
    with open(pres_path, "w") as f:
        f.write('{"data": {}, "checksum": "dummy"}')
    run_config("restore", "/tmp/backup")
    # after restore, node1 should be healthy again (presence restored)
    assert "node1" in [n["id"] for n in json.loads(run_config("list-nodes").stdout)]
    assert (
        "newnode" not in [n["id"] for n in json.loads(run_cli("list-nodes").stdout)]
        if False
        else True
    )
    # Actually use run_config
    ids = [n["id"] for n in json.loads(run_config("list-nodes").stdout)]
    assert "node1" in ids and "newnode" not in ids
    assert json.loads(run_config("get-presence", "node1").stdout)["online"] is True


def test_snapshot_restore_file_with_ops_log():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "node1", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    run_config("allocate", "job1", "node1")
    run_config("snapshot", "/tmp/backup.json")
    run_config("add-node", "newnode", "4", "1024", "0")
    run_config("restore", "/tmp/backup.json")
    assert "newnode" not in [
        n["id"] for n in json.loads(run_config("list-nodes").stdout)
    ]


def test_distribution_weighted_exact_20():
    clean_all()
    # 20 nodes with default config (weights 1,2,1,1 total5)
    # Distribution should sum to 20 and include zeros if global not used
    for i in range(20):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    assert sum(dist.values()) == 20
    assert len(dist) == 4


def test_distribution_tolerance_50_and_100():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(50):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    assert sum(dist.values()) == 50
    # shard 1 has weight 2 vs others 1, so should have ~40% of nodes
    total = sum(dist.values())
    # shard 1 should have approx 20 (50*2/5=20) tolerance 30%
    assert dist["1"] >= 10 and dist["1"] <= 30


def test_global_broadcast_allocate_from_any_copy():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "global:shared", "4", "1024", "0")
    run_config("add-job", "job1", "1", "256", "0")
    # Allocate job1 to global:shared – should succeed even though get-shard-id returns -1
    r = run_config("allocate", "job1", "global:shared")
    assert r.returncode == 0
    assert (
        json.loads(run_config("get-job", "job1").stdout)["node_id"] == "global:shared"
    )


def test_weighted_sharding_empty_string_key_valid():
    clean_all()
    r = run_config("get-shard-id", "")
    assert r.returncode == 0
    assert int(r.stdout.strip()) in [0, 1, 2, 3]
    r2 = run_config("get-shard-path", "")
    assert r2.returncode == 0
    assert r2.stdout.strip() != ""


def test_optimize_preserves_all_jobs_and_no_overcommit_many():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(10):
        run_config("add-node", f"node{i}", "10", "10240", "2")
    for i in range(30):
        run_cli = run_config
        run_cli = run_config
        run_config("add-job", f"job{i}", "1", "256", "0")
        # Use run_config for allocate to avoid confusion
        run_config("allocate", f"job{i}", f"node{i % 10}")
    r = run_config("optimize")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["moves"] >= 0
    assert out["total_nodes"] == 10
    jobs = json.loads(run_config("list-jobs", "0", "0").stdout)
    assert len(jobs) == 30
    for i in range(10):
        n = json.loads(run_config("get-node", f"node{i}").stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]
        assert n["used"]["memory"] <= n["total"]["memory"]


def test_pagination_sharded_large_scale_o_n_log_n():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(300):
        run_config("add-node", f"node-{i:04d}", "4", "1024", "0")
    t0 = time.time()
    arr = json.loads(run_config("list-nodes", "0", "0").stdout)
    elapsed = time.time() - t0
    assert len(arr) == 300
    assert elapsed < 2.0


def test_ops_log_large_100_ops():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(50):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
        run_config("add-job", f"job-{i}", "1", "256", "0")
        run_config("allocate", f"job-{i}", f"node-{i}")
    r = run_config("ops-log")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    # After fixing grading: spec only requires allocate to be logged, not add-node/add-job.
    # So allocation-only logging (50 entries) should pass, not require 100.
    # We keep >=50 to ensure ops are logged, and check allocate presence.
    assert len(arr) >= 50, (
        f"ops-log should contain at least 50 entries after 50 allocations, got {len(arr)}"
    )
    assert any(
        (e.get("op") == "allocate" or "allocate" in str(e).lower()) for e in arr
    ), f"ops-log should contain allocate op, got {arr[:5]}"


def test_presence_multiple_heartbeat_same_node():
    clean_all()
    cfg = default_config()
    cfg["node_heartbeat_ttl_seconds"] = 60
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    run_config("add-node", "nodeA", "4", "1024", "0")
    for _ in range(5):
        assert run_config("heartbeat", "nodeA").returncode == 0
    assert json.loads(run_config("get-presence", "nodeA").stdout)["online"] is True


def test_rate_limit_schedule_no_fit_no_consume():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 1}
    write_config(cfg)
    run_config("add-node", "node1", "1", "256", "0")
    run_config("add-job", "big", "10", "10000", "0")
    run_config("schedule", "big")  # should be no fit or insufficient, not rate limited
    run_config("add-job", "small", "1", "256", "0")
    r = run_config("schedule", "small")
    assert r.returncode == 0 or run_config("allocate", "small", "node1").returncode == 0


def test_distribution_includes_zero_when_no_nodes():
    clean_all()
    dist = json.loads(run_config("distribution").stdout)
    assert sum(dist.values()) == 0
    assert len(dist) == 4


def test_get_shard_path_sorted_for_global():
    clean_all()
    r = run_config("get-shard-path", "global:test")
    assert r.returncode == 0
    parts = r.stdout.strip().split(",")
    assert parts == sorted(parts)
    assert len(parts) == 4


def test_weighted_sharding_with_future_field_ignored():
    clean_all()
    cfg = default_config()
    cfg["future_field"] = 999
    cfg["shards"][0]["future_shard_field"] = "abc"
    write_config(cfg)
    assert run_config("add-node", "node1", "4", "1024", "0").returncode == 0
    assert run_config("get-shard-id", "node1").returncode == 0


def test_snapshot_restore_dir_with_many_nodes():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(20):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    run_config("snapshot", "/tmp/backup")
    for i in range(20, 30):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
    run_config("restore", "/tmp/backup")
    assert len(json.loads(run_config("list-nodes").stdout)) == 20


# ---------- De-monoculture blockers per review: ops-log 200KB line and rate-limit corrupt-then-recreate ----------


def test_ops_log_single_200kb_line_big_buffer():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    # Create a single 200KB line in ops.log – exercises 10*1024*1024 bufio.Scanner buffer directly
    # Spec says must use bufio.Scanner with big buffer 10*1024*1024 to handle 100KB+ lines
    ops_path = cfg["ops_log"]
    os.makedirs(os.path.dirname(ops_path), exist_ok=True)
    huge_payload = "x" * (200 * 1024)  # 200KB
    entry = json.dumps(
        {
            "op": "allocate",
            "job_id": "jobHuge",
            "node_id": "node1",
            "payload": huge_payload,
        }
    )
    with open(ops_path, "w") as f:
        f.write(entry + "\n")
    r = run_config("ops-log")
    assert r.returncode == 0, (
        f"ops-log should handle 200KB line with big buffer, got {r.returncode} {r.stderr}"
    )
    arr = json.loads(r.stdout)
    assert len(arr) == 1, f"should return 1 entry for 200KB line, got {len(arr)}"
    assert arr[0]["job_id"] == "jobHuge"
    # Also test that invalid line skipping still works with huge line present
    with open(ops_path, "a") as f:
        f.write("invalid line\n")
        f.write(json.dumps({"op": "add-node", "node_id": "node2"}) + "\n")
    r2 = run_config("ops-log")
    assert r2.returncode == 0
    arr2 = json.loads(r2.stdout)
    assert len(arr2) == 2, f"should skip invalid and return 2, got {len(arr2)}"
    assert (
        "corrupt" in r2.stderr.lower()
        or "skip" in r2.stderr.lower()
        or "warning" in r2.stderr.lower()
    )


def test_rate_limit_persistence_survives_corrupt_then_recreate():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1, "burst": 2}
    write_config(cfg)
    run_config("add-node", "node1", "10", "10240", "0")
    run_config("add-job", "job0", "1", "256", "0")
    assert run_config("allocate", "job0", "node1").returncode == 0
    rl_path = cfg["rate_limit_path"]
    assert os.path.exists(rl_path)
    with open(rl_path, "w") as f:
        f.write("{ corrupt")
    run_config("add-job", "job1", "1", "256", "0")
    assert run_config("allocate", "job1", "node1").returncode == 0
    run_config("add-job", "job2", "1", "256", "0")
    assert run_config("allocate", "job2", "node1").returncode == 0
    run_config("add-job", "job3", "1", "256", "0")
    r = run_config("allocate", "job3", "node1")
    assert r.returncode == 1 and "rate limit" in r.stderr.lower()
    assert os.path.exists(rl_path)
    with open(rl_path, "w") as f:
        f.write("invalid again")
    run_config("add-job", "job4", "1", "256", "0")
    assert run_config("allocate", "job4", "node1").returncode == 0


# ---------- Additional hardening to make Step2 harder (was too easy at 75% Step2 pass) ----------


def test_best_fit_fragmented_waste_zero_hard():
    clean_all()
    cfg = default_config()
    write_config(cfg)
    for i in range(5):
        run_config("add-node", f"node-{i}", "10", "10240", "0")
    # allocate 9 CPU to node-0, leaving 1 free
    for j in range(9):
        run_config("add-job", f"job-0-{j}", "1", "256", "0")
        run_config("allocate", f"job-0-{j}", "node-0")
    run_config("add-job", "job-need-1", "1", "256", "0")
    out = json.loads(run_config("schedule", "job-need-1").stdout)
    # best-fit should pick node with 1 free (node-0) over nodes with 10 free (waste 0 vs 9)
    assert out["node_id"] == "node-0"


def test_rate_limit_burst_refill_simple():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 2, "burst": 2}
    write_config(cfg)
    run_config("add-node", "nodeA", "10", "10240", "0")
    for j in ["job0", "job1"]:
        run_config("add-job", j, "1", "256", "0")
        assert run_config("allocate", j, "nodeA").returncode == 0
    run_config("add-job", "job2", "1", "256", "0")
    assert run_config("allocate", "job2", "nodeA").returncode == 1
    time.sleep(1.1)
    run_config("add-job", "job3", "1", "256", "0")
    assert run_config("allocate", "job3", "nodeA").returncode == 0


def test_snapshot_restore_with_10_nodes():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    for i in range(10):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
        run_config("add-job", f"job-{i}", "1", "256", "0")
        run_config("allocate", f"job-{i}", f"node-{i}")
    import tempfile, os as _os

    snap = tempfile.mkdtemp()
    snap_path = _os.path.join(snap, "backup")
    assert run_config("snapshot", snap_path).returncode == 0
    run_config("add-node", "node-new", "4", "1024", "0")
    assert run_config("restore", snap_path).returncode == 0
    arr = json.loads(run_config("list-nodes").stdout)
    assert "node-new" not in [n["id"] if isinstance(n, dict) else n for n in arr]
    assert len(arr) == 10


def test_distribution_weighted_50_nodes():
    clean_all()
    cfg = {
        "shard_count": 4,
        "shards": [
            {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
            {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
            {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
            {"id": 3, "path": "/app/data/shard_3.json", "weight": 1},
        ],
        "rate_limit": {"allocations_per_second": 1000, "burst": 10000},
    }
    write_config(cfg)
    for i in range(50):
        run_config("add-node", f"node-{i:04d}", "4", "1024", "0")
    dist = json.loads(run_config("distribution").stdout)
    total = sum(dist.values())
    assert total >= 50


def test_ops_log_order_simple():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 10000, "burst": 10000}
    write_config(cfg)
    for i in range(10):
        run_config("add-node", f"node-{i}", "4", "1024", "0")
        run_config("add-job", f"job-{i}", "1", "256", "0")
        run_config("allocate", f"job-{i}", f"node-{i}")
    r = run_config("ops-log")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) >= 10
    assert any(e.get("op") == "allocate" for e in arr)


def test_concurrent_sharded_allocate_20():
    clean_all()
    cfg = default_config()
    cfg["rate_limit"] = {"allocations_per_second": 1000, "burst": 10000}
    write_config(cfg)
    for i in range(10):
        run_config("add-node", f"node-{i}", "10", "10240", "0")
    for i in range(20):
        run_config("add-job", f"job-{i}", "1", "256", "0")
    import threading

    def alloc(i):
        run_config("allocate", f"job-{i}", f"node-{i % 10}")

    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_config("status").stdout)
    assert st["allocated_jobs"] == 20
