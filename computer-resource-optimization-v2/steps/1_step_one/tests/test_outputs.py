"""
Turn1 moderate 30 tests for cluster management (core: nodes, jobs, allocation, persistence, checksum, concurrency).
"""

import os, json, hashlib, subprocess, time, threading
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
    assert r.returncode == 0, f"build failed\n{r.stdout}\n{r.stderr}"
    assert os.path.exists(BIN)
    yield


def run_cli(*args, data_path=DATA_FILE, timeout=15):
    cmd = [BIN, "--data", data_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def clean_data(path=DATA_FILE):
    for fp in [
        path,
        path + ".lock",
        "/app/config.json",
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
        "/app/data/global.lock",
    ]:
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
    canonical = json.dumps(obj["data"], sort_keys=True, separators=(",", ":"))
    return obj["checksum"] == hashlib.md5(canonical.encode()).hexdigest()


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
    assert (
        st["total_nodes"] == 2 and st["total_jobs"] == 2 and st["allocated_jobs"] == 1
    )


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
    assert (
        len([f for f in os.listdir(os.path.dirname(DATA_FILE)) if ".corrupt." in f])
        >= 1
    )
    with open(DATA_FILE, "w") as f:
        f.write('{"data": {"nodes":{}, "jobs":{}}, "checksum": "bad"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0 and (
        "corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower()
    )


def test_file_lock_cleanup():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists(LOCK_FILE)


def test_stdlib_only():
    result = subprocess.run(
        ["go", "list", "-f", '{{join .Imports " "}}', "."],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=10,
    )
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


def test_allocate_nonexist_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert run_cli("allocate", "nojob", "node1").returncode == 2
    run_cli("add-job", "job1", "1", "256", "0")
    assert run_cli("allocate", "job1", "nonode").returncode == 2


def test_empty_file_handling():
    clean_data()
    open(DATA_FILE, "w").write("")
    assert json.loads(run_cli("list-nodes").stdout) == []
    assert json.loads(run_cli("list-jobs").stdout) == []


def test_missing_file_handling():
    clean_data()
    assert json.loads(run_cli("list-nodes").stdout) == []

# ---------- New harder discriminators for Step1 (was 30 too easy) ----------

def test_whitespace_file_empty_store():
    clean_data()
    open(DATA_FILE, "w").write("   \n\t  \n")
    assert json.loads(run_cli("list-nodes").stdout) == []
    assert json.loads(run_cli("list-jobs").stdout) == []
    assert run_cli("list-nodes").returncode == 0


def test_missing_checksum_corruption():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    # Write wrapper missing checksum
    with open(DATA_FILE, "w") as f:
        f.write('{"data": {"nodes":{}, "jobs":{}}}')
    r = run_cli("list-nodes")
    assert r.returncode == 0
    assert json.loads(r.stdout) == []
    assert any(".corrupt." in fn for fn in os.listdir(os.path.dirname(DATA_FILE)))
    assert "corrupt" in r.stderr.lower() or "checksum" in r.stderr.lower()


def test_bad_checksum_corruption():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write('{"data": {"nodes":{}, "jobs":{}}, "checksum": "badchecksum"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0
    assert json.loads(r.stdout) == []


def test_jobs_field_empty_array_not_null_after_add_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # Go nil slice marshals as null, must be [] - check raw contains '"jobs":[]' not '"jobs":null'
    assert '"jobs":[]' in raw or '"jobs": []' in raw, f"jobs field should be [] not null, got {raw[:500]}"
    assert "null" not in raw or raw.count("null") == 0 or '"jobs":null' not in raw.replace(" ", "")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == []


def test_jobs_field_empty_array_not_null_after_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # After deallocate, jobs should be [] not null
    assert '"jobs":[]' in raw or '"jobs": []' in raw
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == []


def test_jobs_field_empty_array_not_null_after_remove_job():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("remove-job", "job1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == []
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert '"jobs":[]' in raw or '"jobs": []' in raw


def test_add_job_idempotent():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "jobX", "1", "256", "0")
    run_cli("allocate", "jobX", "node1")
    # Re-add same job with different resources should be no-op, keep allocation
    r = run_cli("add-job", "jobX", "8", "2048", "1")
    assert r.returncode == 0
    job = json.loads(run_cli("get-job", "jobX").stdout)
    assert job["required"]["cpu"] == 1, "idempotent should preserve old resources"
    assert job["node_id"] == "node1", "idempotent should preserve allocation"
    assert job["status"] == "running"


def test_add_node_concurrent_20():
    clean_data()
    def add_node(i):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    threads = [threading.Thread(target=add_node, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 20
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    assert not os.path.exists(LOCK_FILE)


def test_allocate_concurrent_same_node_20():
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
    assert len(node["jobs"]) == 20, f"should preserve all 20 jobs, got {len(node['jobs'])}"
    assert node["used"]["cpu"] == 20
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)
    # File must remain valid JSON during concurrent (no partial writes)
    raw = open(DATA_FILE).read()
    json.loads(raw)  # should not throw


def test_allocate_concurrent_diff_nodes_20():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    def alloc(i):
        run_cli("allocate", f"job-{i:02d}", f"node-{i:02d}")
    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 20
    assert st["total_nodes"] == 20
    assert not os.path.exists(LOCK_FILE)


def test_pagination_offset_then_limit_order():
    clean_data()
    for i in range(5):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # offset 1 limit 2 should return nodes 1,2 (sorted asc) not 0,1
    arr = json.loads(run_cli("list-nodes", "2", "1").stdout)
    assert [n["id"] for n in arr] == ["node-1", "node-2"]
    arr2 = json.loads(run_cli("list-jobs", "2", "1").stdout)
    # jobs empty -> [] regardless
    assert arr2 == []


def test_pagination_invalid_limit_offset():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert run_cli("list-nodes", "-1", "0").returncode == 2
    assert run_cli("list-nodes", "abc", "0").returncode == 2
    assert run_cli("list-nodes", "0", "-1").returncode == 2
    assert run_cli("list-nodes", "0", "abc").returncode == 2
    assert run_cli("list-jobs", "-1", "0").returncode == 2
    assert run_cli("list-jobs", "abc", "0").returncode == 2


def test_large_scale_1000_nodes_perf():
    clean_data()
    start = time.time()
    for i in range(500):
        run_cli("add-node", f"node-perf-{i:04d}", "4", "1024", "0")
    elapsed = time.time() - start
    # 500 nodes add should be reasonable, but list 1000+ perf <2s
    t0 = time.time()
    arr = json.loads(run_cli("list-nodes", "0", "0").stdout)
    t1 = time.time() - t0
    assert len(arr) == 500
    assert t1 < 2.0, f"list-nodes 500 took {t1}s >2s, likely O(n^2)"


def test_special_chars_job_no_escape():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job<>&", "1", "256", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert "<" in raw, "SetEscapeHTML(false) required for <>&"
    job = json.loads(run_cli("get-job", "job<>&").stdout)
    assert job["id"] == "job<>&"


def test_large_id_10kb():
    clean_data()
    large_id = "n" + "a" * 10240
    # Should handle large IDs (10KB) – either accept or fail gracefully, but not crash
    r = run_cli("add-node", large_id, "4", "1024", "0")
    # Spec says supports large IDs (10KB), so should succeed
    assert r.returncode == 0
    node = json.loads(run_cli("get-node", large_id).stdout)
    assert node["id"] == large_id


def test_status_resources_sum():
    clean_data()
    run_cli("add-node", "n1", "4", "1024", "1")
    run_cli("add-node", "n2", "2", "512", "0")
    run_cli("add-job", "j1", "1", "256", "0")
    run_cli("add-job", "j2", "1", "256", "0")
    run_cli("allocate", "j1", "n1")
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 2
    assert st["total_jobs"] == 2
    assert st["allocated_jobs"] == 1
    assert st["pending_jobs"] == 1
    assert st["total_resources"]["cpu"] == 6
    assert st["used_resources"]["cpu"] == 1
    assert st["used_resources"]["memory"] == 256


def test_schedule_first_fit_not_best_fit():
    clean_data()
    # First-fit: sorted IDs asc, first that fits wins, even if wasteful
    # nodeA id smaller but more wasteful (10 CPU) vs nodeB (4 CPU) both fit job 2 CPU
    # first-fit should pick nodeA (lexicographically smallest that fits), not best-fit
    run_cli("add-node", "nodeA", "10", "10240", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    out = json.loads(run_cli("schedule", "job1").stdout)
    assert out["node_id"] == "nodeA", f"Step1 should be first-fit, expected nodeA got {out['node_id']}"


def test_atomic_write_no_tmp_leftover():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    run_cli("remove-job", "job1")
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files), f"tmp leftover {files}"


def test_remove_job_deallocates_and_preserves_node():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert json.loads(run_cli("get-node", "node1").stdout)["used"]["cpu"] == 1
    run_cli("remove-job", "job1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["used"]["cpu"] == 0
    assert node["jobs"] == []
    assert node["free"]["cpu"] == 4

# ---------- Additional hardening for Step1 (still too easy -> push to 64) ----------

def test_empty_id_with_spaces_exit2():
    clean_data()
    assert run_cli("add-node", "   ", "4", "1024", "0").returncode == 2
    assert run_cli("add-job", "  ", "1", "256", "0").returncode == 2
    assert run_cli("get-node", "   ").returncode == 2


def test_invalid_resources_float_string():
    clean_data()
    assert run_cli("add-node", "node1", "4.0", "1024", "0").returncode == 2
    assert run_cli("add-node", "node1", "4", "1024.5", "0").returncode == 2
    assert run_cli("add-job", "job1", "1.5", "256", "0").returncode == 2


def test_remove_node_false_not_exist():
    clean_data()
    r = run_cli("remove-node", "nope")
    assert r.returncode == 0 and "false" in r.stdout.lower()


def test_remove_job_false_not_exist():
    clean_data()
    r = run_cli("remove-job", "nope")
    assert r.returncode == 0 and "false" in r.stdout.lower()


def test_deallocate_false_and_nonexist():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("deallocate", "job1")
    assert r.returncode == 0 and "false" in r.stdout.lower()
    assert run_cli("deallocate", "nojob").returncode == 2


def test_allocate_already_allocated_different_node_exit2():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-node", "node2", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("allocate", "job1", "node2")
    assert r.returncode == 2


def test_allocate_already_allocated_same_node_idempotent():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    assert run_cli("allocate", "job1", "node1").returncode == 0
    r = run_cli("allocate", "job1", "node1")
    assert r.returncode == 0
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"].count("job1") == 1


def test_node_jobs_sorted():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["jobZ", "jobA", "jobM"]:
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"])


def test_used_free_correct_after_allocate_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    n = json.loads(run_cli("get-node", "node1").stdout)
    assert n["used"]["cpu"] == 1 and n["free"]["cpu"] == 3
    run_cli("deallocate", "job1")
    n2 = json.loads(run_cli("get-node", "node1").stdout)
    assert n2["used"]["cpu"] == 0 and n2["free"]["cpu"] == 4
    assert n2["jobs"] == []


def test_file_lock_cleaned_after_failure():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "big", "10", "10000", "0")
    r = run_cli("allocate", "big", "node1")
    assert r.returncode == 2
    assert not os.path.exists(LOCK_FILE)
    assert not os.path.exists("/app/data/global.lock")


def test_corruption_backup_nanosec_integer():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid")
    run_cli("list-nodes")
    import re
    files = os.listdir(os.path.dirname(DATA_FILE))
    corrupt = [f for f in files if ".corrupt." in f]
    assert len(corrupt) >= 1
    # suffix must be integer nanosec
    for fn in corrupt:
        m = re.search(r"\.corrupt\.(\d+)$", fn)
        assert m, f"corrupt suffix should be integer nanosec, got {fn}"


def test_corruption_file_null_and_array():
    clean_data()
    with open(DATA_FILE, "w") as f:
        f.write("null")
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []
    with open(DATA_FILE, "w") as f:
        f.write("[]")
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []


def test_list_nodes_and_jobs_sorted():
    clean_data()
    for nid in ["nodeZ", "nodeA", "nodeM"]:
        run_cli("add-node", nid, "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert [n["id"] for n in arr] == ["nodeA", "nodeM", "nodeZ"]
    for jid in ["jobZ", "jobA", "jobM"]:
        run_cli("add-job", jid, "1", "256", "0")
    arr2 = json.loads(run_cli("list-jobs").stdout)
    assert [j["id"] for j in arr2] == ["jobA", "jobM", "jobZ"]


def test_list_with_special_chars_id_dash_underscore_dot_colon():
    clean_data()
    for nid in ["node-a", "node_b", "node.c", "node:d"]:
        run_cli("add-node", nid, "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 4
    for nid in ["node-a", "node_b", "node.c", "node:d"]:
        assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_unicode_job_id_preserved():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job-🌍🚀😀", "1", "256", "0")
    job = json.loads(run_cli("get-job", "job-🌍🚀😀").stdout)
    assert "🌍" in job["id"]
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "🌍" in raw


def test_checksum_uses_setescapehtml_false_raw():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    raw = open(DATA_FILE, encoding="utf-8").read()
    # Must contain raw "<" not escaped \u003c
    assert "<" in raw
    assert "\\u003c" not in raw.lower()
    assert checksum_valid()


def test_concurrent_list_while_allocating():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")
    def alloc_loop():
        for i in range(20):
            run_cli("allocate", f"job{i}", "node1")
    def list_loop():
        for _ in range(20):
            r = run_cli("list-nodes")
            assert r.returncode == 0
            json.loads(r.stdout)
    t1 = threading.Thread(target=alloc_loop)
    t2 = threading.Thread(target=list_loop)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not os.path.exists(LOCK_FILE)

# ---------- Further hardening Step1: 66->80 (still too easy per feedback) ----------

def test_concurrent_add_node_same_id_20():
    clean_data()
    def add_same(i):
        # all same ID but different resources – idempotent must preserve first, not crash
        run_cli("add-node", "node-same", f"{4+i}", f"{1024+i}", "0")
    threads = [threading.Thread(target=add_same, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 1, f"same ID concurrent should result in 1 node, got {len(arr)}"
    assert arr[0]["id"] == "node-same"
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_concurrent_add_job_same_id_20():
    clean_data()
    def add_same(i):
        run_cli("add-job", "job-same", f"{1+i}", f"{256+i}", "0")
    threads = [threading.Thread(target=add_same, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arr = json.loads(run_cli("list-jobs").stdout)
    assert len(arr) == 1
    assert arr[0]["id"] == "job-same"
    assert checksum_valid()


def test_concurrent_deallocate_20():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")
        run_cli("allocate", f"job{i}", "node1")
    def dealloc(j):
        run_cli("deallocate", f"job{j}")
    threads = [threading.Thread(target=dealloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["used"]["cpu"] == 0
    assert node["jobs"] == []
    assert checksum_valid()


def test_allocate_insufficient_gpu():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "jobGPU", "1", "256", "1")
    r = run_cli("allocate", "jobGPU", "node1")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()


def test_deallocate_preserves_other_jobs():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "2")
    for jid in ["jobA", "jobB", "jobC"]:
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, "node1")
    run_cli("deallocate", "jobB")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert set(node["jobs"]) == {"jobA", "jobC"}
    assert node["used"]["cpu"] == 2


def test_checksum_valid_after_each_op():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert checksum_valid()
    run_cli("add-job", "job1", "1", "256", "0")
    assert checksum_valid()
    run_cli("allocate", "job1", "node1")
    assert checksum_valid()
    run_cli("deallocate", "job1")
    assert checksum_valid()
    run_cli("remove-job", "job1")
    assert checksum_valid()
    run_cli("remove-node", "node1")
    assert checksum_valid()


def test_list_nodes_limit_0_vs_omit_all():
    clean_data()
    for i in range(5):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    arr_omit = json.loads(run_cli("list-nodes").stdout)
    arr_zero = json.loads(run_cli("list-nodes", "0", "0").stdout)
    assert len(arr_omit) == 5 and len(arr_zero) == 5
    assert [n["id"] for n in arr_omit] == [n["id"] for n in arr_zero]


def test_list_offset_beyond_empty():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert json.loads(run_cli("list-nodes", "0", "10").stdout) == []
    assert json.loads(run_cli("list-nodes", "10", "10").stdout) == []
    assert json.loads(run_cli("list-jobs", "0", "10").stdout) == []


def test_large_scale_1000_nodes_sorted_perf():
    clean_data()
    start = time.time()
    for i in range(800):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    add_elapsed = time.time() - start
    t0 = time.time()
    arr = json.loads(run_cli("list-nodes").stdout)
    elapsed = time.time() - t0
    assert len(arr) == 800
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    assert elapsed < 1.5, f"list-nodes 800 took {elapsed}s >1.5s, O(n^2) likely"
    # also test with limit
    t1 = time.time()
    arr2 = json.loads(run_cli("list-nodes", "100", "100").stdout)
    elapsed2 = time.time() - t1
    assert len(arr2) == 100
    assert elapsed2 < 1.5


def test_large_scale_1000_jobs_sorted():
    clean_data()
    run_cli("add-node", "node1", "2000", "2000000", "0")
    for i in range(500):
        run_cli("add-job", f"job-{i:04d}", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs").stdout)
    assert len(arr) == 500
    assert [j["id"] for j in arr] == sorted([j["id"] for j in arr])


def test_node_jobs_sorted_after_many_allocations():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for jid in ["jobZ", "jobA", "jobM", "jobB", "jobY"]:
        run_cli("add-job", jid, "1", "256", "0")
    # allocate in non-sorted order
    for jid in ["jobZ", "jobM", "jobA", "jobY", "jobB"]:
        run_cli("allocate", jid, "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"])


def test_remove_node_after_deallocate_succeeds():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("remove-node", "node1").returncode == 2
    run_cli("deallocate", "job1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-nodes").stdout) == []


def test_file_lock_content_is_pid_or_empty():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists(LOCK_FILE)
    # After successful op, lock must not remain even if binary wrote pid inside during lock
    for fname in os.listdir(os.path.dirname(DATA_FILE)):
        assert not fname.endswith(".lock"), f"lock leftover {fname}"


def test_concurrent_list_nodes_100_times_no_crash():
    clean_data()
    for i in range(50):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    def list_many():
        for _ in range(30):
            r = run_cli("list-nodes")
            assert r.returncode == 0
            json.loads(r.stdout)
    threads = [threading.Thread(target=list_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

# ---------- Extra 80->96 (still too easy) ----------

def test_timestamp_integer_required():
    clean_data()
    assert run_cli("add-node", "nodeFloat", "4.0", "1024", "0").returncode == 2
    assert run_cli("add-node", "nodeFloat2", "4", "1024.0", "0").returncode == 2
    assert run_cli("add-job", "jobFloat", "1.0", "256", "0").returncode == 2
    assert run_cli("add-job", "jobFloat2", "1", "256.5", "0").returncode == 2
    assert run_cli("add-node", "nodeInt", "4", "1024", "0").returncode == 0


def test_allocate_insufficient_memory_and_gpu():
    clean_data()
    run_cli("add-node", "node1", "4", "512", "0")
    run_cli("add-job", "jobMem", "1", "1024", "0")
    r = run_cli("allocate", "jobMem", "node1")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()
    run_cli("add-node", "nodeGPU", "4", "1024", "0")
    run_cli("add-job", "jobGPU", "1", "256", "1")
    r2 = run_cli("allocate", "jobGPU", "nodeGPU")
    assert r2.returncode == 2 and "insufficient" in r2.stderr.lower()


def test_schedule_first_fit_fragmented():
    clean_data()
    run_cli("add-node", "nodeA", "4", "1024", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-node", "nodeC", "4", "1024", "0")
    run_cli("add-job", "jobA1", "2", "256", "0")
    run_cli("allocate", "jobA1", "nodeA")
    run_cli("add-job", "jobB1", "3", "256", "0")
    run_cli("allocate", "jobB1", "nodeB")
    run_cli("add-job", "job2", "2", "256", "0")
    out = json.loads(run_cli("schedule", "job2").stdout)
    assert out["node_id"] == "nodeA", f"first-fit should pick nodeA not {out['node_id']}"


def test_list_nodes_offset_beyond_and_limit():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    assert json.loads(run_cli("list-nodes", "0", "100").stdout) == []
    assert json.loads(run_cli("list-nodes", "100", "100").stdout) == []


def test_concurrent_add_node_and_job_interleaved():
    clean_data()
    def worker(i):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 20
    assert len(json.loads(run_cli("list-jobs").stdout)) == 20
    assert checksum_valid()


def test_concurrent_remove_job_20():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "10")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")
        run_cli("allocate", f"job{i}", "node1")
    def rem(j):
        run_cli("remove-job", f"job{j}")
    threads = [threading.Thread(target=rem, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 0 and st["total_jobs"] == 0
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == [] and node["used"]["cpu"] == 0


def test_corruption_missing_data_field():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write('{"checksum": "abc"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []
    assert any(".corrupt." in fn for fn in os.listdir(os.path.dirname(DATA_FILE)))


def test_corruption_data_not_object():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write('{"data": [], "checksum": "dummy"}')
    r = run_cli("list-nodes")
    assert r.returncode == 0 and json.loads(r.stdout) == []


def test_file_lock_retry_success():
    clean_data()
    lock_path = DATA_FILE + ".lock"
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    try:
        os.remove(lock_path)
    except:
        pass
    with open(lock_path, "w") as f:
        f.write("locked")
    def remove_lock():
        time.sleep(0.1)
        try:
            os.remove(lock_path)
        except:
            pass
    t = threading.Thread(target=remove_lock)
    t.start()
    r = run_cli("add-node", "nodeRetry", "4", "1024", "0")
    t.join()
    assert r.returncode == 0
    assert not os.path.exists(lock_path)


def test_empty_jobs_after_remove_all():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(5):
        run_cli("add-job", f"job{i}", "1", "256", "0")
        run_cli("allocate", f"job{i}", "node1")
    for i in range(5):
        run_cli("remove-job", f"job{i}")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == []
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert '"jobs":[]' in raw or '"jobs": []' in raw
    assert json.loads(run_cli("list-jobs").stdout) == []


def test_status_pending_vs_allocated():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("add-job", "job2", "1", "256", "0")
    st1 = json.loads(run_cli("status").stdout)
    assert st1["allocated_jobs"] == 0 and st1["pending_jobs"] == 2
    run_cli("allocate", "job1", "node1")
    st2 = json.loads(run_cli("status").stdout)
    assert st2["allocated_jobs"] == 1 and st2["pending_jobs"] == 1


def test_add_node_zero_gpu_valid():
    clean_data()
    assert run_cli("add-node", "nodeZeroGPU", "4", "1024", "0").returncode == 0
    assert run_cli("add-node", "nodeNegGPU", "4", "1024", "-1").returncode == 2


def test_add_job_zero_gpu_valid():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    assert run_cli("add-job", "jobZeroGPU", "1", "256", "0").returncode == 0
    assert run_cli("add-job", "jobNegGPU", "1", "256", "-1").returncode == 2


def test_allocate_job_special_chars_and_unicode():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    run_cli("add-job", "job-🌍", "1", "256", "0")
    r = run_cli("allocate", "job-🌍", "node<>&")
    assert r.returncode == 0
    job = json.loads(run_cli("get-job", "job-🌍").stdout)
    assert job["node_id"] == "node<>&"
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "🌍" in raw and "<" in raw


def test_concurrent_remove_node_while_allocating():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(10):
        run_cli("add-job", f"job{i}", "1", "256", "0")
    def alloc(i):
        run_cli("allocate", f"job{i}", "node1")
    def remove():
        time.sleep(0.05)
        r = run_cli("remove-node", "node1")
        assert r.returncode in (0, 2)
    threads = []
    for i in range(10):
        threads.append(threading.Thread(target=alloc, args=(i,)))
    threads.append(threading.Thread(target=remove))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()


def test_list_jobs_with_limit_and_offset():
    clean_data()
    for i in range(10):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs", "3", "2").stdout)
    assert [j["id"] for j in arr] == ["job-02", "job-03", "job-04"]
    arr2 = json.loads(run_cli("list-jobs", "0", "0").stdout)
    assert len(arr2) == 10
    arr3 = json.loads(run_cli("list-jobs").stdout)
    assert len(arr3) == 10

# ---------- Extreme: 96->110 (still too easy) ----------

def test_allocate_exact_fit():
    clean_data()
    run_cli("add-node", "nodeExact", "2", "512", "1")
    run_cli("add-job", "jobExact", "2", "512", "1")
    r = run_cli("allocate", "jobExact", "nodeExact")
    assert r.returncode == 0
    node = json.loads(run_cli("get-node", "nodeExact").stdout)
    assert node["free"]["cpu"] == 0 and node["free"]["memory"] == 0 and node["free"]["gpu"] == 0
    run_cli("add-job", "jobNoFit", "1", "256", "0")
    r2 = run_cli("allocate", "jobNoFit", "nodeExact")
    assert r2.returncode == 2 and "insufficient" in r2.stderr.lower()


def test_list_nodes_zero_padded_limit_offset():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # "00" and "01" should be parsed as 0 and 1
    arr = json.loads(run_cli("list-nodes", "00", "00").stdout)
    assert len(arr) == 3
    arr2 = json.loads(run_cli("list-nodes", "01", "01").stdout)
    assert len(arr2) == 1 and arr2[0]["id"] == "node-1"


def test_corruption_backup_contains_original():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    invalid_content = "{ not valid json at all"
    with open(DATA_FILE, "w") as f:
        f.write(invalid_content)
    run_cli("list-nodes")
    files = os.listdir(os.path.dirname(DATA_FILE))
    corrupt = [fn for fn in files if ".corrupt." in fn]
    assert len(corrupt) >= 1
    # At least one backup should contain original invalid content
    found = False
    for fn in corrupt:
        try:
            if invalid_content in open(os.path.join(os.path.dirname(DATA_FILE), fn), "r", encoding="utf-8", errors="ignore").read():
                found = True
                break
        except:
            pass
    assert found, "backup should contain original invalid content"


def test_status_after_remove_all():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("remove-job", "job1")
    run_cli("remove-node", "node1")
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 0 and st["total_jobs"] == 0 and st["allocated_jobs"] == 0 and st["pending_jobs"] == 0
    assert st["total_resources"]["cpu"] == 0 and st["used_resources"]["cpu"] == 0


def test_concurrent_allocate_and_deallocate_interleaved():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "10")
    for i in range(30):
        run_cli("add-job", f"job{i}", "1", "100", "0")
    def alloc_dealloc_loop(start):
        for i in range(start, start + 10):
            run_cli("allocate", f"job{i}", "node1")
            time.sleep(0.01)
            run_cli("deallocate", f"job{i}")
    threads = [threading.Thread(target=alloc_dealloc_loop, args=(i * 10,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_list_jobs_pagination_with_allocations():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(20):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
        if i % 2 == 0:
            run_cli("allocate", f"job-{i:02d}", "node1")
    arr = json.loads(run_cli("list-jobs", "5", "5").stdout)
    assert len(arr) == 5
    assert [j["id"] for j in arr] == sorted([j["id"] for j in arr])


def test_add_node_id_case_sensitive():
    clean_data()
    run_cli("add-node", "NodeA", "4", "1024", "0")
    run_cli("add-node", "nodeA", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 2
    assert set([n["id"] for n in arr]) == {"NodeA", "nodeA"}


def test_file_lock_cleaned_even_after_success_many_ops():
    clean_data()
    for i in range(50):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
        run_cli("allocate", f"job-{i:02d}", f"node-{i:02d}")
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(f.endswith(".lock") for f in files)


def test_jobs_field_sorted_after_deallocate_reallocate():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["jobC", "jobA", "jobB"]:
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, "node1")
    run_cli("deallocate", "jobB")
    run_cli("allocate", "jobB", "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"])


def test_large_scale_1000_nodes_and_jobs_perf():
    clean_data()
    for i in range(600):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    t0 = time.time()
    arr = json.loads(run_cli("list-nodes", "100", "100").stdout)
    elapsed = time.time() - t0
    assert len(arr) == 100
    assert elapsed < 1.5
    for i in range(300):
        run_cli("add-job", f"job-{i:04d}", "1", "256", "0")
    t1 = time.time()
    arr2 = json.loads(run_cli("list-jobs", "100", "100").stdout)
    elapsed2 = time.time() - t1
    assert len(arr2) == 100
    assert elapsed2 < 1.5


def test_allocate_with_special_chars_ids():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    run_cli("add-job", "job<>&", "1", "256", "0")
    r = run_cli("allocate", "job<>&", "node<>&")
    assert r.returncode == 0
    node = json.loads(run_cli("get-node", "node<>&").stdout)
    assert "job<>&" in node["jobs"]


def test_remove_node_has_jobs_even_after_failed_allocate():
    clean_data()
    run_cli("add-node", "node1", "1", "256", "0")
    run_cli("add-job", "jobBig", "10", "10000", "0")
    r = run_cli("allocate", "jobBig", "node1")
    assert r.returncode == 2
    # Node has no jobs, so remove should succeed (not fail due to failed allocate)
    r2 = run_cli("remove-node", "node1")
    assert r2.returncode == 0 and "true" in r2.stdout.lower()
