"""
Turn1 moderate 40 tests for cluster management.
"""
import os, json, hashlib, subprocess, time, threading
import pytest

APP = "/app"
BIN = "/app/cluster-manager"
DATA_FILE = "/app/data/cluster.json"
LOCK_FILE = DATA_FILE + ".lock"

GO_ENV = {**os.environ, "GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod", "GOCACHE": "/tmp/gocache", "GOPATH": "/tmp/gopath"}

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
    try:
        for fname in os.listdir(d):
            if ".corrupt." in fname or fname.endswith(".lock"):
                try:
                    os.remove(os.path.join(d, fname))
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
    if "data" not in obj or "checksum" not in obj or not obj["checksum"]:
        return False
    canonical = json.dumps(obj["data"], sort_keys=True, separators=(',', ':'))
    return obj["checksum"] == hashlib.md5(canonical.encode()).hexdigest()

def test_help_contains_keywords():
    clean_data()
    r = run_cli()
    assert r.returncode == 0
    out = r.stdout.lower()
    for kw in ["add-node", "remove-node", "list-nodes", "get-node", "add-job", "remove-job", "list-jobs", "get-job", "allocate", "deallocate", "schedule", "status", "data", "checksum"]:
        assert kw in out

def test_unknown_command_exit2():
    clean_data()
    assert run_cli("unknown-cmd").returncode == 2

def test_missing_args_exit2():
    clean_data()
    assert run_cli("add-node", "node1").returncode == 2
    assert run_cli("add-job", "job1").returncode == 2
    assert run_cli("allocate", "job1").returncode == 2

def test_empty_id_exit2():
    clean_data()
    assert run_cli("add-node", "", "4", "1024", "1").returncode == 2
    assert run_cli("get-node", "").returncode == 2
    assert run_cli("add-job", "", "1", "256", "0").returncode == 2

def test_invalid_resources_exit2():
    clean_data()
    assert run_cli("add-node", "n1", "0", "1024", "1").returncode == 2
    assert run_cli("add-node", "n1", "4", "0", "1").returncode == 2
    assert run_cli("add-node", "n1", "4", "1024", "-1").returncode == 2
    assert run_cli("add-node", "n1", "abc", "1024", "1").returncode == 2
    assert run_cli("add-job", "j1", "0", "256", "0").returncode == 2

def test_add_node_list_get():
    clean_data()
    assert run_cli("add-node", "nodeA", "4", "1024", "1").returncode == 0
    assert run_cli("add-node", "nodeB", "8", "2048", "2").returncode == 0
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 2
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    node = json.loads(run_cli("get-node", "nodeA").stdout)
    assert node["id"] == "nodeA" and node["free"]["cpu"] == 4

def test_add_node_idempotent():
    clean_data()
    run_cli("add-node", "nodeX", "4", "1024", "1")
    assert run_cli("add-node", "nodeX", "8", "2048", "2").returncode == 0
    assert json.loads(run_cli("get-node", "nodeX").stdout)["total"]["cpu"] == 4

def test_remove_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-nodes").stdout) == []
    assert "false" in run_cli("remove-node", "noexist").stdout.lower()

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
    assert len(arr) == 2 and [j["id"] for j in arr] == sorted([j["id"] for j in arr])
    job = json.loads(run_cli("get-job", "jobA").stdout)
    assert job["status"] == "pending" and job["node_id"] == ""

def test_remove_job_deallocates():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("remove-job", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("get-node", "node1").stdout)["jobs"] == []
    assert "false" in run_cli("remove-job", "job1").stdout.lower()

def test_allocate_success():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 0
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == "node1"
    assert json.loads(run_cli("get-node", "node1").stdout)["used"]["cpu"] == 1

def test_allocate_insufficient():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "job1", "4", "1024", "0")
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()

def test_allocate_idempotent_and_diff_fail():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-node", "node2", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("allocate", "job1", "node1").returncode == 0
    assert run_cli("allocate", "job1", "node2").returncode == 2

def test_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("deallocate", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == ""
    assert "false" in run_cli("deallocate", "job1").stdout.lower()
    assert run_cli("deallocate", "nojob").returncode == 2

def test_schedule_first_fit():
    clean_data()
    run_cli("add-node", "nodeA", "2", "512", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    assert json.loads(run_cli("schedule", "job1").stdout)["node_id"] == "nodeA"
    run_cli("add-job", "job2", "2", "512", "0")
    assert json.loads(run_cli("schedule", "job2").stdout)["node_id"] == "nodeB"

def test_schedule_no_fit():
    clean_data()
    run_cli("add-node", "node1", "1", "256", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    r = run_cli("schedule", "job1")
    assert r.returncode == 1 and "no fit" in r.stderr.lower() and r.stdout.strip() == ""

def test_status():
    clean_data()
    run_cli("add-node", "n1", "4", "1024", "1")
    run_cli("add-node", "n2", "2", "512", "0")
    run_cli("add-job", "j1", "1", "256", "0")
    run_cli("add-job", "j2", "1", "256", "0")
    run_cli("allocate", "j1", "n1")
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 2 and st["total_jobs"] == 2 and st["allocated_jobs"] == 1

def test_special_chars_no_escape():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    assert "<" in open(DATA_FILE).read()
    assert json.loads(run_cli("get-node", "node<>&").stdout)["id"] == "node<>&"
    run_cli("add-job", "job<>&", "1", "256", "0")
    assert "<" in open(DATA_FILE).read()

def test_unicode_preserved():
    clean_data()
    run_cli("add-node", "node-🌍", "4", "1024", "0")
    assert "🌍" in json.loads(run_cli("get-node", "node-🌍").stdout)["id"]
    assert "🌍" in open(DATA_FILE, encoding="utf-8").read()

def test_large_scale_sorted():
    clean_data()
    for i in range(100):
        run_cli("add-node", f"node-{i:03d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 100
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])

def test_checksum_and_atomic():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert checksum_valid()
    assert '"checksum"' in read_wrapper_raw()
    found = {"CreateTemp": False, "Rename": False, "SetEscapeHTML": False}
    for root, _, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                try:
                    c = open(os.path.join(root, f)).read()
                    for k in found:
                        if k in c:
                            found[k] = True
                except:
                    pass
    assert all(found.values())

def test_corruption_handling():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid json")
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []
    assert len([f for f in os.listdir(os.path.dirname(DATA_FILE)) if ".corrupt." in f]) >= 1
    with open(DATA_FILE, "w") as f:
        f.write('{"data": {"nodes":{}, "jobs":{}}, "checksum": "bad"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0 and ("corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower())

def test_concurrent_allocate_same_node():
    clean_data()
    run_cli("add-node", "node1", "50", "50000", "5")
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
    assert len(node["jobs"]) == 20
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)

def test_file_lock_cleanup():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists(LOCK_FILE)

def test_stdlib_only():
    result = subprocess.run(["go", "list", "-f", "{{join .Imports \" \"}}", "."], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        for imp in result.stdout.split():
            assert "." not in imp

def test_get_nonexist_fails():
    clean_data()
    assert run_cli("get-node", "noexist").returncode == 2
    assert run_cli("get-job", "noexist").returncode == 2

def test_list_empty():
    clean_data()
    assert json.loads(run_cli("list-nodes").stdout) == []
    assert json.loads(run_cli("list-jobs").stdout) == []

def test_persistence_across_restarts():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert "job1" in json.loads(run_cli("get-node", "node1").stdout)["jobs"]

def test_node_jobs_sorted_and_free():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for j in ["jobB", "jobA", "jobC"]:
        run_cli("add-job", j, "1", "256", "0")
        run_cli("allocate", j, "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"])
    assert node["free"]["cpu"] == 7
