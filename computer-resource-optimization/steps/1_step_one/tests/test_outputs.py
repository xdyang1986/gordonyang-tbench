"""
T-Bench Turn1 tests for computer cluster management system.
Black-box tests: build Go binary and drive via CLI args, checking file persistence, checksum, atomicity.
"""

import os
import sys
import json
import hashlib
import subprocess
import time
import threading
import shutil
import tempfile

import pytest

APP = "/app"
BIN = "/tmp/cluster_manager"
DATA_DIR = "/app/data"
DATA_FILE = "/app/data/cluster.json"
LOCK_FILE = DATA_FILE + ".lock"

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
                except OSError:
                    pass
    return None


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
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, (
        f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert os.path.exists(BIN), "binary not built"
    yield


def run_cli(*args, data_path=DATA_FILE, timeout=15):
    """Run binary with --data flag and args list, return CompletedProcess."""
    cmd = [BIN, "--data", data_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def run_cli_no_data_flag(*args, timeout=15):
    """Run binary without explicit --data to test default."""
    cmd = [BIN] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def clean_data(path=DATA_FILE):
    # remove data file and lock and corrupt backups, and also sharded files and config to force single-file mode
    for fp in [path, path+".lock", "/app/config.json", "/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json", "/app/data/jobs.json", "/app/data/presence.json", "/app/data/rate_limit.json", "/app/data/counter.json", "/app/data/cluster_ops.log", "/app/data/nodes_index.json", "/app/data/global.lock"]:
        try:
            os.remove(fp)
        except FileNotFoundError:
            pass
    # remove corrupt backups for all
    d = os.path.dirname(path)
    try:
        for fname in os.listdir(d):
            if ".corrupt." in fname or fname.endswith(".lock"):
                try:
                    os.remove(os.path.join(d, fname))
                except:
                    pass
    except FileNotFoundError:
        pass


def read_wrapper_raw(path=DATA_FILE):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_data(path=DATA_FILE):
    if not os.path.exists(path):
        return {"nodes": {}, "jobs": {}}
    txt = open(path, "r", encoding="utf-8").read().strip()
    if txt == "":
        return {"nodes": {}, "jobs": {}}
    obj = json.loads(txt)
    if "data" in obj:
        return obj["data"]
    return obj  # fallback if old format


def checksum_valid(path=DATA_FILE):
    raw = read_wrapper_raw(path)
    if raw is None:
        return True
    raw = raw.strip()
    if raw == "":
        return True
    try:
        obj = json.loads(raw)
    except:
        return False
    if "data" not in obj or "checksum" not in obj:
        return False
    data = obj["data"]
    cs = obj["checksum"]
    if not cs:
        return False
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    exp = hashlib.md5(canonical.encode()).hexdigest()
    return cs == exp


def test_help_contains_keywords():
    clean_data()
    r = run_cli()
    assert r.returncode == 0
    out = r.stdout.lower()
    for kw in [
        "add-node",
        "remove-node",
        "list-nodes",
        "get-node",
        "add-job",
        "remove-job",
        "list-jobs",
        "get-job",
        "allocate",
        "deallocate",
        "schedule",
        "status",
        "data",
        "checksum",
    ]:
        assert kw in out, f"help missing keyword {kw}"

    r2 = subprocess.run([BIN, "--help"], capture_output=True, text=True, timeout=10)
    assert r2.returncode == 0
    assert "add-node" in r2.stdout.lower()

    r3 = subprocess.run([BIN, "-h"], capture_output=True, text=True, timeout=10)
    assert r3.returncode == 0

    r4 = subprocess.run([BIN, "help"], capture_output=True, text=True, timeout=10)
    assert r4.returncode == 0


def test_unknown_command_exit2():
    clean_data()
    r = run_cli("unknown-cmd")
    assert r.returncode == 2


def test_missing_args_exit2():
    clean_data()
    r = run_cli("add-node", "node1")
    assert r.returncode == 2
    r = run_cli("add-job", "job1")
    assert r.returncode == 2
    r = run_cli("allocate", "job1")
    assert r.returncode == 2
    r = run_cli("get-node")
    assert r.returncode == 2


def test_empty_id_exit2():
    clean_data()
    r = run_cli("add-node", "", "4", "1024", "1")
    assert r.returncode == 2
    r = run_cli("get-node", "")
    assert r.returncode == 2
    r = run_cli("add-job", "", "1", "256", "0")
    assert r.returncode == 2


def test_invalid_resources_exit2():
    clean_data()
    r = run_cli("add-node", "n1", "0", "1024", "1")
    assert r.returncode == 2
    r = run_cli("add-node", "n1", "-1", "1024", "1")
    assert r.returncode == 2
    r = run_cli("add-node", "n1", "4", "0", "1")
    assert r.returncode == 2
    r = run_cli("add-node", "n1", "4", "1024", "-1")
    assert r.returncode == 2
    r = run_cli("add-node", "n1", "abc", "1024", "1")
    assert r.returncode == 2
    r = run_cli("add-job", "j1", "0", "256", "0")
    assert r.returncode == 2
    r = run_cli("add-job", "j1", "1", "-1", "0")
    assert r.returncode == 2


def test_add_node_list_get():
    clean_data()
    r = run_cli("add-node", "nodeA", "4", "1024", "1")
    assert r.returncode == 0, r.stderr
    r = run_cli("add-node", "nodeB", "8", "2048", "2")
    assert r.returncode == 0
    r = run_cli("list-nodes")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert isinstance(arr, list)
    assert len(arr) == 2
    ids = [n["id"] for n in arr]
    assert ids == sorted(ids)
    assert "nodeA" in ids and "nodeB" in ids

    r = run_cli("get-node", "nodeA")
    assert r.returncode == 0
    node = json.loads(r.stdout)
    assert node["id"] == "nodeA"
    assert node["total"]["cpu"] == 4
    assert node["free"]["cpu"] == 4
    assert node["jobs"] == []


def test_add_node_idempotent():
    clean_data()
    r = run_cli("add-node", "nodeX", "4", "1024", "1")
    assert r.returncode == 0
    r2 = run_cli("add-node", "nodeX", "8", "2048", "2")
    assert r2.returncode == 0
    r = run_cli("get-node", "nodeX")
    node = json.loads(r.stdout)
    # should keep original resources (idempotent, not update)
    assert node["total"]["cpu"] == 4


def test_remove_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0
    assert "true" in r.stdout.lower()
    r = run_cli("list-nodes")
    arr = json.loads(r.stdout)
    assert len(arr) == 0

    r = run_cli("remove-node", "noexist")
    assert r.returncode == 0
    assert "false" in r.stdout.lower()


def test_remove_node_with_jobs_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 2, "should fail when node has allocated jobs"


def test_add_job_list_get():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    r = run_cli("add-job", "jobA", "1", "256", "0")
    assert r.returncode == 0
    r = run_cli("add-job", "jobB", "2", "512", "0")
    assert r.returncode == 0
    r = run_cli("list-jobs")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) == 2
    ids = [j["id"] for j in arr]
    assert ids == sorted(ids)

    r = run_cli("get-job", "jobA")
    assert r.returncode == 0
    job = json.loads(r.stdout)
    assert job["id"] == "jobA"
    assert job["status"] == "pending"
    assert job["node_id"] == ""


def test_remove_job_deallocates():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("get-node", "node1")
    node = json.loads(r.stdout)
    assert len(node["jobs"]) == 1

    r = run_cli("remove-job", "job1")
    assert r.returncode == 0
    assert "true" in r.stdout.lower()

    r = run_cli("get-node", "node1")
    node = json.loads(r.stdout)
    assert node["jobs"] == []
    assert node["used"]["cpu"] == 0

    r = run_cli("remove-job", "job1")
    assert r.returncode == 0
    assert "false" in r.stdout.lower()


def test_allocate_success():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 0, r.stderr
    alloc = json.loads(r.stdout)
    assert alloc["job_id"] == "job1"
    assert alloc["node_id"] == "node1"

    r = run_cli("get-job", "job1")
    job = json.loads(r.stdout)
    assert job["node_id"] == "node1"
    assert job["status"] == "running"

    r = run_cli("get-node", "node1")
    node = json.loads(r.stdout)
    assert node["used"]["cpu"] == 1
    assert "job1" in node["jobs"]


def test_allocate_idempotent_same_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 0


def test_allocate_different_node_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-node", "node2", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("allocate", "job1", "node2")
    assert r.returncode == 2


def test_allocate_insufficient():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "job1", "4", "1024", "0")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 2
    assert "insufficient" in r.stderr.lower()


def test_allocate_nonexist_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    r = run_cli("allocate", "nojob", "node1")
    assert r.returncode == 2
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("allocate", "job1", "nonode")
    assert r.returncode == 2


def test_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")

    r = run_cli("deallocate", "job1")
    assert r.returncode == 0
    assert "true" in r.stdout.lower()

    r = run_cli("get-job", "job1")
    job = json.loads(r.stdout)
    assert job["node_id"] == ""
    assert job["status"] == "pending"

    r = run_cli("get-node", "node1")
    node = json.loads(r.stdout)
    assert node["used"]["cpu"] == 0

    r = run_cli("deallocate", "job1")
    assert r.returncode == 0
    assert "false" in r.stdout.lower()

    r = run_cli("deallocate", "nojob")
    assert r.returncode == 2


def test_schedule_first_fit():
    clean_data()
    run_cli("add-node", "nodeA", "2", "512", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("schedule", "job1")
    assert r.returncode == 0
    sched = json.loads(r.stdout)
    # first-fit sorted by id asc, nodeA should be picked
    assert sched["node_id"] == "nodeA"

    run_cli("add-job", "job2", "2", "512", "0")
    r = run_cli("schedule", "job2")
    assert r.returncode == 0
    sched = json.loads(r.stdout)
    # nodeA now has only 1 cpu free, needs 2, so should pick nodeB
    assert sched["node_id"] == "nodeB"


def test_schedule_no_fit():
    clean_data()
    run_cli("add-node", "node1", "1", "256", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    r = run_cli("schedule", "job1")
    assert r.returncode == 1
    assert "no fit" in r.stderr.lower()
    assert r.stdout.strip() == ""


def test_schedule_already_allocated_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("schedule", "job1")
    assert r.returncode == 2


def test_status():
    clean_data()
    run_cli("add-node", "n1", "4", "1024", "1")
    run_cli("add-node", "n2", "2", "512", "0")
    run_cli("add-job", "j1", "1", "256", "0")
    run_cli("add-job", "j2", "1", "256", "0")
    run_cli("allocate", "j1", "n1")
    r = run_cli("status")
    assert r.returncode == 0
    st = json.loads(r.stdout)
    assert st["total_nodes"] == 2
    assert st["total_jobs"] == 2
    assert st["allocated_jobs"] == 1
    assert st["pending_jobs"] == 1
    assert st["total_resources"]["cpu"] == 6
    assert st["used_resources"]["cpu"] == 1


def test_special_chars_no_escape():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert "<" in raw, "raw file must contain < without HTML escaping"
    assert "\\u003c" not in raw.lower()
    r = run_cli("get-node", "node<>&")
    assert r.returncode == 0
    node = json.loads(r.stdout)
    assert node["id"] == "node<>&"

    run_cli("add-job", "job<>&", "1", "256", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert "<" in raw
    r = run_cli("get-job", "job<>&")
    assert r.returncode == 0
    assert r.stdout.find("<") != -1 or json.loads(r.stdout)["id"] == "job<>&"


def test_unicode_preserved():
    clean_data()
    run_cli("add-node", "node-🌍", "4", "1024", "0")
    r = run_cli("get-node", "node-🌍")
    assert r.returncode == 0
    node = json.loads(r.stdout)
    assert "🌍" in node["id"]
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert "🌍" in raw


def test_large_scale_sorted():
    clean_data()
    for i in range(200):
        run_cli("add-node", f"node-{i:03d}", "4", "1024", "0")
    r = run_cli("list-nodes")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) == 200
    ids = [n["id"] for n in arr]
    assert ids == sorted(ids)


def test_large_history_perf():
    clean_data()
    start = time.time()
    for i in range(500):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    elapsed = time.time() - start
    assert elapsed < 20, f"500 add-node too slow: {elapsed}s"

    r = run_cli("list-nodes")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) == 500
    # test list perf <2s for 500 nodes
    start = time.time()
    r = run_cli("list-nodes")
    elapsed = time.time() - start
    assert elapsed < 2, f"list-nodes 500 too slow: {elapsed}s"


def test_checksum_and_atomic():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert checksum_valid(), "checksum should be valid after write"
    raw = read_wrapper_raw()
    assert '"checksum"' in raw
    # file should not have escaped <
    # atomic check: source must contain CreateTemp and Rename and SetEscapeHTML
    go_files = []
    for root, _, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))
    found_create = False
    found_rename = False
    found_escape = False
    for gf in go_files:
        try:
            content = open(gf, "r").read()
            if "CreateTemp" in content:
                found_create = True
            if "Rename" in content:
                found_rename = True
            if "SetEscapeHTML" in content:
                found_escape = True
        except:
            pass
    assert found_create, "source must contain CreateTemp for atomic writes"
    assert found_rename, "source must contain Rename for atomic writes"
    assert found_escape, "source must contain SetEscapeHTML(false)"


def test_corruption_handling():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    # corrupt file: invalid JSON
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid json")
    r = run_cli("list-nodes")
    # should handle corruption, backup, recreate empty, return []
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert arr == []
    # backup file should exist
    d = os.path.dirname(DATA_FILE)
    backups = [
        f
        for f in os.listdir(d)
        if f.startswith(os.path.basename(DATA_FILE) + ".corrupt.")
    ]
    assert len(backups) >= 1, "corruption should create backup file"
    # stderr should contain corrupt or checksum
    # our run_cli captured stderr for list-nodes which in this case is read path that does corruption handling
    # need to check that during corruption, stderr warns
    # rerun with explicit corrupt again to capture stderr
    with open(DATA_FILE, "w") as f:
        f.write('{"data": {"nodes":{}, "jobs":{}}, "checksum": "bad"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0
    assert "corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower()


def test_missing_checksum_corruption():
    clean_data()
    # write wrapper without checksum
    with open(DATA_FILE, "w") as f:
        json.dump({"data": {"nodes": {}, "jobs": {}}}, f)
    r = run_cli("list-nodes")
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert arr == []
    d = os.path.dirname(DATA_FILE)
    backups = [
        f
        for f in os.listdir(d)
        if f.startswith(os.path.basename(DATA_FILE) + ".corrupt.")
    ]
    assert len(backups) >= 1


def test_concurrent_allocate_same_node():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")

    def alloc_job(j):
        run_cli("allocate", f"job{j}", "node1")

    threads = []
    for i in range(20):
        t = threading.Thread(target=alloc_job, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    r = run_cli("get-node", "node1")
    assert r.returncode == 0
    node = json.loads(r.stdout)
    assert len(node["jobs"]) == 20, f"expected 20 jobs allocated, got {node['jobs']}"
    assert node["used"]["cpu"] == 20
    # file must still be valid JSON and checksum valid
    assert checksum_valid()
    # lock file must not remain
    assert not os.path.exists(LOCK_FILE), "lock file must be cleaned up"


def test_concurrent_allocate_diff_nodes():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node{i}", "4", "1024", "0")
        run_cli("add-job", f"job{i}", "1", "256", "0")

    def alloc_pair(i):
        run_cli("allocate", f"job{i}", f"node{i}")

    threads = []
    for i in range(20):
        t = threading.Thread(target=alloc_pair, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    for i in range(20):
        r = run_cli("get-node", f"node{i}")
        node = json.loads(r.stdout)
        assert len(node["jobs"]) == 1
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_concurrent_add_node():
    clean_data()

    def add_node(i):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")

    threads = []
    for i in range(20):
        t = threading.Thread(target=add_node, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    r = run_cli("list-nodes")
    arr = json.loads(r.stdout)
    assert len(arr) == 20
    assert not os.path.exists(LOCK_FILE)


def test_file_lock_cleanup():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists(LOCK_FILE), "lock file must not remain after command"


def test_stdlib_only():
    # check imports
    found_non_stdlib = False
    for root, _, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                path = os.path.join(root, f)
                try:
                    content = open(path).read()
                    # naive check for import containing dot
                    # we check go list output via subprocess?
                except:
                    pass
    # Use go list to check imports
    result = subprocess.run(
        ["go", "list", "-f", '{{join .Imports " "}}', "."],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        imports = result.stdout
        # stdlib imports have no dot
        for imp in imports.split():
            assert "." not in imp, f"non-stdlib import found: {imp}"


def test_get_nonexist_fails():
    clean_data()
    r = run_cli("get-node", "noexist")
    assert r.returncode == 2
    r = run_cli("get-job", "noexist")
    assert r.returncode == 2


def test_list_empty():
    clean_data()
    r = run_cli("list-nodes")
    assert r.returncode == 0
    assert json.loads(r.stdout) == []
    r = run_cli("list-jobs")
    assert r.returncode == 0
    assert json.loads(r.stdout) == []


def test_node_id_special_chars():
    clean_data()
    for nid in ["node-dash", "node_underscore", "node.dot", "node:colon"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode == 0, f"failed for {nid}: {r.stderr}"
    r = run_cli("list-nodes")
    arr = json.loads(r.stdout)
    assert len(arr) == 4


def test_persistence_across_restarts():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    # Re-read via new process list
    r = run_cli("get-node", "node1")
    node = json.loads(r.stdout)
    assert "job1" in node["jobs"]
    r = run_cli("get-job", "job1")
    job = json.loads(r.stdout)
    assert job["node_id"] == "node1"
