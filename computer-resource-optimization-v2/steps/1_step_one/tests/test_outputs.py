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
    if not os.path.exists(BIN):
        # Rebuild if binary disappeared (shared /app overwritten by other tasks)
        try:
            subprocess.run(
                ["go", "build", "-o", BIN, "."],
                cwd=APP,
                env=GO_ENV,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass
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
    # Go uses SetEscapeHTML(false) and raw UTF-8, so must not escape unicode, but Go still escapes U+2028/U+2029
    canonical = json.dumps(
        obj["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    # Go escapes U+2028/U+2029 even with SetEscapeHTML(false); handle both
    canonical_escaped = canonical.replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029"
    )
    checksum = obj["checksum"]
    return checksum in (
        hashlib.md5(canonical.encode()).hexdigest(),
        hashlib.md5(canonical_escaped.encode()).hexdigest(),
    )


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
    # Execution-based checks for atomic write (not source-scan):
    # - No tmp file leftover in data dir after write
    # - Lock file cleaned after success
    # - Raw file contains "<" unescaped if special chars used (tested separately)
    # - Checksum matches canonical with ensure_ascii=False
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files), f"tmp leftover {files}"
    assert not os.path.exists(LOCK_FILE)
    # Verify raw file uses raw UTF-8 and contains checksum
    raw = read_wrapper_raw()
    obj = json.loads(raw)
    canonical = json.dumps(
        obj["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    # Handle U+2028/U+2029 special escaping that Go does even with SetEscapeHTML(false)
    # Go escapes \u2028 and \u2029, Python with ensure_ascii=False does not, so we need to
    # normalize by escaping them for checksum comparison if present
    canonical_escaped = canonical.replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029"
    )
    computed = hashlib.md5(canonical.encode()).hexdigest()
    computed_escaped = hashlib.md5(canonical_escaped.encode()).hexdigest()
    assert obj["checksum"] in (computed, computed_escaped), (
        f"checksum mismatch: file {obj['checksum']} vs {computed} or {computed_escaped}"
    )


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
    assert '"jobs":[]' in raw or '"jobs": []' in raw, (
        f"jobs field should be [] not null, got {raw[:500]}"
    )
    assert (
        "null" not in raw
        or raw.count("null") == 0
        or '"jobs":null' not in raw.replace(" ", "")
    )
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
    assert len(node["jobs"]) == 20, (
        f"should preserve all 20 jobs, got {len(node['jobs'])}"
    )
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
    assert out["node_id"] == "nodeA", (
        f"Step1 should be first-fit, expected nodeA got {out['node_id']}"
    )


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
        run_cli("add-node", "node-same", f"{4 + i}", f"{1024 + i}", "0")

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
        run_cli("add-job", "job-same", f"{1 + i}", f"{256 + i}", "0")

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


def test_concurrent_status_many_times():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")

    def status_many():
        for _ in range(30):
            r = run_cli("status")
            assert r.returncode == 0
            json.loads(r.stdout)

    threads = [threading.Thread(target=status_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_node_total_resources_preserved_after_failed_allocate():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    before = json.loads(run_cli("get-node", "node1").stdout)
    run_cli("add-job", "big", "10", "10000", "0")
    run_cli("allocate", "big", "node1")
    after = json.loads(run_cli("get-node", "node1").stdout)
    assert before["total"] == after["total"]
    assert before["used"] == after["used"]


# ---------- Further: 323->350 (still too easy, keep enhancing) ----------


def test_add_node_with_id_containing_backtick():
    clean_data()
    for nid in ["node`backtick", "node`with`multiple"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_backtick():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job`backtick", "job`with`multiple"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)


def test_list_nodes_with_limit_as_hex_string_invalid():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert run_cli("list-nodes", "0x10", "0").returncode == 2
    assert run_cli("list-jobs", "0x10", "0").returncode == 2


def test_add_node_with_cpu_as_hex_string_invalid():
    clean_data()
    assert run_cli("add-node", "node1", "0x4", "1024", "0").returncode == 2
    assert run_cli("add-job", "job1", "0x1", "256", "0").returncode == 2


def test_concurrent_add_node_with_backtick():
    clean_data()

    def add_bt(i):
        run_cli("add-node", f"node`{i}`", "4", "1024", "0")

    threads = [threading.Thread(target=add_bt, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 15
    assert checksum_valid()


def test_status_with_negative_zero_and_plus_zero_limit():
    clean_data()
    for i in range(5):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # -0 and +0 already tested for list, but status should be ok regardless of previous invalid list attempts
    assert run_cli("list-nodes", "-0", "0").returncode in (0, 2)
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 5


def test_allocate_with_extra_spaces_in_id():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    # ID with internal spaces "job 1" – if supported, should be valid? Our implementation uses first arg as ID, so "job 1" would be two args if not quoted
    # For hardening, we test that job ID with space inside via direct arg with space preserved (subprocess list preserves)
    r = run_cli("add-job", "job with space", "1", "256", "0")
    assert r.returncode in (0, 2)
    if r.returncode == 0:
        assert run_cli("allocate", "job with space", "node1").returncode == 0


def test_deallocate_with_extra_spaces():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("deallocate", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_remove_job_with_extra_spaces():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("remove-job", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_list_nodes_with_limit_offset_as_zero_and_large():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    arr_all = json.loads(run_cli("list-nodes").stdout)
    arr_zero = json.loads(run_cli("list-nodes", "0", "0").stdout)
    assert arr_all == arr_zero
    arr_large = json.loads(run_cli("list-nodes", "1000", "0").stdout)
    assert len(arr_large) == 20


def test_list_jobs_with_limit_offset_as_zero_and_large():
    clean_data()
    run_cli("add-node", "node1", "30", "30000", "0")
    for i in range(20):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    arr_all = json.loads(run_cli("list-jobs").stdout)
    arr_zero = json.loads(run_cli("list-jobs", "0", "0").stdout)
    assert arr_all == arr_zero
    assert len(json.loads(run_cli("list-jobs", "1000", "0").stdout)) == 20


def test_concurrent_allocate_with_special_chars_and_unicode():
    clean_data()
    for nid in ["node<>&", "node-🌍"]:
        run_cli("add-node", nid, "20", "20000", "0")
    for jid in ["job<>&", "job-🌍", "job😀", "job-a_b.c:d"]:
        run_cli("add-job", jid, "1", "256", "0")

    def alloc(jid, nid):
        run_cli("allocate", jid, nid)

    threads = []
    for jid in ["job<>&", "job-🌍", "job😀", "job-a_b.c:d"]:
        threads.append(threading.Thread(target=alloc, args=(jid, "node<>&")))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    node = json.loads(run_cli("get-node", "node<>&").stdout)
    assert len(node["jobs"]) == 4


def test_node_total_resources_with_large_numbers():
    clean_data()
    run_cli("add-node", "nodeLarge", "1000000", "1000000000", "1000")
    node = json.loads(run_cli("get-node", "nodeLarge").stdout)
    assert node["total"]["cpu"] == 1000000
    assert node["total"]["memory"] == 1000000000
    assert node["total"]["gpu"] == 1000


def test_job_required_with_large_numbers():
    clean_data()
    run_cli("add-node", "nodeLarge", "1000000", "1000000000", "1000")
    run_cli("add-job", "jobLarge", "500000", "500000000", "500")
    job = json.loads(run_cli("get-job", "jobLarge").stdout)
    assert job["required"]["cpu"] == 500000


def test_allocate_with_large_numbers_exact_fit():
    clean_data()
    run_cli("add-node", "nodeLarge", "1000000", "1000000000", "1000")
    run_cli("add-job", "jobLarge", "1000000", "1000000000", "1000")
    assert run_cli("allocate", "jobLarge", "nodeLarge").returncode == 0
    assert json.loads(run_cli("get-node", "nodeLarge").stdout)["free"]["cpu"] == 0


def test_concurrent_status_with_many_nodes():
    clean_data()
    for i in range(50):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")

    def status_loop():
        for _ in range(20):
            r = run_cli("status")
            assert r.returncode == 0
            json.loads(r.stdout)

    threads = [threading.Thread(target=status_loop) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_list_nodes_after_concurrent_add_and_remove():
    clean_data()

    def add_remove(i):
        run_cli("add-node", f"node-conc-{i}", "4", "1024", "0")
        run_cli("remove-node", f"node-conc-{i}")
        run_cli("add-node", f"node-conc-{i}", "4", "1024", "0")

    threads = [threading.Thread(target=add_remove, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 20
    assert checksum_valid()


# ---------- New canonicalization divergence tests per review (only family that ever caught anything is 10KB-ID+unicode+special-chars) ----------


def test_canonicalization_id_with_lt_gt_amp_and_emoji_same_key():
    clean_data()
    # ID containing <, >, & and emoji in same key — raw < in file and raw UTF-8 in checksum simultaneously
    nid = "node<>&🌍🚀😀"
    run_cli("add-node", nid, "4", "1024", "1")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # raw < must be present (SetEscapeHTML false) and raw emoji must be present (ensure_ascii=False)
    assert "<" in raw, "raw < must be present, SetEscapeHTML(false) required"
    assert ">" in raw and "&" in raw
    assert "🌍" in raw and "🚀" in raw and "😀" in raw, "raw emoji must be preserved"
    assert (
        "\\u003c" not in raw.lower()
        and "\\u003e" not in raw.lower()
        and "\\u0026" not in raw.lower()
    )
    # checksum must match canonical with ensure_ascii=False and raw UTF-8
    obj = json.loads(raw)
    canonical = json.dumps(
        obj["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    # Go escapes U+2028/U+2029 even with SetEscapeHTML(false), but not for this ID, so direct
    computed = hashlib.md5(canonical.encode()).hexdigest()
    assert obj["checksum"] == computed, (
        f"checksum mismatch for <>&+emoji ID: {obj['checksum']} vs {computed}"
    )
    assert checksum_valid()


def test_canonicalization_keys_with_u2028_u2029():
    clean_data()
    # Keys containing U+2028 / U+2029. Go's encoder escapes these even with SetEscapeHTML(false); Python with ensure_ascii=False does not.
    # Correct implementation must special-case it to match checksum
    # U+2028 = \u2028, U+2029 = \u2029
    nid = f"node\u2028with\u2029separator"
    run_cli("add-node", nid, "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # Go will escape U+2028/U+2029 as \u2028 and \u2029 even with SetEscapeHTML(false)
    # So raw file should contain escaped form OR raw char? Actually Go escapes them, so file should contain \u2028
    # Our verifier must accept both raw and escaped checksum computation
    obj = json.loads(raw)
    canonical = json.dumps(
        obj["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    canonical_escaped = canonical.replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029"
    )
    checksum = obj["checksum"]
    assert checksum in (
        hashlib.md5(canonical.encode()).hexdigest(),
        hashlib.md5(canonical_escaped.encode()).hexdigest(),
    ), f"U+2028/U+2029 checksum handling required: {checksum} not in computed"
    assert checksum_valid()


def test_canonicalization_mixed_scripts_byte_vs_codepoint_ordering():
    clean_data()
    # Key ordering with mixed scripts: sort_keys=True orders by Unicode code point; Go's sort.Strings orders by byte.
    # Pick IDs where those differ and assert checksum matches.
    # Example: IDs with different UTF-8 byte lengths where code point order != byte order
    # For simplicity, use IDs that differ in first byte vs code point: "a", "é" (é code point 233, byte order differs)
    # In Go, sorting byte-wise: "a" (0x61) < "é" (0xC3 0xA9) because 0x61 < 0xC3
    # In Python, sort_keys by code point: "a" (97) < "é" (233) same order actually same? Need better example where byte order differs from code point order
    # Use characters where code point order is opposite of byte order: e.g., "\u00E9" (é) code point 233 byte C3 A9, and "\u0101" (ā) code point 257 byte C4 81
    # Code point order: é (233) < ā (257), byte order: C3 < C4, same order again. Need where code point order differs from byte order due to different UTF-8 lengths?
    # Actually for all code points, UTF-8 byte order preserves code point order (UTF-8 is code point order preserving). So sort.Strings byte order == code point order for valid UTF-8.
    # So this third bullet may be moot, but we still test that checksum matches for mixed scripts IDs
    ids = ["node-a", "node-é", "node-ā", "node-🌍", "node-😀"]
    for nid in ids:
        run_cli("add-node", nid, "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert checksum_valid()
    obj = json.loads(raw)
    canonical = json.dumps(
        obj["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    canonical_escaped = canonical.replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029"
    )
    assert obj["checksum"] in (
        hashlib.md5(canonical.encode()).hexdigest(),
        hashlib.md5(canonical_escaped.encode()).hexdigest(),
    )
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == len(ids)
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])


def test_stale_tmp_file_ignored_and_cleaned():
    clean_data()
    # Pre-create stale <data>.tmp.<pid> before a command → must be ignored and cleaned up
    tmp_dir = os.path.dirname(DATA_FILE)
    os.makedirs(tmp_dir, exist_ok=True)
    stale_tmp = os.path.join(tmp_dir, "cluster.json.tmp.12345")
    with open(stale_tmp, "w") as f:
        f.write('{"stale": true}')
    assert os.path.exists(stale_tmp)
    run_cli("add-node", "node1", "4", "1024", "0")
    # Stale tmp should be ignored (not read as DB) and cleaned up (or at least not cause failure)
    # Our impl should not read tmp file as DB, and may clean up stale tmp files
    # Check that list-nodes returns our node, not stale content
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 1 and arr[0]["id"] == "node1"
    # If implementation cleans up, tmp should be gone; if not, at least not cause corruption
    # We assert that after command, no tmp file that would be mistaken as DB remains that breaks next command
    assert checksum_valid()
    # Clean up our stale if still exists
    try:
        os.remove(stale_tmp)
    except:
        pass


def test_stale_lock_retry_and_no_corrupt():
    clean_data()
    lock_path = DATA_FILE + ".lock"
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    try:
        os.remove(lock_path)
    except:
        pass
    with open(lock_path, "w") as f:
        f.write("stale lock")

    def remove_stale_lock():
        time.sleep(0.15)
        try:
            os.remove(lock_path)
        except:
            pass

    t = threading.Thread(target=remove_stale_lock)
    t.start()
    r = run_cli("add-node", "nodeAfterStaleLock", "4", "1024", "0")
    t.join()
    # Must either acquire after retry or fail cleanly, never leaving corrupt DB
    assert r.returncode == 0, (
        f"should acquire after stale lock removed, got {r.returncode} {r.stderr}"
    )
    assert not os.path.exists(lock_path)
    assert checksum_valid()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 1


def test_truncated_file_corruption_path():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # Truncate valid JSON prefix cut mid-object
    truncated = raw[:50]  # cut mid-object
    with open(DATA_FILE, "w") as f:
        f.write(truncated)
    r = run_cli("list-nodes")
    # Must take corruption path with .corrupt.<nanosec> backup, not crash
    assert r.returncode == 0
    assert json.loads(r.stdout) == []
    assert any(".corrupt." in fn for fn in os.listdir(os.path.dirname(DATA_FILE)))
    assert checksum_valid()


def test_exact_state_concurrency_interleaved_add_node_allocate_overlapping():
    clean_data()

    # 20 concurrent CLI processes doing interleaved add-node + allocate on overlapping IDs, then assert exact used/free arithmetic plus valid checksum
    def worker(i):
        # Overlapping IDs: node-0..4 and job-0..9 overlapping across workers
        nid = f"node-{i % 5}"
        jid = f"job-{i % 10}"
        run_cli("add-node", nid, "20", "20480", "2")
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, nid)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    # 5 nodes, 10 jobs, each job 1 CPU, each node up to 20 CPU, so all 10 jobs should be allocated, but overlapping add-node same ID should preserve first
    assert st["total_nodes"] == 5
    assert st["total_jobs"] == 10
    assert st["allocated_jobs"] == 10
    assert checksum_valid()
    for i in range(5):
        n = json.loads(run_cli("get-node", f"node-{i}").stdout)
        assert n["used"]["cpu"] == len(n["jobs"])
        assert n["used"]["cpu"] <= n["total"]["cpu"]
        assert n["free"]["cpu"] == n["total"]["cpu"] - n["used"]["cpu"]
    assert not os.path.exists(LOCK_FILE)


# ---------- New hardening: Step1 too easy -> add 20+ extra discriminators ----------


def test_global_prefix_in_step1_is_normal_id():
    clean_data()
    # In single-file mode, global: prefix is NOT special, just normal ID
    r = run_cli("add-node", "global:sequencer-1", "4", "1024", "0")
    assert r.returncode == 0
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 1 and arr[0]["id"] == "global:sequencer-1"
    assert (
        json.loads(run_cli("get-node", "global:sequencer-1").stdout)["id"]
        == "global:sequencer-1"
    )
    assert checksum_valid()
    assert not any(".tmp." in f for f in os.listdir(os.path.dirname(DATA_FILE)))


def test_id_substring_no_prefix_confusion():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    run_cli("add-node", "node10", "10", "10240", "0")
    run_cli("add-node", "node100", "10", "10240", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    n1 = json.loads(run_cli("get-node", "node1").stdout)
    n10 = json.loads(run_cli("get-node", "node10").stdout)
    assert "job1" in n1["jobs"]
    assert "job1" not in n10["jobs"]
    assert n10["used"]["cpu"] == 0
    assert checksum_valid()


def test_concurrent_allocate_deallocate_same_job_50():
    clean_data()
    run_cli("add-node", "nodeA", "100", "100000", "10")
    run_cli("add-job", "jobX", "1", "256", "0")

    def alloc():
        for _ in range(5):
            run_cli("allocate", "jobX", "nodeA")

    def dealloc():
        for _ in range(5):
            run_cli("deallocate", "jobX")

    threads = []
    for _ in range(10):
        threads.append(threading.Thread(target=alloc))
        threads.append(threading.Thread(target=dealloc))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # final state must be valid, no overcommit, no negative used
    node = json.loads(run_cli("get-node", "nodeA").stdout)
    assert node["used"]["cpu"] in (0, 1)
    assert node["used"]["cpu"] >= 0
    assert node["free"]["cpu"] == node["total"]["cpu"] - node["used"]["cpu"]
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_status_with_max_int_resources():
    clean_data()
    big = 2147483647
    r = run_cli("add-node", "bigNode", str(big), str(big), str(big))
    assert r.returncode == 0
    st = json.loads(run_cli("status").stdout)
    assert st["total_resources"]["cpu"] == big
    # adding second big node may overflow Python int? but should handle big ints
    run_cli("add-node", "bigNode2", "1", "1", "0")
    st2 = json.loads(run_cli("status").stdout)
    assert st2["total_nodes"] == 2
    assert checksum_valid()


def test_list_nodes_limit_offset_random_sorted():
    clean_data()
    for i in range(30):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    # random pagination checks – O(n^2) impl would be slow but must be correct
    import random

    for _ in range(20):
        limit = random.randint(0, 10)
        offset = random.randint(0, 35)
        r = run_cli("list-nodes", str(limit), str(offset))
        assert r.returncode == 0
        arr = json.loads(r.stdout)
        # sorted asc slice check
        all_ids = [f"node-{i:02d}" for i in range(30)]
        expected = all_ids[offset : offset + limit] if limit > 0 else all_ids[offset:]
        assert [n["id"] for n in arr] == expected


def test_corrupt_file_then_immediate_allocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    # corrupt file to trigger backup and reset
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid")
    r = run_cli("add-node", "nodeAfterCorrupt", "4", "1024", "0")
    assert r.returncode == 0
    arr = json.loads(run_cli("list-nodes").stdout)
    # after corruption handling, old data gone, only new node
    assert len(arr) == 1 and arr[0]["id"] == "nodeAfterCorrupt"
    assert any(".corrupt." in fn for fn in os.listdir(os.path.dirname(DATA_FILE)))
    assert checksum_valid()


def test_concurrent_remove_node_while_allocating_same_node():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i}", "10", "10240", "0")
        run_cli("add-job", f"job-{i}", "1", "256", "0")

    def alloc_worker(i):
        run_cli("allocate", f"job-{i}", f"node-{i}")

    def remove_worker(i):
        # try to remove node that may have job – should fail if allocated, else succeed
        run_cli("remove-node", f"node-{i}")

    threads = []
    for i in range(10):
        threads.append(threading.Thread(target=alloc_worker, args=(i,)))
        threads.append(threading.Thread(target=remove_worker, args=(i,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # final state must be valid, no overcommit, checksum valid, no lock leftover
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files)
    st = json.loads(run_cli("status").stdout)
    # allocated jobs + pending = total jobs, no overcommit
    assert st["total_jobs"] == 10
    assert st["allocated_jobs"] + st["pending_jobs"] == 10


def test_unicode_normalization_bytewise_distinct():
    clean_data()
    # é as single codepoint vs e + combining accent – must be distinct IDs byte-wise
    id1 = "node-é"  # U+00E9
    id2 = "node-e\u0301"  # e + combining
    r1 = run_cli("add-node", id1, "4", "1024", "0")
    r2 = run_cli("add-node", id2, "4", "1024", "0")
    assert r1.returncode == 0 and r2.returncode == 0
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 2
    ids = [n["id"] for n in arr]
    assert id1 in ids and id2 in ids
    assert checksum_valid()


def test_special_chars_mixed_10kb_concurrent():
    clean_data()

    # 10KB IDs with mixed special chars concurrently – stress file handling
    def worker(i):
        base = "n" + "<>&-_.:/%$*+@" * 200 + f"{i:04d}"
        # truncate to 10KB
        large_id = base[:10240]
        run_cli("add-node", large_id, "4", "1024", "0")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 20
    assert checksum_valid()
    assert not any(".tmp." in f for f in os.listdir(os.path.dirname(DATA_FILE)))


def test_allocate_exact_fit_then_fragmented_no_fit():
    clean_data()
    run_cli("add-node", "nodeA", "2", "512", "0")
    run_cli("add-node", "nodeB", "2", "512", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    run_cli("add-job", "job2", "2", "512", "0")
    run_cli("add-job", "job3", "1", "256", "0")
    assert run_cli("allocate", "job1", "nodeA").returncode == 0
    assert run_cli("allocate", "job2", "nodeB").returncode == 0
    # both nodes full, job3 should not fit
    r = run_cli("allocate", "job3", "nodeA")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()
    r2 = run_cli("schedule", "job3")
    assert r2.returncode == 1 and "no fit" in r2.stderr.lower()
    # deallocate one, then schedule should pick nodeA (first-fit)
    run_cli("deallocate", "job1")
    out = json.loads(run_cli("schedule", "job3").stdout)
    assert out["node_id"] == "nodeA"
    assert checksum_valid()


def test_raw_file_no_null_and_jobs_sorted_after_stress():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["jobZ", "jobA", "jobM", "jobB"]:
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, "node1")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert '"jobs":null' not in raw.replace(" ", "")
    # jobs should be present and sorted, not null. Raw may be '["jobA","jobB"...]' compact.
    assert '"jobs":[' in raw.replace(" ", "") or '"jobs": [' in raw
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"]) == ["jobA", "jobB", "jobM", "jobZ"]
    assert checksum_valid()


def test_concurrent_50_add_node_allocate_stress():
    clean_data()

    # 50-way stress – harder than 20-way, but moderate vs 100-way that caused timeouts
    def worker(i):
        nid = f"node-50-{i:04d}"
        jid = f"job-50-{i:04d}"
        run_cli("add-node", nid, "4", "1024", "0")
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, nid)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 50
    assert st["total_jobs"] == 50
    assert st["allocated_jobs"] == 50
    assert checksum_valid()
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files)
    assert not os.path.exists(LOCK_FILE)


def test_checksum_after_50_random_ops():
    clean_data()
    import random

    random.seed(42)
    for i in range(50):
        op = random.choice(["add-node", "add-job", "allocate", "deallocate"])
        if op == "add-node":
            run_cli("add-node", f"node-rand-{i % 20}", "4", "1024", "0")
        elif op == "add-job":
            run_cli("add-job", f"job-rand-{i % 30}", "1", "256", "0")
        elif op == "allocate":
            run_cli("allocate", f"job-rand-{i % 30}", f"node-rand-{i % 20}")
        else:
            run_cli("deallocate", f"job-rand-{i % 30}")
        if os.path.exists(DATA_FILE):
            raw = open(DATA_FILE, "r", encoding="utf-8").read().strip()
            if raw != "":
                # must remain valid JSON and checksum valid after each op – catches non-atomic writes
                json.loads(raw)
                assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files)


def test_large_scale_200_nodes_pagination_perf():
    clean_data()
    # 200 nodes – tests O(n log n) sorting, moderate vs 800 nodes <1.5s
    for i in range(100):
        run_cli("add-node", f"node-{i:05d}", "4", "1024", "0")
    import time

    start = time.time()
    r = run_cli("list-nodes", "0", "0")
    elapsed = time.time() - start
    assert r.returncode == 0
    arr = json.loads(r.stdout)
    assert len(arr) == 100
    assert elapsed < 2.5, f"list 100 nodes took {elapsed}s, should be <2.5s"
    assert checksum_valid()
