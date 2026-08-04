"""
T-Bench Turn1 HARD 65 tests for computer cluster management system.
Covers core functionality with extra hard edge cases, large scale, concurrency, integrity, special chars, unicode, atomicity.
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

def run_cli(*args, data_path=DATA_FILE, timeout=15):
    cmd = [BIN, "--data", data_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def clean_data(path=DATA_FILE):
    for fp in [path, path+".lock", "/app/config.json", "/app/data/shard_0.json", "/app/data/shard_1.json", "/app/data/shard_2.json", "/app/data/shard_3.json", "/app/data/jobs.json", "/app/data/presence.json", "/app/data/rate_limit.json", "/app/data/counter.json", "/app/data/cluster_ops.log", "/app/data/nodes_index.json", "/app/data/global.lock"]:
        try:
            os.remove(fp)
        except FileNotFoundError:
            pass
    d = os.path.dirname(path)
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
    return open(path, "r", encoding="utf-8").read()

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
    if not obj["checksum"]:
        return False
    canonical = json.dumps(obj["data"], sort_keys=True, separators=(',', ':'))
    exp = hashlib.md5(canonical.encode()).hexdigest()
    return obj["checksum"] == exp

# ----- help -----
def test_help_contains_keywords():
    clean_data()
    r = run_cli()
    assert r.returncode == 0
    out = r.stdout.lower()
    for kw in ["add-node", "remove-node", "list-nodes", "get-node", "add-job", "remove-job", "list-jobs", "get-job", "allocate", "deallocate", "schedule", "status", "data", "checksum"]:
        assert kw in out

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
    assert run_cli("add-node", "node1").returncode == 2
    assert run_cli("add-job", "job1").returncode == 2
    assert run_cli("allocate", "job1").returncode == 2
    assert run_cli("get-node").returncode == 2
    assert run_cli("get-job").returncode == 2
    assert run_cli("remove-node").returncode == 2
    assert run_cli("remove-job").returncode == 2
    assert run_cli("deallocate").returncode == 2
    assert run_cli("schedule").returncode == 2

def test_empty_id_exit2():
    clean_data()
    assert run_cli("add-node", "", "4", "1024", "1").returncode == 2
    assert run_cli("get-node", "").returncode == 2
    assert run_cli("add-job", "", "1", "256", "0").returncode == 2
    assert run_cli("get-job", "").returncode == 2
    assert run_cli("remove-node", "").returncode == 2
    assert run_cli("remove-job", "").returncode == 2
    assert run_cli("allocate", "", "node1").returncode == 2
    assert run_cli("allocate", "job1", "").returncode == 2

def test_invalid_resources_exit2():
    clean_data()
    assert run_cli("add-node", "n1", "0", "1024", "1").returncode == 2
    assert run_cli("add-node", "n1", "-1", "1024", "1").returncode == 2
    assert run_cli("add-node", "n1", "4", "0", "1").returncode == 2
    assert run_cli("add-node", "n1", "4", "1024", "-1").returncode == 2
    assert run_cli("add-node", "n1", "abc", "1024", "1").returncode == 2
    assert run_cli("add-job", "j1", "0", "256", "0").returncode == 2
    assert run_cli("add-job", "j1", "1", "-1", "0").returncode == 2
    assert run_cli("add-job", "j1", "1", "256", "-1").returncode == 2
    assert run_cli("add-job", "j1", "1.5", "256", "0").returncode == 2

def test_add_node_list_get():
    clean_data()
    assert run_cli("add-node", "nodeA", "4", "1024", "1").returncode == 0
    assert run_cli("add-node", "nodeB", "8", "2048", "2").returncode == 0
    r = run_cli("list-nodes")
    arr = json.loads(r.stdout)
    assert len(arr) == 2
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    r = run_cli("get-node", "nodeA")
    node = json.loads(r.stdout)
    assert node["id"] == "nodeA"
    assert node["total"]["cpu"] == 4
    assert node["free"]["cpu"] == 4
    assert node["jobs"] == []

def test_add_node_idempotent():
    clean_data()
    run_cli("add-node", "nodeX", "4", "1024", "1")
    r2 = run_cli("add-node", "nodeX", "8", "2048", "2")
    assert r2.returncode == 0
    r = run_cli("get-node", "nodeX")
    assert json.loads(r.stdout)["total"]["cpu"] == 4

def test_remove_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-nodes").stdout) == []
    r = run_cli("remove-node", "noexist")
    assert r.returncode == 0 and "false" in r.stdout.lower()

def test_remove_node_with_jobs_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("remove-node", "node1").returncode == 2

def test_add_job_list_get():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert run_cli("add-job", "jobA", "1", "256", "0").returncode == 0
    assert run_cli("add-job", "jobB", "2", "512", "0").returncode == 0
    arr = json.loads(run_cli("list-jobs").stdout)
    assert len(arr) == 2
    ids = [j["id"] for j in arr]
    assert ids == sorted(ids)
    job = json.loads(run_cli("get-job", "jobA").stdout)
    assert job["id"] == "jobA" and job["status"] == "pending" and job["node_id"] == ""

def test_remove_job_deallocates():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert len(json.loads(run_cli("get-node", "node1").stdout)["jobs"]) == 1
    r = run_cli("remove-job", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("get-node", "node1").stdout)["jobs"] == []
    assert json.loads(run_cli("get-node", "node1").stdout)["used"]["cpu"] == 0
    r = run_cli("remove-job", "job1")
    assert r.returncode == 0 and "false" in r.stdout.lower()

def test_allocate_success():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 0
    assert json.loads(r.stdout)["job_id"] == "job1"
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == "node1"
    assert json.loads(run_cli("get-node", "node1").stdout)["used"]["cpu"] == 1

def test_allocate_idempotent_same_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("allocate", "job1", "node1").returncode == 0

def test_allocate_different_node_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-node", "node2", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("allocate", "job1", "node2").returncode == 2

def test_allocate_insufficient():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "job1", "4", "1024", "0")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()

def test_allocate_insufficient_mem_gpu():
    clean_data()
    run_cli("add-node", "node1", "10", "512", "1")
    run_cli("add-job", "job1", "1", "1024", "0")
    assert run_cli("allocate", "job1", "node1").returncode == 2
    run_cli("add-job", "job2", "1", "100", "2")
    assert run_cli("allocate", "job2", "node1").returncode == 2

def test_allocate_nonexist_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert run_cli("allocate", "nojob", "node1").returncode == 2
    run_cli("add-job", "job1", "1", "256", "0")
    assert run_cli("allocate", "job1", "nonode").returncode == 2

def test_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("deallocate", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == ""
    assert json.loads(run_cli("get-node", "node1").stdout)["used"]["cpu"] == 0
    r = run_cli("deallocate", "job1")
    assert r.returncode == 0 and "false" in r.stdout.lower()
    assert run_cli("deallocate", "nojob").returncode == 2

def test_schedule_first_fit():
    clean_data()
    run_cli("add-node", "nodeA", "2", "512", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("schedule", "job1")
    assert json.loads(r.stdout)["node_id"] == "nodeA"
    run_cli("add-job", "job2", "2", "512", "0")
    assert json.loads(run_cli("schedule", "job2").stdout)["node_id"] == "nodeB"

def test_schedule_no_fit():
    clean_data()
    run_cli("add-node", "node1", "1", "256", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    r = run_cli("schedule", "job1")
    assert r.returncode == 1 and "no fit" in r.stderr.lower() and r.stdout.strip() == ""

def test_schedule_already_allocated_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("schedule", "job1").returncode == 2

def test_status():
    clean_data()
    run_cli("add-node", "n1", "4", "1024", "1")
    run_cli("add-node", "n2", "2", "512", "0")
    run_cli("add-job", "j1", "1", "256", "0")
    run_cli("add-job", "j2", "1", "256", "0")
    run_cli("allocate", "j1", "n1")
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 2 and st["total_jobs"] == 2 and st["allocated_jobs"] == 1 and st["pending_jobs"] == 1
    assert st["total_resources"]["cpu"] == 6 and st["used_resources"]["cpu"] == 1

def test_special_chars_no_escape():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert "<" in raw and "\\u003c" not in raw.lower()
    assert json.loads(run_cli("get-node", "node<>&").stdout)["id"] == "node<>&"
    run_cli("add-job", "job<>&", "1", "256", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert "<" in raw
    assert json.loads(run_cli("get-job", "job<>&").stdout)["id"] == "job<>&"

def test_unicode_preserved():
    clean_data()
    run_cli("add-node", "node-🌍", "4", "1024", "0")
    assert "🌍" in json.loads(run_cli("get-node", "node-🌍").stdout)["id"]
    assert "🌍" in open(DATA_FILE, "r", encoding="utf-8").read()
    run_cli("add-job", "job-🚀", "1", "256", "0")
    assert "🚀" in json.loads(run_cli("get-job", "job-🚀").stdout)["id"]

def test_large_scale_sorted():
    clean_data()
    for i in range(200):
        run_cli("add-node", f"node-{i:03d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 200
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])

def test_large_history_perf():
    clean_data()
    start = time.time()
    for i in range(300):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    assert time.time() - start < 15
    r = run_cli("list-nodes")
    assert len(json.loads(r.stdout)) == 300
    start = time.time()
    run_cli("list-nodes")
    assert time.time() - start < 2

def test_checksum_and_atomic():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert checksum_valid()
    assert '"checksum"' in read_wrapper_raw()
    # check source contains atomic markers
    found_create = found_rename = found_escape = False
    for root, _, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                try:
                    c = open(os.path.join(root, f)).read()
                    if "CreateTemp" in c:
                        found_create = True
                    if "Rename" in c:
                        found_rename = True
                    if "SetEscapeHTML" in c:
                        found_escape = True
                except:
                    pass
    assert found_create and found_rename and found_escape

def test_corruption_handling():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid json")
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []
    backups = [f for f in os.listdir(os.path.dirname(DATA_FILE)) if ".corrupt." in f]
    assert len(backups) >= 1
    with open(DATA_FILE, "w") as f:
        f.write('{"data": {"nodes":{}, "jobs":{}}, "checksum": "bad"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0
    assert "corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower()

def test_missing_checksum_corruption():
    clean_data()
    with open(DATA_FILE, "w") as f:
        json.dump({"data": {"nodes": {}, "jobs": {}}}, f)
    r = run_cli("list-nodes")
    assert json.loads(r.stdout) == []
    assert len([f for f in os.listdir(os.path.dirname(DATA_FILE)) if ".corrupt." in f]) >= 1

def test_empty_file_handling():
    clean_data()
    open(DATA_FILE, "w").write("")
    r = run_cli("list-nodes")
    assert json.loads(r.stdout) == []
    r = run_cli("list-jobs")
    assert json.loads(r.stdout) == []

def test_missing_file_handling():
    clean_data()
    # file already removed by clean_data, list should return empty not crash
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []

def test_concurrent_allocate_same_node():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")
    def alloc(j):
        run_cli("allocate", f"job{j}", "node1")
    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert len(node["jobs"]) == 20 and node["used"]["cpu"] == 20
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)

def test_concurrent_allocate_diff_nodes():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node{i}", "4", "1024", "0")
        run_cli("add-job", f"job{i}", "1", "256", "0")
    def alloc_pair(i):
        run_cli("allocate", f"job{i}", f"node{i}")
    threads = [threading.Thread(target=alloc_pair, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for i in range(20):
        node = json.loads(run_cli("get-node", f"node{i}").stdout)
        assert len(node["jobs"]) == 1
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)

def test_concurrent_add_node():
    clean_data()
    def add_node(i):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    threads = [threading.Thread(target=add_node, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 20
    assert not os.path.exists(LOCK_FILE)

def test_concurrent_list_during_writes():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")
    stop = False
    def writer():
        for i in range(20):
            run_cli("allocate", f"job{i}", "node1")
    def reader():
        while not stop:
            r = run_cli("list-nodes")
            try:
                json.loads(r.stdout)
            except:
                assert False, "list-nodes returned invalid JSON during concurrent writes"
            time.sleep(0.01)
    t_writer = threading.Thread(target=writer)
    t_reader = threading.Thread(target=reader)
    t_writer.start()
    t_reader.start()
    t_writer.join()
    stop = True
    t_reader.join()

def test_file_lock_cleanup():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists(LOCK_FILE)

def test_stdlib_only():
    result = subprocess.run(["go", "list", "-f", "{{join .Imports \" \"}}", "."], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        for imp in result.stdout.split():
            assert "." not in imp, f"non-stdlib import {imp}"

def test_get_nonexist_fails():
    clean_data()
    assert run_cli("get-node", "noexist").returncode == 2
    assert run_cli("get-job", "noexist").returncode == 2

def test_list_empty():
    clean_data()
    assert json.loads(run_cli("list-nodes").stdout) == []
    assert json.loads(run_cli("list-jobs").stdout) == []

def test_node_id_special_chars():
    clean_data()
    for nid in ["node-dash", "node_underscore", "node.dot", "node:colon"]:
        assert run_cli("add-node", nid, "4", "1024", "0").returncode == 0
    assert len(json.loads(run_cli("list-nodes").stdout)) == 4

def test_persistence_across_restarts():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert "job1" in json.loads(run_cli("get-node", "node1").stdout)["jobs"]
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == "node1"

def test_large_id_10kb():
    clean_data()
    big_id = "n" * 5000
    r = run_cli("add-node", big_id, "4", "1024", "0")
    assert r.returncode == 0
    r = run_cli("get-node", big_id)
    assert r.returncode == 0
    assert json.loads(r.stdout)["id"] == big_id

def test_node_jobs_sorted():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for j in ["jobB", "jobA", "jobC"]:
        run_cli("add-job", j, "1", "256", "0")
        run_cli("allocate", j, "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"])

def test_free_computed():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["free"]["cpu"] == 3 and node["free"]["memory"] == 768 and node["free"]["gpu"] == 1

def test_deallocate_then_reallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-node", "node2", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    r = run_cli("allocate", "job1", "node2")
    assert r.returncode == 0
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == "node2"

def test_remove_node_after_deallocate_all():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("remove-job", "job1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()

def test_status_after_deallocate():
    clean_data()
    run_cli("add-node", "n1", "4", "1024", "0")
    run_cli("add-job", "j1", "1", "256", "0")
    run_cli("allocate", "j1", "n1")
    run_cli("deallocate", "j1")
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 0 and st["pending_jobs"] == 1 and st["used_resources"]["cpu"] == 0

def test_list_pagination_single_file():
    clean_data()
    for i in range(15):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    r = run_cli("list-nodes", "5", "0")
    assert len(json.loads(r.stdout)) == 5
    r = run_cli("list-nodes", "5", "5")
    assert len(json.loads(r.stdout)) == 5
    r = run_cli("list-nodes", "0", "0")
    assert len(json.loads(r.stdout)) == 15
    r = run_cli("list-nodes", "5", "20")
    assert json.loads(r.stdout) == []

def test_schedule_after_deallocate_picks_correct():
    clean_data()
    run_cli("add-node", "nodeA", "1", "256", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("add-job", "job2", "1", "256", "0")
    run_cli("schedule", "job1")  # should pick nodeA first
    run_cli("deallocate", "job1")
    # now nodeA free again, schedule job2 should pick nodeA (first-fit)
    r = run_cli("schedule", "job2")
    assert json.loads(r.stdout)["node_id"] == "nodeA"
