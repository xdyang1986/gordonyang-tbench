"""
Turn2 extra hard 46 tests for efficient cluster management.

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
    canonical = json.dumps(obj["data"], sort_keys=True, separators=(",", ":"))
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


