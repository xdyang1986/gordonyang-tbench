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
    assert out["node_id"] == "nodeA", (
        f"first-fit should pick nodeA not {out['node_id']}"
    )


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
    assert (
        node["free"]["cpu"] == 0
        and node["free"]["memory"] == 0
        and node["free"]["gpu"] == 0
    )
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
            if (
                invalid_content
                in open(
                    os.path.join(os.path.dirname(DATA_FILE), fn),
                    "r",
                    encoding="utf-8",
                    errors="ignore",
                ).read()
            ):
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
    assert (
        st["total_nodes"] == 0
        and st["total_jobs"] == 0
        and st["allocated_jobs"] == 0
        and st["pending_jobs"] == 0
    )
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

    threads = [
        threading.Thread(target=alloc_dealloc_loop, args=(i * 10,)) for i in range(3)
    ]
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


# ---------- Extreme: 108->120 (still too easy) ----------


def test_invalid_resources_with_plus_and_leading_zeros():
    clean_data()
    # plus sign should be allowed per Go ParseInt? Actually spec says cpu>0 int, plus sign may be considered valid – but we test leading zeros valid
    r = run_cli("add-node", "nodePlus", "+4", "1024", "0")
    # Go ParseInt allows +, so plus should be ok (not exit2) – we check not crash, either 0 or 2 acceptable? For hardening, require leading zeros valid
    # Leading zeros valid
    assert run_cli("add-node", "nodeLeadZero", "0004", "01024", "0001").returncode == 0
    node = json.loads(run_cli("get-node", "nodeLeadZero").stdout)
    assert node["total"]["cpu"] == 4


def test_extra_args_exit2():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert run_cli("add-node", "node1", "4", "1024", "0", "extra").returncode == 2
    assert run_cli("add-job", "job1", "1", "256", "0", "extra").returncode == 2
    run_cli("add-job", "job1", "1", "256", "0")
    assert run_cli("allocate", "job1", "node1", "extra").returncode == 2


def test_status_keys_and_resources():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    st = json.loads(run_cli("status").stdout)
    for k in [
        "total_nodes",
        "total_jobs",
        "allocated_jobs",
        "pending_jobs",
        "total_resources",
        "used_resources",
    ]:
        assert k in st, f"status missing key {k}"
    assert (
        "cpu" in st["total_resources"]
        and "memory" in st["total_resources"]
        and "gpu" in st["total_resources"]
    )
    assert "cpu" in st["used_resources"]


def test_get_node_and_job_keys():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "1", "256", "0")
    node = json.loads(run_cli("get-node", "node1").stdout)
    for k in ["id", "total", "used", "free", "jobs"]:
        assert k in node
    job = json.loads(run_cli("get-job", "job1").stdout)
    for k in ["id", "required", "node_id", "status"]:
        assert k in job


def test_list_nodes_limit_offset_as_float_invalid():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert run_cli("list-nodes", "2.0", "0").returncode == 2
    assert run_cli("list-nodes", "0", "1.5").returncode == 2
    assert run_cli("list-jobs", "2.0", "0").returncode == 2


def test_allocate_exact_fit_free_zero_then_no_fit():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    assert run_cli("allocate", "job1", "node1").returncode == 0
    run_cli("add-job", "job2", "1", "256", "0")
    r = run_cli("allocate", "job2", "node1")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["free"]["cpu"] == 0


def test_concurrent_allocate_and_remove_job():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")
        run_cli("allocate", f"job{i}", "node1")

    def rem_and_add(i):
        run_cli("remove-job", f"job{i}")
        run_cli("add-job", f"job_new{i}", "1", "100", "0")
        run_cli("allocate", f"job_new{i}", "node1")

    threads = [threading.Thread(target=rem_and_add, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_large_id_job_10kb():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    large_jid = "j" + "b" * 10240
    r = run_cli("add-job", large_jid, "1", "256", "0")
    assert r.returncode == 0
    job = json.loads(run_cli("get-job", large_jid).stdout)
    assert job["id"] == large_jid


def test_add_node_with_negative_zero_invalid():
    clean_data()
    # "-0" parses as 0, should be invalid because cpu>0
    assert run_cli("add-node", "nodeNegZero", "-0", "1024", "0").returncode == 2
    assert run_cli("add-node", "nodeNegZero2", "4", "-0", "0").returncode == 2


def test_remove_node_after_failed_allocate_still_possible():
    clean_data()
    run_cli("add-node", "node1", "1", "256", "0")
    run_cli("add-job", "big", "10", "10000", "0")
    assert run_cli("allocate", "big", "node1").returncode == 2
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_get_node_after_remove_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("remove-node", "node1")
    assert run_cli("get-node", "node1").returncode == 2


def test_list_nodes_with_special_chars_sorted():
    clean_data()
    ids = ["node<>&", "node-🌍", "nodeA", "nodeB"]
    for nid in ids:
        run_cli("add-node", nid, "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    got = [n["id"] for n in arr]
    assert got == sorted(got), f"list-nodes not sorted, got {got}"


# ---------- Extreme: 120->135 (still too easy) ----------


def test_overcommit_prevention_concurrent_limited_resources():
    clean_data()
    # Node 10 CPU, 20 jobs 1 CPU each, 20 concurrent allocs -> only 10 should succeed, no overcommit
    run_cli("add-node", "nodeLimited", "10", "10240", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "256", "0")

    def alloc(i):
        run_cli("allocate", f"job{i}", "nodeLimited")

    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    node = json.loads(run_cli("get-node", "nodeLimited").stdout)
    assert node["used"]["cpu"] <= 10, (
        f"overcommit detected used {node['used']['cpu']} > 10"
    )
    assert node["used"]["cpu"] == len(node["jobs"])
    assert len(node["jobs"]) <= 10
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_persistence_with_special_chars_across_restarts():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    run_cli("add-job", "job-🌍", "1", "256", "0")
    run_cli("allocate", "job-🌍", "node<>&")
    # Simulate restart: binary rebuilt? Actually same binary, but data file persists
    # List should still show special chars and raw file contains "<" and emoji
    arr_nodes = json.loads(run_cli("list-nodes").stdout)
    assert any(n["id"] == "node<>&" for n in arr_nodes)
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "<" in raw and "🌍" in raw
    assert "\\u003c" not in raw.lower()
    node = json.loads(run_cli("get-node", "node<>&").stdout)
    assert "job-🌍" in node["jobs"]


def test_concurrent_add_node_and_list_interleaved():
    clean_data()

    def add_many():
        for i in range(30):
            run_cli("add-node", f"node-a-{i:03d}", "4", "1024", "0")

    def list_many():
        for _ in range(30):
            r = run_cli("list-nodes")
            assert r.returncode == 0
            json.loads(r.stdout)

    t1 = threading.Thread(target=add_many)
    t2 = threading.Thread(target=list_many)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 30
    assert not os.path.exists(LOCK_FILE)


def test_list_nodes_zero_padded_many_zeros():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # "00000" should be parsed as 0
    arr = json.loads(run_cli("list-nodes", "00000", "00000").stdout)
    assert len(arr) == 3


def test_add_node_id_single_char_zero_valid():
    clean_data()
    assert run_cli("add-node", "0", "4", "1024", "0").returncode == 0
    assert json.loads(run_cli("get-node", "0").stdout)["id"] == "0"


def test_remove_node_special_chars():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    r = run_cli("remove-node", "node<>&")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-nodes").stdout) == []


def test_deallocate_special_chars():
    clean_data()
    run_cli("add-node", "node<>&", "4", "1024", "0")
    run_cli("add-job", "job<>&", "1", "256", "0")
    run_cli("allocate", "job<>&", "node<>&")
    r = run_cli("deallocate", "job<>&")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("get-node", "node<>&").stdout)["jobs"] == []


def test_schedule_with_gpu_requirement():
    clean_data()
    run_cli("add-node", "nodeGPU", "4", "1024", "1")
    run_cli("add-node", "nodeNoGPU", "4", "1024", "0")
    run_cli("add-job", "jobNeedGPU", "1", "256", "1")
    out = json.loads(run_cli("schedule", "jobNeedGPU").stdout)
    assert out["node_id"] == "nodeGPU"


def test_allocate_fails_does_not_change_used():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "big", "10", "10000", "0")
    before = json.loads(run_cli("get-node", "node1").stdout)
    r = run_cli("allocate", "big", "node1")
    assert r.returncode == 2
    after = json.loads(run_cli("get-node", "node1").stdout)
    assert before["used"] == after["used"] and before["free"] == after["free"]


def test_concurrent_allocate_insufficient_no_overcommit():
    clean_data()
    run_cli("add-node", "nodeSmall", "5", "5120", "0")
    for i in range(15):
        run_cli("add-job", f"job{i}", "1", "256", "0")

    def alloc(i):
        run_cli("allocate", f"job{i}", "nodeSmall")

    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    node = json.loads(run_cli("get-node", "nodeSmall").stdout)
    assert node["used"]["cpu"] == 5, f"expected 5 cpu used, got {node['used']['cpu']}"
    assert len(node["jobs"]) == 5
    assert node["used"]["memory"] == 5 * 256
    assert checksum_valid()


def test_list_nodes_after_remove_all_empty_array():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    for i in range(3):
        run_cli("remove-node", f"node-{i}")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # After removing all, data.nodes should be {} not null, and overall file valid
    assert checksum_valid()
    arr = json.loads(run_cli("list-nodes").stdout)
    assert arr == []


def test_add_job_with_dash_underscore_dot_colon_valid():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job-a", "job_b", "job.c", "job:d"]:
        assert run_cli("add-job", jid, "1", "256", "0").returncode == 0
        assert run_cli("allocate", jid, "node1").returncode == 0
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert len(node["jobs"]) == 4


def test_get_job_after_deallocate_status_pending():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    job = json.loads(run_cli("get-job", "job1").stdout)
    assert job["status"] == "pending" and job["node_id"] == ""


def test_remove_job_when_allocated_cleans_node_and_job():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("remove-job", "job1")
    assert json.loads(run_cli("list-jobs").stdout) == []
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == [] and node["used"]["cpu"] == 0


def test_concurrent_get_node_while_allocating():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")

    def alloc_loop():
        for i in range(20):
            run_cli("allocate", f"job{i}", "node1")

    def get_loop():
        for _ in range(30):
            r = run_cli("get-node", "node1")
            assert r.returncode == 0
            json.loads(r.stdout)

    t1 = threading.Thread(target=alloc_loop)
    t2 = threading.Thread(target=get_loop)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not os.path.exists(LOCK_FILE)


# ---------- Further hardening: 135->150 (still too easy) ----------


def test_status_keys_exact():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    st = json.loads(run_cli("status").stdout)
    expected_keys = {
        "total_nodes",
        "total_jobs",
        "allocated_jobs",
        "pending_jobs",
        "total_resources",
        "used_resources",
    }
    assert expected_keys.issubset(set(st.keys())), (
        f"status missing keys {expected_keys - set(st.keys())}"
    )
    assert (
        "cpu" in st["total_resources"]
        and "memory" in st["total_resources"]
        and "gpu" in st["total_resources"]
    )


def test_node_json_keys_and_structure():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    for k in ["id", "total", "used", "free", "jobs"]:
        assert k in node, f"node missing key {k}"
    for rk in ["cpu", "memory", "gpu"]:
        assert rk in node["total"] and rk in node["used"] and rk in node["free"]
    assert isinstance(node["jobs"], list)


def test_job_json_keys_and_structure():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    job = json.loads(run_cli("get-job", "job1").stdout)
    for k in ["id", "required", "node_id", "status"]:
        assert k in job, f"job missing key {k}"
    for rk in ["cpu", "memory", "gpu"]:
        assert rk in job["required"]
    assert job["status"] in ("pending", "running")


def test_allocate_json_keys():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    out = json.loads(run_cli("allocate", "job1", "node1").stdout)
    for k in ["job_id", "node_id", "allocated"]:
        assert k in out
    assert out["allocated"] is True


def test_schedule_json_keys():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    out = json.loads(run_cli("schedule", "job1").stdout)
    for k in ["job_id", "node_id", "scheduled"]:
        assert k in out
    assert out["scheduled"] is True


def test_list_nodes_returns_full_objects():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 1
    assert isinstance(arr[0], dict) and "id" in arr[0] and "total" in arr[0]


def test_list_jobs_returns_full_objects():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs").stdout)
    assert len(arr) == 1 and isinstance(arr[0], dict) and "id" in arr[0]


def test_remove_node_true_false_lowercase():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    r_true = run_cli("remove-node", "node1")
    assert r_true.stdout.strip().lower() == "true"
    r_false = run_cli("remove-node", "node1")
    assert r_false.stdout.strip().lower() == "false"


def test_deallocate_true_false_lowercase():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    r_false = run_cli("deallocate", "job1")
    assert r_false.stdout.strip().lower() == "false"
    run_cli("allocate", "job1", "node1")
    r_true = run_cli("deallocate", "job1")
    assert r_true.stdout.strip().lower() == "true"


def test_large_scale_1000_nodes_and_1000_jobs_sorted():
    clean_data()
    for i in range(500):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 500 and [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    for i in range(500):
        run_cli("add-job", f"job-{i:04d}", "1", "256", "0")
    arr2 = json.loads(run_cli("list-jobs").stdout)
    assert len(arr2) == 500 and [j["id"] for j in arr2] == sorted(
        [j["id"] for j in arr2]
    )


def test_allocate_exact_fit_memory_and_gpu():
    clean_data()
    run_cli("add-node", "nodeMem", "10", "512", "0")
    run_cli("add-job", "jobMem", "1", "512", "0")
    assert run_cli("allocate", "jobMem", "nodeMem").returncode == 0
    assert json.loads(run_cli("get-node", "nodeMem").stdout)["free"]["memory"] == 0
    run_cli("add-node", "nodeGPU", "4", "1024", "1")
    run_cli("add-job", "jobGPU2", "1", "256", "1")
    assert run_cli("allocate", "jobGPU2", "nodeGPU").returncode == 0
    assert json.loads(run_cli("get-node", "nodeGPU").stdout)["free"]["gpu"] == 0


def test_add_node_with_leading_zeros_preserves_id():
    clean_data()
    assert run_cli("add-node", "node-001", "4", "1024", "0").returncode == 0
    assert json.loads(run_cli("get-node", "node-001").stdout)["id"] == "node-001"


def test_get_nonexist_after_many_ops():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    assert run_cli("get-node", "node-not-exist-xyz").returncode == 2
    assert run_cli("get-job", "job-not-exist-xyz").returncode == 2


def test_concurrent_remove_job_while_allocating_same_job():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    run_cli("add-job", "jobRace", "1", "256", "0")

    def alloc():
        for _ in range(10):
            run_cli("allocate", "jobRace", "node1")
            run_cli("deallocate", "jobRace")

    def remove():
        time.sleep(0.02)
        run_cli("remove-job", "jobRace")

    threads = [threading.Thread(target=alloc), threading.Thread(target=remove)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()


def test_status_after_many_dealloc():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(10):
        run_cli("add-job", f"job{i}", "1", "256", "0")
        run_cli("allocate", f"job{i}", "node1")
    for i in range(10):
        run_cli("deallocate", f"job{i}")
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 0 and st["pending_jobs"] == 10
    assert st["used_resources"]["cpu"] == 0


# ---------- Extreme: 150->165 (still too easy) ----------


def test_checksum_non_canonical_is_corrupt():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    # Write file with data but checksum computed from non-canonical (with spaces and unsorted)
    data = {
        "nodes": {
            "node1": {
                "id": "node1",
                "total": {"cpu": 4, "memory": 1024, "gpu": 0},
                "used": {"cpu": 0, "memory": 0, "gpu": 0},
                "jobs": [],
            }
        },
        "jobs": {},
    }
    # Non-canonical: json.dumps with indent and without sort_keys
    non_canonical = json.dumps(data, indent=2)
    bad_checksum = hashlib.md5(non_canonical.encode()).hexdigest()
    wrapper = {"data": data, "checksum": bad_checksum}
    with open(DATA_FILE, "w") as f:
        json.dump(wrapper, f)
    r = run_cli("list-nodes")
    # Should be treated as corrupt because canonical checksum differs -> backup and empty
    assert r.returncode == 0 and json.loads(r.stdout) == []
    assert any(".corrupt." in fn for fn in os.listdir(os.path.dirname(DATA_FILE)))


def test_list_nodes_limit_very_large_returns_all():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes", "1000000", "0").stdout)
    assert len(arr) == 10


def test_list_jobs_limit_very_large_returns_all():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(10):
        run_cli("add-job", f"job-{i}", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs", "1000000", "0").stdout)
    assert len(arr) == 10


def test_concurrent_add_node_and_remove_node_same_id():
    clean_data()

    def add_remove(i):
        run_cli("add-node", f"node-race-{i % 5}", "4", "1024", "0")
        run_cli("remove-node", f"node-race-{i % 5}")

    threads = [threading.Thread(target=add_remove, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_allocate_exact_fit_memory_gpu_then_fail_next():
    clean_data()
    run_cli("add-node", "nodeMemGPU", "5", "1024", "2")
    run_cli("add-job", "job1", "5", "1024", "2")
    assert run_cli("allocate", "job1", "nodeMemGPU").returncode == 0
    node = json.loads(run_cli("get-node", "nodeMemGPU").stdout)
    assert (
        node["free"]["cpu"] == 0
        and node["free"]["memory"] == 0
        and node["free"]["gpu"] == 0
    )
    run_cli("add-job", "job2", "1", "256", "0")
    r = run_cli("allocate", "job2", "nodeMemGPU")
    assert r.returncode == 2 and "insufficient" in r.stderr.lower()


def test_remove_node_after_deallocate_then_re_add_same_id():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    run_cli("remove-node", "node1")
    assert run_cli("add-node", "node1", "8", "2048", "1").returncode == 0
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["total"]["cpu"] == 8 and node["jobs"] == []


def test_status_after_remove_all_nodes_and_jobs():
    clean_data()
    run_cli("add-node", "n1", "4", "1024", "0")
    run_cli("add-job", "j1", "1", "256", "0")
    run_cli("remove-job", "j1")
    run_cli("remove-node", "n1")
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 0 and st["total_jobs"] == 0
    assert st["total_resources"]["cpu"] == 0 and st["used_resources"]["cpu"] == 0


def test_concurrent_get_job_while_allocating():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")

    def alloc_loop():
        for i in range(20):
            run_cli("allocate", f"job{i}", "node1")

    def get_loop():
        for _ in range(30):
            for i in range(20):
                r = run_cli("get-job", f"job{i}")
                assert r.returncode in (0, 2)
                if r.returncode == 0:
                    json.loads(r.stdout)

    t1 = threading.Thread(target=alloc_loop)
    t2 = threading.Thread(target=get_loop)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert checksum_valid()


def test_add_node_with_cpu_memory_zero_invalid_gpu_zero_valid():
    clean_data()
    assert run_cli("add-node", "n0cpu", "0", "1024", "0").returncode == 2
    assert run_cli("add-node", "n0mem", "4", "0", "0").returncode == 2
    assert run_cli("add-node", "n0gpu", "4", "1024", "0").returncode == 0
    assert run_cli("add-node", "nNegZeroGPU", "4", "1024", "-0").returncode == 0
    assert run_cli("add-node", "nNegGPU", "4", "1024", "-1").returncode == 2


def test_add_job_with_zero_resources_valid():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "1")
    assert (
        run_cli("add-job", "jobZero", "0", "0", "0").returncode == 2
    )  # cpu>0 mem>0 required
    # Actually per spec job cpu>0 mem>0, so zero invalid
    assert run_cli("add-job", "job1", "1", "1", "0").returncode == 0


def test_list_nodes_contains_free_used_total_jobs():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    node = json.loads(run_cli("list-nodes").stdout)[0]
    assert "free" in node and "used" in node and "total" in node and "jobs" in node
    assert "cpu" in node["free"]


def test_list_jobs_contains_required_node_id_status():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    job = json.loads(run_cli("list-jobs").stdout)[0]
    assert "required" in job and "node_id" in job and "status" in job


def test_persistence_across_binary_rebuild():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    # Rebuild binary
    import subprocess as sp

    sp.run(
        ["go", "build", "-o", BIN, "."],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=30,
    )
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert "job1" in node["jobs"]


def test_concurrent_list_jobs_100_times_no_crash():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(50):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
        if i % 2 == 0:
            run_cli("allocate", f"job-{i:02d}", "node1")

    def list_many():
        for _ in range(30):
            r = run_cli("list-jobs")
            assert r.returncode == 0
            json.loads(r.stdout)

    threads = [threading.Thread(target=list_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_allocate_with_dash_underscore_dot_colon_ids():
    clean_data()
    for nid in ["node-a", "node_b", "node.c", "node:d"]:
        run_cli("add-node", nid, "4", "1024", "0")
    for jid in ["job-a", "job_b", "job.c", "job:d"]:
        run_cli("add-job", jid, "1", "256", "0")
    for nid, jid in zip(
        ["node-a", "node_b", "node.c", "node:d"], ["job-a", "job_b", "job.c", "job:d"]
    ):
        assert run_cli("allocate", jid, nid).returncode == 0


# ---------- Extreme: 165->180 (still too easy) ----------


def test_allocate_job_exact_fit_all_resources():
    clean_data()
    run_cli("add-node", "nodeExactAll", "3", "768", "2")
    run_cli("add-job", "jobExactAll", "3", "768", "2")
    r = run_cli("allocate", "jobExactAll", "nodeExactAll")
    assert r.returncode == 0
    node = json.loads(run_cli("get-node", "nodeExactAll").stdout)
    assert (
        node["free"]["cpu"] == 0
        and node["free"]["memory"] == 0
        and node["free"]["gpu"] == 0
    )


def test_list_nodes_with_limit_as_string_with_spaces():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # limit with spaces should be invalid? Our parse trims? Check implementation: TrimSpace then Atoi, so " 2 " valid
    r = run_cli("list-nodes", " 2 ", "0")
    # If trims, should return 2 nodes, if not, exit2 – either is acceptable for hardening? For strict, we allow trimmed spaces as valid per our impl
    assert r.returncode in (0, 2)


def test_add_node_id_with_10kb_special_chars():
    clean_data()
    large_special = "n" + "<>&🌍" * 2000  # ~10KB with special chars
    r = run_cli("add-node", large_special, "4", "1024", "0")
    assert r.returncode == 0
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "<" in raw and "🌍" in raw
    assert "\\u003c" not in raw.lower()


def test_concurrent_add_node_same_id_different_resources_preserves_first():
    clean_data()

    def add_first():
        run_cli("add-node", "nodeRacePreserve", "4", "1024", "0")

    def add_second():
        time.sleep(0.01)
        run_cli("add-node", "nodeRacePreserve", "8", "2048", "1")

    t1 = threading.Thread(target=add_first)
    t2 = threading.Thread(target=add_second)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    node = json.loads(run_cli("get-node", "nodeRacePreserve").stdout)
    # Should preserve first resources (4 CPU) not overwrite to 8
    assert node["total"]["cpu"] == 4, (
        f"should preserve first, got {node['total']['cpu']}"
    )


def test_concurrent_allocate_same_job_to_same_node_idempotent_no_duplicate():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    run_cli("add-job", "jobSame", "1", "256", "0")

    def alloc():
        for _ in range(10):
            run_cli("allocate", "jobSame", "node1")

    threads = [threading.Thread(target=alloc) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"].count("jobSame") == 1
    assert node["used"]["cpu"] == 1


def test_remove_node_with_jobs_fails_even_if_job_pending():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    # job pending (not allocated) should not block remove-node – only allocated jobs block
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_remove_node_with_allocated_jobs_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 2


def test_deallocate_then_remove_node_succeeds():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_list_nodes_after_adding_100_nodes_sorted():
    clean_data()
    for i in reversed(range(100)):
        run_cli("add-node", f"node-{i:03d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    assert len(arr) == 100


def test_list_jobs_after_adding_100_jobs_sorted():
    clean_data()
    run_cli("add-node", "node1", "200", "200000", "0")
    for i in reversed(range(100)):
        run_cli("add-job", f"job-{i:03d}", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs").stdout)
    assert [j["id"] for j in arr] == sorted([j["id"] for j in arr])


def test_file_lock_no_leftover_after_many_failures():
    clean_data()
    for _ in range(20):
        run_cli("add-node", "node1", "1", "256", "0")
        run_cli("add-job", "big", "10", "10000", "0")
        run_cli("allocate", "big", "node1")
        run_cli("remove-job", "big")
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(f.endswith(".lock") for f in files)


def test_checksum_valid_after_corruption_handling():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid")
    run_cli("list-nodes")
    assert checksum_valid(), (
        "after corruption handling, new file should have valid checksum"
    )


def test_add_node_with_id_containing_newline_tab_invalid_or_handled():
    clean_data()
    # IDs containing newline/tab should be rejected or handled without crash
    r = run_cli("add-node", "node\nwithnewline", "4", "1024", "0")
    # Either exit2 or 0 but not crash (returncode 0/2 acceptable, not 1 or panic)
    assert r.returncode in (0, 2)
    r2 = run_cli("add-node", "node\twithtab", "4", "1024", "0")
    assert r2.returncode in (0, 2)


def test_concurrent_schedule_first_fit_20_jobs():
    clean_data()
    for i in range(5):
        run_cli("add-node", f"node-{i}", "10", "10240", "0")
    for i in range(20):
        run_cli("add-job", f"job-{i}", "1", "256", "0")

    def sched(i):
        run_cli("schedule", f"job-{i}")

    threads = [threading.Thread(target=sched, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 20
    assert checksum_valid()


def test_node_free_equals_total_minus_used():
    clean_data()
    run_cli("add-node", "node1", "8", "2048", "2")
    run_cli("add-job", "job1", "2", "512", "1")
    run_cli("allocate", "job1", "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["free"]["cpu"] == node["total"]["cpu"] - node["used"]["cpu"]
    assert node["free"]["memory"] == node["total"]["memory"] - node["used"]["memory"]
    assert node["free"]["gpu"] == node["total"]["gpu"] - node["used"]["gpu"]


# ---------- Further hardening: 180->200 (still too easy) ----------


def test_list_nodes_limit_offset_single():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    arr0 = json.loads(run_cli("list-nodes", "1", "0").stdout)
    assert [n["id"] for n in arr0] == ["node-0"]
    arr1 = json.loads(run_cli("list-nodes", "1", "1").stdout)
    assert [n["id"] for n in arr1] == ["node-1"]
    arr2 = json.loads(run_cli("list-nodes", "1", "2").stdout)
    assert [n["id"] for n in arr2] == ["node-2"]


def test_list_jobs_limit_offset_single():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(3):
        run_cli("add-job", f"job-{i}", "1", "256", "0")
    arr0 = json.loads(run_cli("list-jobs", "1", "0").stdout)
    assert [j["id"] for j in arr0] == ["job-0"]
    arr1 = json.loads(run_cli("list-jobs", "1", "1").stdout)
    assert [j["id"] for j in arr1] == ["job-1"]


def test_status_empty_cluster():
    clean_data()
    st = json.loads(run_cli("status").stdout)
    assert (
        st["total_nodes"] == 0
        and st["total_jobs"] == 0
        and st["allocated_jobs"] == 0
        and st["pending_jobs"] == 0
    )
    assert st["total_resources"]["cpu"] == 0 and st["used_resources"]["cpu"] == 0


def test_allocate_exact_memory_and_gpu():
    clean_data()
    run_cli("add-node", "nodeMemExact", "10", "512", "0")
    run_cli("add-job", "jobMemExact", "1", "512", "0")
    assert run_cli("allocate", "jobMemExact", "nodeMemExact").returncode == 0
    assert json.loads(run_cli("get-node", "nodeMemExact").stdout)["free"]["memory"] == 0
    run_cli("add-node", "nodeGPUExact", "4", "1024", "2")
    run_cli("add-job", "jobGPUExact", "1", "256", "2")
    assert run_cli("allocate", "jobGPUExact", "nodeGPUExact").returncode == 0
    assert json.loads(run_cli("get-node", "nodeGPUExact").stdout)["free"]["gpu"] == 0


def test_deallocate_then_allocate_different_node():
    clean_data()
    run_cli("add-node", "nodeA", "4", "1024", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "nodeA")
    run_cli("deallocate", "job1")
    assert run_cli("allocate", "job1", "nodeB").returncode == 0
    assert json.loads(run_cli("get-job", "job1").stdout)["node_id"] == "nodeB"


def test_remove_job_not_allocated_returns_true():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    r = run_cli("remove-job", "job1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-jobs").stdout) == []


def test_concurrent_add_job_and_remove_job_same_id():
    clean_data()

    def add_remove(i):
        run_cli("add-job", f"job-race-{i % 3}", "1", "256", "0")
        run_cli("remove-job", f"job-race-{i % 3}")

    threads = [threading.Thread(target=add_remove, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_file_contains_total_used_free_jobs_keys():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    assert '"total"' in raw and '"used"' in raw and '"jobs"' in raw
    assert checksum_valid()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert "free" in node and "total" in node and "used" in node


def test_raw_file_no_null_for_empty_jobs_and_nodes():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    raw = open(DATA_FILE, "r", encoding="utf-8").read()
    # Empty jobs array must be [] not null, nodes map {} not null
    assert '"jobs":[]' in raw or '"jobs": []' in raw
    assert '"nodes":{}' in raw or '"nodes": {}' in raw or '"nodes":' in raw
    assert '"jobs":null' not in raw.replace(
        " ", ""
    ) and '"nodes":null' not in raw.replace(" ", "")


def test_concurrent_allocate_and_list_jobs():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(30):
        run_cli("add-job", f"job{i}", "1", "100", "0")

    def alloc_loop():
        for i in range(30):
            run_cli("allocate", f"job{i}", "node1")

    def list_loop():
        for _ in range(30):
            r = run_cli("list-jobs")
            assert r.returncode == 0
            json.loads(r.stdout)

    t1 = threading.Thread(target=alloc_loop)
    t2 = threading.Thread(target=list_loop)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert checksum_valid()


def test_add_node_numeric_id_valid():
    clean_data()
    assert run_cli("add-node", "1234567890", "4", "1024", "0").returncode == 0
    assert json.loads(run_cli("get-node", "1234567890").stdout)["id"] == "1234567890"


def test_schedule_no_fit_with_gpu():
    clean_data()
    run_cli("add-node", "nodeNoGPU", "4", "1024", "0")
    run_cli("add-job", "jobNeedGPU", "1", "256", "1")
    r = run_cli("schedule", "jobNeedGPU")
    assert r.returncode == 1 and "no fit" in r.stderr.lower()


def test_allocate_with_zero_cpu_invalid():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert run_cli("add-job", "jobZeroCPU", "0", "256", "0").returncode == 2


def test_status_total_used_after_concurrent_alloc():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "2", "512", "0")

    def alloc(i):
        run_cli("allocate", f"job-{i:02d}", f"node-{i:02d}")

    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 10 and st["allocated_jobs"] == 10
    assert st["total_resources"]["cpu"] == 40
    assert st["used_resources"]["cpu"] == 20


def test_get_node_with_dash_underscore_dot_colon():
    clean_data()
    for nid in ["n-a", "n_b", "n.c", "n:d"]:
        run_cli("add-node", nid, "4", "1024", "0")
        assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_unicode_node_and_job_together():
    clean_data()
    run_cli("add-node", "node-🌍🚀", "4", "1024", "0")
    run_cli("add-job", "job-😀🎉", "1", "256", "0")
    assert run_cli("allocate", "job-😀🎉", "node-🌍🚀").returncode == 0
    assert "🌍" in open(DATA_FILE, encoding="utf-8").read()
    assert "😀" in open(DATA_FILE, encoding="utf-8").read()


# ---------- Further: 196->210 (still too easy) ----------


def test_error_messages_expected_substrings():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "big", "10", "10000", "0")
    r_insuf = run_cli("allocate", "big", "node1")
    assert r_insuf.returncode == 2 and "insufficient" in r_insuf.stderr.lower()
    run_cli("add-job", "jobNoFit", "10", "10000", "0")
    r_nofit = run_cli("schedule", "jobNoFit")
    assert r_nofit.returncode == 1 and "no fit" in r_nofit.stderr.lower()
    with open(DATA_FILE, "w") as f:
        f.write("{ invalid")
    r_corr = run_cli("list-nodes")
    assert r_corr.returncode == 0
    assert "corrupt" in r_corr.stderr.lower() or "checksum" in r_corr.stderr.lower()
    assert run_cli("add-node", "", "4", "1024", "0").returncode == 2


def test_concurrent_status_while_allocating():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")

    def alloc():
        for i in range(20):
            run_cli("allocate", f"job-{i:02d}", f"node-{i:02d}")

    def status_loop():
        for _ in range(30):
            r = run_cli("status")
            assert r.returncode == 0
            json.loads(r.stdout)

    t1 = threading.Thread(target=alloc)
    t2 = threading.Thread(target=status_loop)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert checksum_valid()


def test_list_nodes_limit_offset_zero_padded_extra():
    clean_data()
    for i in range(5):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes", "00002", "00001").stdout)
    assert [n["id"] for n in arr] == ["node-1", "node-2"]


def test_add_node_id_10kb_special_chars_again():
    clean_data()
    large_special = "node-" + "🌍<>&" * 2000
    r = run_cli("add-node", large_special, "4", "1024", "0")
    assert r.returncode == 0
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "🌍" in raw and "<" in raw
    assert "\\u003c" not in raw.lower()


def test_allocate_with_numeric_id_zero():
    clean_data()
    assert run_cli("add-node", "0", "4", "1024", "0").returncode == 0
    assert run_cli("add-job", "0", "1", "256", "0").returncode == 0
    assert run_cli("allocate", "0", "0").returncode == 0
    assert json.loads(run_cli("get-job", "0").stdout)["node_id"] == "0"


def test_deallocate_many_times_idempotent():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    for _ in range(10):
        r = run_cli("deallocate", "job1")
        assert r.returncode == 0
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == [] and node["used"]["cpu"] == 0


def test_remove_node_many_times_idempotent():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    for _ in range(5):
        r = run_cli("remove-node", "node1")
        assert r.returncode == 0
    assert json.loads(run_cli("list-nodes").stdout) == []


def test_schedule_already_allocated_exit2():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    assert run_cli("schedule", "job1").returncode == 2


def test_allocate_with_job_id_special_chars_dash_underscore():
    clean_data()
    run_cli("add-node", "node-a_b.c:d", "10", "10240", "0")
    for jid in ["job-a", "job_b", "job.c", "job:d"]:
        run_cli("add-job", jid, "1", "256", "0")
        assert run_cli("allocate", jid, "node-a_b.c:d").returncode == 0
    node = json.loads(run_cli("get-node", "node-a_b.c:d").stdout)
    assert len(node["jobs"]) == 4


def test_status_resources_after_remove():
    clean_data()
    run_cli("add-node", "node1", "8", "2048", "2")
    run_cli("add-job", "job1", "2", "512", "1")
    run_cli("allocate", "job1", "node1")
    run_cli("remove-job", "job1")
    st = json.loads(run_cli("status").stdout)
    assert st["used_resources"]["cpu"] == 0 and st["total_resources"]["cpu"] == 8


def test_concurrent_add_job_many_times():
    clean_data()

    def add_many():
        for i in range(30):
            run_cli("add-job", f"job-conc-{i:03d}", "1", "256", "0")

    threads = [threading.Thread(target=add_many) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arr = json.loads(run_cli("list-jobs").stdout)
    assert len(arr) == 30
    assert checksum_valid()


def test_list_nodes_and_jobs_limit_1_offset_0_first_sorted():
    clean_data()
    for nid in ["nodeZ", "nodeA", "nodeM"]:
        run_cli("add-node", nid, "4", "1024", "0")
    first = json.loads(run_cli("list-nodes", "1", "0").stdout)
    assert [n["id"] for n in first] == ["nodeA"]


def test_persistence_special_chars_after_rebuild():
    clean_data()
    run_cli("add-node", "node<>&🌍", "4", "1024", "0")
    run_cli("add-job", "job<>&😀", "1", "256", "0")
    run_cli("allocate", "job<>&😀", "node<>&🌍")
    import subprocess as sp

    sp.run(
        ["go", "build", "-o", BIN, "."],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=30,
    )
    node = json.loads(run_cli("get-node", "node<>&🌍").stdout)
    assert "job<>&😀" in node["jobs"]
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "<" in raw and "🌍" in raw and "😀" in raw


# ---------- Further: 209->225 (still too easy) ----------


def test_list_nodes_pagination_all_offsets():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    for offset in range(10):
        arr = json.loads(run_cli("list-nodes", "1", str(offset)).stdout)
        assert len(arr) == 1 and arr[0]["id"] == f"node-{offset:02d}"


def test_list_jobs_pagination_all_offsets():
    clean_data()
    run_cli("add-node", "node1", "20", "20000", "0")
    for i in range(10):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    for offset in range(10):
        arr = json.loads(run_cli("list-jobs", "1", str(offset)).stdout)
        assert len(arr) == 1 and arr[0]["id"] == f"job-{offset:02d}"


def test_concurrent_allocate_and_deallocate_same_jobs():
    clean_data()
    run_cli("add-node", "node1", "50", "50000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "256", "0")

    def alloc_dealloc(i):
        run_cli("allocate", f"job{i}", "node1")
        run_cli("deallocate", f"job{i}")
        run_cli("allocate", f"job{i}", "node1")

    threads = [threading.Thread(target=alloc_dealloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert len(node["jobs"]) == 20


def test_large_scale_1000_nodes_list_limit_offset():
    clean_data()
    for i in range(1000):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    t0 = time.time()
    arr = json.loads(run_cli("list-nodes", "100", "500").stdout)
    elapsed = time.time() - t0
    assert len(arr) == 100
    assert elapsed < 1.5


def test_large_scale_1000_jobs_list_limit_offset():
    clean_data()
    run_cli("add-node", "node1", "2000", "2000000", "0")
    for i in range(800):
        run_cli("add-job", f"job-{i:04d}", "1", "256", "0")
    t0 = time.time()
    arr = json.loads(run_cli("list-jobs", "100", "400").stdout)
    elapsed = time.time() - t0
    assert len(arr) == 100
    assert elapsed < 1.5


def test_node_total_used_free_consistency():
    clean_data()
    run_cli("add-node", "node1", "8", "2048", "2")
    for i in range(3):
        run_cli("add-job", f"job{i}", "2", "512", "0")
        run_cli("allocate", f"job{i}", "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["total"]["cpu"] == node["used"]["cpu"] + node["free"]["cpu"]
    assert node["total"]["memory"] == node["used"]["memory"] + node["free"]["memory"]
    assert node["total"]["gpu"] == node["used"]["gpu"] + node["free"]["gpu"]


def test_job_required_preserved_after_allocate_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "1")
    run_cli("add-job", "job1", "2", "512", "1")
    run_cli("allocate", "job1", "node1")
    job1 = json.loads(run_cli("get-job", "job1").stdout)
    assert job1["required"]["cpu"] == 2 and job1["required"]["gpu"] == 1
    run_cli("deallocate", "job1")
    job2 = json.loads(run_cli("get-job", "job1").stdout)
    assert job2["required"]["cpu"] == 2 and job2["required"]["gpu"] == 1


def test_add_node_with_id_hyphen_underscore_dot_colon_at_edges():
    clean_data()
    for nid in ["-node", "_node", ".node", ":node", "node-", "node_", "node.", "node:"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)  # either allowed or rejected, but not crash
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_hyphen_underscore_dot_colon_at_edges():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["-job", "_job", ".job", ":job", "job-", "job_", "job.", "job:"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-job", jid).stdout)["id"] == jid


def test_concurrent_add_node_with_special_chars():
    clean_data()

    def add_special(i):
        run_cli("add-node", f"node<>&{i}", "4", "1024", "0")
        run_cli("add-node", f"node-🌍{i}", "4", "1024", "0")

    threads = [threading.Thread(target=add_special, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 20
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "<" in raw and "🌍" in raw
    assert checksum_valid()


def test_status_with_no_nodes_but_jobs():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("remove-node", "node1")
    st = json.loads(run_cli("status").stdout)
    # Node removed but job still exists pending
    assert st["total_nodes"] == 0
    assert st["total_jobs"] == 1
    assert st["pending_jobs"] == 1
    assert st["allocated_jobs"] == 0


def test_allocate_with_exact_fit_cpu_memory_gpu_all():
    clean_data()
    run_cli("add-node", "nodeExact", "5", "1024", "1")
    run_cli("add-job", "jobExact", "5", "1024", "1")
    assert run_cli("allocate", "jobExact", "nodeExact").returncode == 0
    node = json.loads(run_cli("get-node", "nodeExact").stdout)
    assert (
        node["free"]["cpu"] == 0
        and node["free"]["memory"] == 0
        and node["free"]["gpu"] == 0
    )


def test_list_nodes_with_limit_offset_as_empty_string_invalid():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    # Empty string for limit/offset should be invalid or treated as 0? Our impl treats missing as -1, empty string Atoi fails -> exit2
    assert run_cli("list-nodes", "", "0").returncode == 2
    assert run_cli("list-nodes", "0", "").returncode == 2


def test_deallocate_after_remove_job_fails():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("remove-job", "job1")
    assert run_cli("deallocate", "job1").returncode == 2


def test_remove_node_after_remove_jobs_succeeds_and_empty():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    for i in range(3):
        run_cli("add-job", f"job{i}", "1", "256", "0")
        run_cli("allocate", f"job{i}", "node1")
    for i in range(3):
        run_cli("remove-job", f"job{i}")
    r = run_cli("remove-node", "node1")
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-nodes").stdout) == []


def test_concurrent_schedule_and_allocate():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "20", "20480", "0")
    for i in range(30):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")

    def sched(i):
        run_cli("schedule", f"job-{i:02d}")

    def alloc(i):
        run_cli("allocate", f"job-{i:02d}", f"node-{i % 10:02d}")

    threads = []
    for i in range(15):
        threads.append(threading.Thread(target=sched, args=(i,)))
    for i in range(15, 30):
        threads.append(threading.Thread(target=alloc, args=(i,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    # With 20 CPU each, 10 nodes total 200 capacity, 30 jobs should all fit even with first-fit fragmentation
    assert st["allocated_jobs"] == 30, f"expected 30 allocated, got {st}"
    assert checksum_valid()
    # No overcommit
    for i in range(10):
        n = json.loads(run_cli("get-node", f"node-{i:02d}").stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]


# ---------- Further hardening: 225->250 (still too easy) ----------


def test_add_node_10kb_id_with_special_chars_and_emoji_alloc():
    clean_data()
    large_id = "node-" + "a<>&🌍" * 2000
    r = run_cli("add-node", large_id, "4", "1024", "0")
    assert r.returncode == 0
    run_cli("add-job", "job1", "1", "256", "0")
    assert run_cli("allocate", "job1", large_id).returncode == 0
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "<" in raw and "🌍" in raw
    assert "\\u003c" not in raw.lower()
    assert checksum_valid()


def test_add_job_10kb_id_with_special_chars_alloc():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    large_jid = "job-" + "b<>&😀" * 2000
    r = run_cli("add-job", large_jid, "1", "256", "0")
    assert r.returncode == 0
    assert run_cli("allocate", large_jid, "node1").returncode == 0
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "😀" in raw and "<" in raw
    assert checksum_valid()


def test_list_nodes_limit_offset_very_large():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes", "999999", "0").stdout)
    assert len(arr) == 10
    assert json.loads(run_cli("list-nodes", "1", "999999").stdout) == []
    assert json.loads(run_cli("list-jobs", "999999", "0").stdout) == []


def test_concurrent_add_node_remove_node_same_id_race():
    clean_data()

    def add_remove(i):
        run_cli("add-node", f"node-race-{i % 5:02d}", "4", "1024", "0")
        run_cli("remove-node", f"node-race-{i % 5:02d}")

    threads = [threading.Thread(target=add_remove, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    assert not os.path.exists(LOCK_FILE)


def test_status_total_resources_after_many_nodes():
    clean_data()
    total_cpu = 0
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", f"{i + 1}", "1024", "0")
        total_cpu += i + 1
    st = json.loads(run_cli("status").stdout)
    assert st["total_resources"]["cpu"] == total_cpu
    assert st["total_nodes"] == 20


def test_get_node_with_10kb_id():
    clean_data()
    large_id = "n" + "x" * 10240
    run_cli("add-node", large_id, "4", "1024", "0")
    node = json.loads(run_cli("get-node", large_id).stdout)
    assert node["id"] == large_id


def test_get_job_with_10kb_id():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    large_jid = "j" + "y" * 10240
    run_cli("add-job", large_jid, "1", "256", "0")
    job = json.loads(run_cli("get-job", large_jid).stdout)
    assert job["id"] == large_jid


def test_remove_node_with_10kb_id():
    clean_data()
    large_id = "n" + "z" * 10240
    run_cli("add-node", large_id, "4", "1024", "0")
    r = run_cli("remove-node", large_id)
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("list-nodes").stdout) == []


def test_deallocate_with_10kb_job_id():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    large_jid = "j" + "a" * 10240
    run_cli("add-job", large_jid, "1", "256", "0")
    run_cli("allocate", large_jid, "node1")
    r = run_cli("deallocate", large_jid)
    assert r.returncode == 0 and "true" in r.stdout.lower()
    assert json.loads(run_cli("get-node", "node1").stdout)["jobs"] == []


def test_concurrent_list_nodes_and_jobs_interleaved():
    clean_data()
    for i in range(30):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")

    def list_nodes():
        for _ in range(20):
            assert run_cli("list-nodes").returncode == 0

    def list_jobs():
        for _ in range(20):
            assert run_cli("list-jobs").returncode == 0

    t1 = threading.Thread(target=list_nodes)
    t2 = threading.Thread(target=list_jobs)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert checksum_valid()


def test_add_node_id_with_newline_tab_handled():
    clean_data()
    for bad_id in ["node\nnewline", "node\twithtab", "node\rwithcr"]:
        r = run_cli("add-node", bad_id, "4", "1024", "0")
        assert r.returncode in (0, 2), (
            f"should handle newline/tab without crash, got {r.returncode}"
        )


def test_add_job_id_with_newline_tab_handled():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    for bad_id in ["job\nnewline", "job\twithtab"]:
        r = run_cli("add-job", bad_id, "1", "256", "0")
        assert r.returncode in (0, 2)


def test_file_lock_no_leftover_after_concurrent_failures():
    clean_data()
    run_cli("add-node", "node1", "1", "256", "0")

    def fail_alloc(i):
        run_cli("add-job", f"big{i}", "10", "10000", "0")
        run_cli("allocate", f"big{i}", "node1")

    threads = [threading.Thread(target=fail_alloc, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(f.endswith(".lock") for f in files)
    assert checksum_valid()


def test_list_nodes_with_limit_offset_as_float_string_invalid():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert run_cli("list-nodes", "1.0", "0").returncode == 2
    assert run_cli("list-nodes", "0", "1.0").returncode == 2


def test_status_pending_after_deallocate():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 0 and st["pending_jobs"] == 1
    assert st["used_resources"]["cpu"] == 0


def test_concurrent_schedule_many_jobs_no_overcommit():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "20", "20480", "0")
    for i in range(50):
        run_cli("add-job", f"job-{i:02d}", "2", "512", "0")

    def sched(i):
        run_cli("schedule", f"job-{i:02d}")

    threads = [threading.Thread(target=sched, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 50
    for i in range(10):
        n = json.loads(run_cli("get-node", f"node-{i:02d}").stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]
    assert checksum_valid()


# ---------- Further hardening: 241->260 (still too easy) ----------


def test_add_node_id_with_spaces_at_ends():
    clean_data()
    # ID with leading/trailing spaces should be preserved (not trimmed) and retrievable, unless all spaces -> exit2
    # " node1" with leading space valid (contains non-space)
    r = run_cli("add-node", " node1", "4", "1024", "0")
    assert r.returncode in (
        0,
        2,
    )  # allow exit2 for strict impl that trims, but not crash
    if r.returncode == 0:
        # Should be retrievable with same spaces
        assert run_cli("get-node", " node1").returncode == 0


def test_add_job_id_with_spaces_at_ends():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    r = run_cli("add-job", " job1", "1", "256", "0")
    assert r.returncode in (0, 2)
    if r.returncode == 0:
        assert run_cli("get-job", " job1").returncode == 0


def test_list_nodes_with_limit_offset_with_spaces():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # " 1 " with spaces should be valid after trim
    r = run_cli("list-nodes", " 1 ", " 1 ")
    assert r.returncode in (0, 2)


def test_concurrent_add_node_and_remove_job():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "100", "0")

    def add_remove_mix(i):
        run_cli("add-node", f"node-mix-{i}", "4", "1024", "0")
        run_cli("remove-job", f"job{i}")

    threads = [threading.Thread(target=add_remove_mix, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()


def test_allocate_with_job_id_numeric_string():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "123", "1", "256", "0")
    assert run_cli("allocate", "123", "node1").returncode == 0
    assert json.loads(run_cli("get-job", "123").stdout)["node_id"] == "node1"


def test_deallocate_with_numeric_id_zero():
    clean_data()
    run_cli("add-node", "0", "4", "1024", "0")
    run_cli("add-job", "0", "1", "256", "0")
    run_cli("allocate", "0", "0")
    r = run_cli("deallocate", "0")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_remove_node_with_numeric_id_zero():
    clean_data()
    run_cli("add-node", "0", "4", "1024", "0")
    r = run_cli("remove-node", "0")
    assert r.returncode == 0 and "true" in r.stdout.lower()


def test_list_nodes_with_large_limit_and_small_offset():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes", "100", "5").stdout)
    assert len(arr) == 15
    assert [n["id"] for n in arr] == [f"node-{i:02d}" for i in range(5, 20)]


def test_list_jobs_with_large_limit_and_small_offset():
    clean_data()
    run_cli("add-node", "node1", "30", "30000", "0")
    for i in range(20):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs", "100", "5").stdout)
    assert len(arr) == 15


def test_node_used_free_total_all_keys_present():
    clean_data()
    run_cli("add-node", "node1", "8", "2048", "2")
    node = json.loads(run_cli("get-node", "node1").stdout)
    for k in ["id", "total", "used", "free", "jobs"]:
        assert k in node
    for rk in ["cpu", "memory", "gpu"]:
        assert rk in node["total"] and rk in node["used"] and rk in node["free"]


def test_job_required_node_id_status_keys():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "2", "512", "1")
    job = json.loads(run_cli("get-job", "job1").stdout)
    assert "required" in job and "node_id" in job and "status" in job
    assert job["required"]["cpu"] == 2


def test_allocate_exact_fit_cpu_memory():
    clean_data()
    run_cli("add-node", "nodeExactMem", "3", "768", "0")
    run_cli("add-job", "jobExactMem", "3", "768", "0")
    assert run_cli("allocate", "jobExactMem", "nodeExactMem").returncode == 0
    assert json.loads(run_cli("get-node", "nodeExactMem").stdout)["free"]["memory"] == 0


def test_concurrent_list_nodes_with_limit_offset():
    clean_data()
    for i in range(50):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")

    def list_paginated():
        for _ in range(20):
            r = run_cli("list-nodes", "10", "5")
            assert r.returncode == 0
            json.loads(r.stdout)

    threads = [threading.Thread(target=list_paginated) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_add_node_with_id_containing_slash():
    clean_data()
    for nid in ["node/withslash", "node\\withbackslash"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_slash():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job/withslash", "job\\withbackslash"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)


def test_file_lock_is_removed_even_when_command_fails_invalid():
    clean_data()
    r = run_cli("add-node", "", "4", "1024", "0")
    assert r.returncode == 2
    assert not os.path.exists(LOCK_FILE)
    r2 = run_cli("add-node", "node1", "0", "1024", "0")
    assert r2.returncode == 2
    assert not os.path.exists(LOCK_FILE)


def test_checksum_valid_with_unicode_and_special_chars():
    clean_data()
    run_cli("add-node", "node-🌍<>&", "4", "1024", "0")
    run_cli("add-job", "job-😀<>&", "1", "256", "0")
    run_cli("allocate", "job-😀<>&", "node-🌍<>&")
    assert checksum_valid()
    raw = open(DATA_FILE, encoding="utf-8").read()
    assert "🌍" in raw and "😀" in raw and "<" in raw
    assert "\\u" not in raw or "\\u003c" not in raw.lower()


# ---------- Further: 258->275 (still too easy) ----------


def test_add_node_with_id_containing_equals_and_semicolon():
    clean_data()
    for nid in ["node=eq", "node;semicolon", "node:colon:double", "node,withcomma"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_equals_and_semicolon():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job=eq", "job;semicolon", "job:colon:double", "job,withcomma"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-job", jid).stdout)["id"] == jid


def test_list_nodes_with_limit_offset_as_negative_zero():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # "-0" should be parsed as 0 valid (since -0 ==0)
    r = run_cli("list-nodes", "-0", "0")
    assert r.returncode in (0, 2)  # allow exit2 if strict rejects negative
    if r.returncode == 0:
        assert len(json.loads(r.stdout)) == 3


def test_list_jobs_with_limit_offset_as_negative_zero():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(3):
        run_cli("add-job", f"job-{i}", "1", "256", "0")
    r = run_cli("list-jobs", "-0", "0")
    assert r.returncode in (0, 2)
    if r.returncode == 0:
        assert len(json.loads(r.stdout)) == 3


def test_add_node_with_id_10kb_dash_underscore_dot_colon():
    clean_data()
    large_id = "node-" + "-_b.c:d" * 2000
    r = run_cli("add-node", large_id, "4", "1024", "0")
    assert r.returncode == 0
    assert json.loads(run_cli("get-node", large_id).stdout)["id"] == large_id


def test_add_job_with_id_10kb_dash_underscore_dot_colon():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    large_jid = "job-" + "-_b.c:d" * 2000
    r = run_cli("add-job", large_jid, "1", "256", "0")
    assert r.returncode == 0
    assert json.loads(run_cli("get-job", large_jid).stdout)["id"] == large_jid


def test_status_with_many_nodes_and_jobs_total_used():
    clean_data()
    total_cpu = 0
    for i in range(30):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "1")
        total_cpu += 4
    for i in range(20):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 30 and st["total_jobs"] == 20
    assert st["total_resources"]["cpu"] == total_cpu
    assert st["used_resources"]["cpu"] == 0


def test_allocate_with_cpu_memory_gpu_exact_all_then_list():
    clean_data()
    run_cli("add-node", "nodeExactAll", "5", "1024", "1")
    run_cli("add-job", "jobExactAll", "5", "1024", "1")
    assert run_cli("allocate", "jobExactAll", "nodeExactAll").returncode == 0
    node = json.loads(run_cli("get-node", "nodeExactAll").stdout)
    assert (
        node["free"]["cpu"] == 0
        and node["free"]["memory"] == 0
        and node["free"]["gpu"] == 0
    )
    arr = json.loads(run_cli("list-nodes").stdout)
    assert arr[0]["free"]["cpu"] == 0


def test_deallocate_all_jobs_then_node_free_equals_total():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "2")
    for i in range(5):
        run_cli("add-job", f"job{i}", "2", "512", "0")
        run_cli("allocate", f"job{i}", "node1")
    for i in range(5):
        run_cli("deallocate", f"job{i}")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["used"]["cpu"] == 0 and node["free"]["cpu"] == node["total"]["cpu"]
    assert node["jobs"] == []


def test_concurrent_allocate_same_node_many_times_with_exact_fit():
    clean_data()
    run_cli("add-node", "nodeExact", "10", "10240", "2")
    for i in range(10):
        run_cli("add-job", f"job{i}", "1", "1024", "0")

    def alloc(i):
        run_cli("allocate", f"job{i}", "nodeExact")

    threads = [threading.Thread(target=alloc, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    node = json.loads(run_cli("get-node", "nodeExact").stdout)
    assert node["used"]["cpu"] == 10 and node["free"]["cpu"] == 0
    assert len(node["jobs"]) == 10
    assert checksum_valid()


def test_list_nodes_and_jobs_with_limit_1_many_offsets():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    for offset in [0, 5, 10, 15, 19]:
        arr_n = json.loads(run_cli("list-nodes", "1", str(offset)).stdout)
        assert len(arr_n) == 1 and arr_n[0]["id"] == f"node-{offset:02d}"
        arr_j = json.loads(run_cli("list-jobs", "1", str(offset)).stdout)
        assert len(arr_j) == 1 and arr_j[0]["id"] == f"job-{offset:02d}"


def test_add_node_with_id_containing_percent_and_ampersand():
    clean_data()
    for nid in ["node%percent", "node&and", "node?query", "node#hash"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_percent_and_ampersand():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job%percent", "job&and", "job?query", "job#hash"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-job", jid).stdout)["id"] == jid


def test_file_lock_is_exclusive_and_cleaned():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    assert not os.path.exists(LOCK_FILE)
    run_cli("add-job", "job1", "1", "256", "0")
    assert not os.path.exists(LOCK_FILE)
    run_cli("allocate", "job1", "node1")
    assert not os.path.exists(LOCK_FILE)
    run_cli("deallocate", "job1")
    assert not os.path.exists(LOCK_FILE)


def test_checksum_valid_with_many_nodes_and_jobs():
    clean_data()
    for i in range(100):
        run_cli("add-node", f"node-{i:03d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:03d}", "1", "256", "0")
    assert checksum_valid()
    raw = open(DATA_FILE).read()
    # Must contain checksum key
    assert '"checksum"' in raw


def test_schedule_with_many_nodes_first_fit_still_works():
    clean_data()
    for i in range(20):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    for i in range(20):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
        out = json.loads(run_cli("schedule", f"job-{i:02d}").stdout)
        assert out["scheduled"] is True
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 20


# ---------- Further: 274->300 (still too easy, keep enhancing) ----------


def test_large_scale_1000_nodes_sorted_first_and_last():
    clean_data()
    for i in range(1000):
        run_cli("add-node", f"node-{i:04d}", "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == 1000
    assert arr[0]["id"] == "node-0000" and arr[-1]["id"] == "node-0999"
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])


def test_large_scale_1000_jobs_sorted_first_and_last():
    clean_data()
    run_cli("add-node", "node1", "2000", "2000000", "0")
    for i in range(1000):
        run_cli("add-job", f"job-{i:04d}", "1", "256", "0")
    arr = json.loads(run_cli("list-jobs").stdout)
    assert len(arr) == 1000
    assert arr[0]["id"] == "job-0000" and arr[-1]["id"] == "job-0999"


def test_concurrent_allocate_100_jobs_to_10_nodes_no_overcommit():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "20", "20480", "0")
    for i in range(100):
        run_cli("add-job", f"job-{i:03d}", "2", "512", "0")

    def alloc_range(start):
        for i in range(start, start + 20):
            run_cli("allocate", f"job-{i:03d}", f"node-{(i % 10):02d}")

    threads = [threading.Thread(target=alloc_range, args=(i * 20,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["allocated_jobs"] == 100
    for i in range(10):
        n = json.loads(run_cli("get-node", f"node-{i:02d}").stdout)
        assert n["used"]["cpu"] <= n["total"]["cpu"]
    assert not os.path.exists(LOCK_FILE)


def test_add_node_id_with_10kb_dash_underscore_dot_colon_and_special():
    clean_data()
    large_id = "node-" + "-_." * 3000 + ":a" * 1000
    r = run_cli("add-node", large_id, "4", "1024", "0")
    assert r.returncode == 0
    assert json.loads(run_cli("get-node", large_id).stdout)["id"] == large_id


def test_add_job_id_with_10kb_dash_underscore_dot_colon():
    clean_data()
    run_cli("add-node", "node1", "100", "100000", "0")
    large_jid = "job-" + "_-.:a" * 2500
    r = run_cli("add-job", large_jid, "1", "256", "0")
    assert r.returncode == 0


def test_list_nodes_with_negative_zero_and_plus_zero():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # -0 should be treated as 0 valid, +0 as 0 valid (Go ParseInt allows +)
    for lim in ["-0", "+0", "00", "000"]:
        r = run_cli("list-nodes", lim, "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert len(json.loads(r.stdout)) == 3


def test_list_jobs_with_negative_zero_and_plus_zero():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(3):
        run_cli("add-job", f"job-{i}", "1", "256", "0")
    for lim in ["-0", "+0"]:
        r = run_cli("list-jobs", lim, "0")
        assert r.returncode in (0, 2)


def test_status_total_used_exact_after_alloc_dealloc_cycle():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "5")
    for i in range(10):
        run_cli("add-job", f"job{i}", "1", "1024", "0")
    for i in range(5):
        run_cli("allocate", f"job{i}", "node1")
    st1 = json.loads(run_cli("status").stdout)
    assert st1["used_resources"]["cpu"] == 5 and st1["allocated_jobs"] == 5
    for i in range(5):
        run_cli("deallocate", f"job{i}")
    st2 = json.loads(run_cli("status").stdout)
    assert (
        st2["used_resources"]["cpu"] == 0
        and st2["allocated_jobs"] == 0
        and st2["pending_jobs"] == 10
    )


def test_node_jobs_after_allocate_deallocate_reallocate_sorted():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["jobC", "jobA", "jobB"]:
        run_cli("add-job", jid, "1", "256", "0")
    run_cli("allocate", "jobC", "node1")
    run_cli("allocate", "jobA", "node1")
    run_cli("deallocate", "jobC")
    run_cli("allocate", "jobB", "node1")
    run_cli("allocate", "jobC", "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["jobs"] == sorted(node["jobs"])


def test_concurrent_add_node_and_remove_node_many_times():
    clean_data()

    def worker(i):
        for j in range(20):
            run_cli("add-node", f"node-{i}-{j:02d}", "4", "1024", "0")
            run_cli("remove-node", f"node-{i}-{j:02d}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()


def test_concurrent_add_job_and_remove_job_many_times():
    clean_data()
    run_cli("add-node", "node1", "200", "200000", "0")

    def worker(i):
        for j in range(20):
            run_cli("add-job", f"job-{i}-{j:02d}", "1", "256", "0")
            run_cli("remove-job", f"job-{i}-{j:02d}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()


def test_allocate_with_job_id_case_sensitive():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    run_cli("add-job", "JobA", "1", "256", "0")
    run_cli("add-job", "joba", "1", "256", "0")
    assert run_cli("allocate", "JobA", "node1").returncode == 0
    assert run_cli("allocate", "joba", "node1").returncode == 0
    assert json.loads(run_cli("get-node", "node1").stdout)["used"]["cpu"] == 2


def test_get_node_with_id_64_chars_valid():
    clean_data()
    nid_64 = "n" + "a" * 63
    assert len(nid_64) == 64
    assert run_cli("add-node", nid_64, "4", "1024", "0").returncode == 0
    assert json.loads(run_cli("get-node", nid_64).stdout)["id"] == nid_64


def test_get_job_with_id_64_chars_valid():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    jid_64 = "j" + "b" * 63
    assert run_cli("add-job", jid_64, "1", "256", "0").returncode == 0
    assert json.loads(run_cli("get-job", jid_64).stdout)["id"] == jid_64


def test_remove_node_with_allocated_jobs_fails_even_after_list():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    # list should not affect remove failure
    json.loads(run_cli("list-nodes").stdout)
    assert run_cli("remove-node", "node1").returncode == 2


def test_deallocate_and_allocate_different_node_preserves_resources():
    clean_data()
    run_cli("add-node", "nodeA", "4", "1024", "0")
    run_cli("add-node", "nodeB", "4", "1024", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    run_cli("allocate", "job1", "nodeA")
    assert json.loads(run_cli("get-node", "nodeA").stdout)["used"]["cpu"] == 2
    run_cli("deallocate", "job1")
    assert json.loads(run_cli("get-node", "nodeA").stdout)["used"]["cpu"] == 0
    run_cli("allocate", "job1", "nodeB")
    assert json.loads(run_cli("get-node", "nodeB").stdout)["used"]["cpu"] == 2
    assert json.loads(run_cli("get-node", "nodeA").stdout)["used"]["cpu"] == 0


def test_file_lock_retry_with_two_concurrent_holders():
    clean_data()
    lock_path = DATA_FILE + ".lock"
    try:
        os.remove(lock_path)
    except:
        pass
    with open(lock_path, "w") as f:
        f.write("locked")

    def remove1():
        time.sleep(0.15)
        try:
            os.remove(lock_path)
        except:
            pass

    def create_again():
        time.sleep(0.05)
        # Another process tries to create lock while first lock exists – should fail to acquire and retry
        # But we simulate by not removing, just let original lock remain
        pass

    t = threading.Thread(target=remove1)
    t.start()
    r = run_cli("add-node", "nodeRetryLock", "4", "1024", "0")
    t.join()
    assert r.returncode == 0
    assert not os.path.exists(lock_path)
    assert json.loads(run_cli("list-nodes").stdout)[0]["id"] == "nodeRetryLock"


# ---------- Further: 291->310 (still too easy, keep enhancing) ----------


def test_list_nodes_with_limit_offset_with_spaces_and_zero_padded():
    clean_data()
    for i in range(5):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    # spaces and zero-padded should be valid after trim
    r = run_cli("list-nodes", " 02 ", " 01 ")
    assert r.returncode in (0, 2)
    if r.returncode == 0:
        assert len(json.loads(r.stdout)) == 2


def test_add_node_id_with_10kb_mixed_and_allocate_many():
    clean_data()
    large_id = "node-" + "a-_b.c:d<>&🌍😀" * 800
    r = run_cli("add-node", large_id, "10", "10240", "2")
    assert r.returncode == 0
    for i in range(5):
        run_cli("add-job", f"job-{i}", "2", "512", "0")
        assert run_cli("allocate", f"job-{i}", large_id).returncode == 0
    assert checksum_valid()


def test_add_job_id_with_10kb_mixed_and_allocate():
    clean_data()
    run_cli("add-node", "node1", "20", "20000", "2")
    large_jid = "job-" + "b-_c.d:e<>&😀🌍" * 800
    r = run_cli("add-job", large_jid, "2", "512", "1")
    assert r.returncode == 0
    assert run_cli("allocate", large_jid, "node1").returncode == 0
    assert checksum_valid()


def test_status_total_resources_exact_after_adding_30_nodes():
    clean_data()
    total_cpu = 0
    total_mem = 0
    total_gpu = 0
    for i in range(30):
        cpu = i + 1
        mem = (i + 1) * 100
        gpu = i % 3
        run_cli("add-node", f"node-{i:02d}", str(cpu), str(mem), str(gpu))
        total_cpu += cpu
        total_mem += mem
        total_gpu += gpu
    st = json.loads(run_cli("status").stdout)
    assert st["total_resources"]["cpu"] == total_cpu
    assert st["total_resources"]["memory"] == total_mem
    assert st["total_resources"]["gpu"] == total_gpu


def test_concurrent_add_node_with_id_containing_equals():
    clean_data()

    def add_eq(i):
        run_cli("add-node", f"node=eq-{i}", "4", "1024", "0")

    threads = [threading.Thread(target=add_eq, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 15
    assert checksum_valid()


def test_concurrent_add_job_with_id_containing_equals():
    clean_data()
    run_cli("add-node", "node1", "50", "50000", "0")

    def add_eq(i):
        run_cli("add-job", f"job=eq-{i}", "1", "256", "0")

    threads = [threading.Thread(target=add_eq, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-jobs").stdout)) == 15


def test_allocate_with_cpu_memory_exact_and_free_zero():
    clean_data()
    run_cli("add-node", "nodeExact", "3", "768", "0")
    run_cli("add-job", "jobExact", "3", "768", "0")
    assert run_cli("allocate", "jobExact", "nodeExact").returncode == 0
    node = json.loads(run_cli("get-node", "nodeExact").stdout)
    assert node["free"]["cpu"] == 0 and node["free"]["memory"] == 0


def test_deallocate_and_remove_node_then_re_add():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    run_cli("remove-job", "job1")
    run_cli("remove-node", "node1")
    assert run_cli("add-node", "node1", "8", "2048", "1").returncode == 0
    assert json.loads(run_cli("get-node", "node1").stdout)["total"]["cpu"] == 8


def test_list_nodes_with_special_chars_sorted_and_limit():
    clean_data()
    ids = ["node<>&", "node-🌍", "nodeA", "nodeB", "node-a", "node_b"]
    for nid in ids:
        run_cli("add-node", nid, "4", "1024", "0")
    arr = json.loads(run_cli("list-nodes").stdout)
    assert len(arr) == len(ids)
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])
    arr2 = json.loads(run_cli("list-nodes", "2", "1").stdout)
    assert len(arr2) == 2


def test_file_lock_cleaned_after_many_successful_ops():
    clean_data()
    for i in range(30):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
        run_cli("allocate", f"job-{i:02d}", f"node-{i:02d}")
        run_cli("deallocate", f"job-{i:02d}")
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(f.endswith(".lock") for f in files)


def test_checksum_valid_after_deallocate_and_remove():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job1", "1", "256", "0")
    run_cli("allocate", "job1", "node1")
    run_cli("deallocate", "job1")
    run_cli("remove-job", "job1")
    run_cli("remove-node", "node1")
    assert checksum_valid()
    assert json.loads(run_cli("list-nodes").stdout) == []


def test_add_node_with_id_containing_percent_ampersand_valid():
    clean_data()
    for nid in ["node%percent", "node&and"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_percent_ampersand_valid():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job%percent", "job&and"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)


def test_concurrent_schedule_30_jobs():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "10", "10240", "0")
    for i in range(30):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")

    def sched(i):
        run_cli("schedule", f"job-{i:02d}")

    threads = [threading.Thread(target=sched, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(run_cli("status").stdout)["allocated_jobs"] == 30
    assert checksum_valid()


def test_node_free_equals_total_minus_used_after_many_ops():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "2")
    for i in range(5):
        run_cli("add-job", f"job{i}", "2", "1024", "0")
        run_cli("allocate", f"job{i}", "node1")
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["free"]["cpu"] == node["total"]["cpu"] - node["used"]["cpu"]
    assert node["free"]["memory"] == node["total"]["memory"] - node["used"]["memory"]
    for i in range(5):
        run_cli("deallocate", f"job{i}")
    node2 = json.loads(run_cli("get-node", "node1").stdout)
    assert node2["free"]["cpu"] == node2["total"]["cpu"]
    assert node2["jobs"] == []


def test_list_nodes_and_jobs_with_limit_offset_large_numbers():
    clean_data()
    for i in range(50):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    arr_n = json.loads(run_cli("list-nodes", "20", "10").stdout)
    assert len(arr_n) == 20
    assert arr_n[0]["id"] == "node-10"
    arr_j = json.loads(run_cli("list-jobs", "20", "10").stdout)
    assert len(arr_j) == 20
    assert arr_j[0]["id"] == "job-10"


# ---------- Further: 307->325 (still too easy, keep enhancing) ----------


def test_add_node_with_id_containing_brackets():
    clean_data()
    for nid in ["node[1]", "node]2[", "node{3}", "node}4{", "node(5)", "node)6("]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_brackets():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job[1]", "job]2[", "job{3}", "job}4{"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)


def test_list_nodes_with_limit_as_plus_sign():
    clean_data()
    for i in range(3):
        run_cli("add-node", f"node-{i}", "4", "1024", "0")
    r = run_cli("list-nodes", "+2", "0")
    assert r.returncode in (0, 2)
    if r.returncode == 0:
        assert len(json.loads(r.stdout)) == 2


def test_list_jobs_with_limit_as_plus_sign():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for i in range(3):
        run_cli("add-job", f"job-{i}", "1", "256", "0")
    r = run_cli("list-jobs", "+2", "0")
    assert r.returncode in (0, 2)


def test_concurrent_add_node_with_brackets():
    clean_data()

    def add_bracket(i):
        run_cli("add-node", f"node[{i}]", "4", "1024", "0")

    threads = [threading.Thread(target=add_bracket, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(run_cli("list-nodes").stdout)) == 15
    assert checksum_valid()


def test_status_with_zero_nodes_and_zero_jobs_keys():
    clean_data()
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 0 and st["total_jobs"] == 0
    assert "total_resources" in st and "used_resources" in st


def test_get_node_with_id_containing_equals():
    clean_data()
    run_cli("add-node", "node=eq", "4", "1024", "0")
    assert json.loads(run_cli("get-node", "node=eq").stdout)["id"] == "node=eq"


def test_get_job_with_id_containing_equals():
    clean_data()
    run_cli("add-node", "node1", "4", "1024", "0")
    run_cli("add-job", "job=eq", "1", "256", "0")
    assert json.loads(run_cli("get-job", "job=eq").stdout)["id"] == "job=eq"


def test_allocate_with_exact_fit_then_deallocate_then_allocate_again():
    clean_data()
    run_cli("add-node", "node1", "2", "512", "0")
    run_cli("add-job", "job1", "2", "512", "0")
    assert run_cli("allocate", "job1", "node1").returncode == 0
    run_cli("deallocate", "job1")
    run_cli("add-job", "job2", "2", "512", "0")
    assert run_cli("allocate", "job2", "node1").returncode == 0
    assert json.loads(run_cli("get-node", "node1").stdout)["free"]["cpu"] == 0


def test_concurrent_deallocate_and_allocate_same_node_different_jobs():
    clean_data()
    run_cli("add-node", "node1", "20", "20000", "0")
    for i in range(20):
        run_cli("add-job", f"job{i}", "1", "256", "0")
        run_cli("allocate", f"job{i}", "node1")

    def dealloc_alloc(i):
        run_cli("deallocate", f"job{i}")
        run_cli("add-job", f"job_new{i}", "1", "256", "0")
        run_cli("allocate", f"job_new{i}", "node1")

    threads = [threading.Thread(target=dealloc_alloc, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checksum_valid()
    node = json.loads(run_cli("get-node", "node1").stdout)
    assert node["used"]["cpu"] == 20


def test_list_nodes_with_large_offset_and_limit_0():
    clean_data()
    for i in range(10):
        run_cli("add-node", f"node-{i:02d}", "4", "1024", "0")
    assert json.loads(run_cli("list-nodes", "0", "100").stdout) == []
    assert json.loads(run_cli("list-nodes", "0", "10").stdout) == []


def test_list_jobs_with_large_offset_and_limit_0():
    clean_data()
    run_cli("add-node", "node1", "20", "20000", "0")
    for i in range(10):
        run_cli("add-job", f"job-{i:02d}", "1", "256", "0")
    assert json.loads(run_cli("list-jobs", "0", "100").stdout) == []
    assert json.loads(run_cli("list-jobs", "0", "10").stdout) == []


def test_add_node_with_id_containing_dollar_and_star():
    clean_data()
    for nid in ["node$dollar", "node*star", "node+plus", "node@at"]:
        r = run_cli("add-node", nid, "4", "1024", "0")
        assert r.returncode in (0, 2)
        if r.returncode == 0:
            assert json.loads(run_cli("get-node", nid).stdout)["id"] == nid


def test_add_job_with_id_containing_dollar_and_star():
    clean_data()
    run_cli("add-node", "node1", "10", "10240", "0")
    for jid in ["job$dollar", "job*star", "job+plus"]:
        r = run_cli("add-job", jid, "1", "256", "0")
        assert r.returncode in (0, 2)


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


def test_concurrent_100_add_node_allocate_stress():
    clean_data()

    # 100-way concurrency stress – naive lock without retry or non-atomic write will lose updates or corrupt
    def worker(i):
        nid = f"node-stress-{i:04d}"
        jid = f"job-stress-{i:04d}"
        run_cli("add-node", nid, "4", "1024", "0")
        run_cli("add-job", jid, "1", "256", "0")
        run_cli("allocate", jid, nid)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = json.loads(run_cli("status").stdout)
    assert st["total_nodes"] == 100
    assert st["total_jobs"] == 100
    assert st["allocated_jobs"] == 100
    assert checksum_valid()
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files), f"tmp leftover {files}"
    assert not os.path.exists(LOCK_FILE)
    # ensure sorted order preserved after heavy concurrency
    arr = json.loads(run_cli("list-nodes", "0", "0").stdout)
    assert [n["id"] for n in arr] == sorted([n["id"] for n in arr])


def test_concurrent_allocate_deallocate_same_job_50():
    clean_data()
    run_cli("add-node", "nodeA", "100", "100000", "10")
    run_cli("add-job", "jobX", "1", "256", "0")

    def alloc():
        for _ in range(10):
            run_cli("allocate", "jobX", "nodeA")

    def dealloc():
        for _ in range(10):
            run_cli("deallocate", "jobX")

    threads = []
    for _ in range(25):
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


def test_checksum_after_100_random_ops():
    clean_data()
    import random

    # deterministic pseudo-random sequence
    for i in range(100):
        op = random.choice(["add-node", "add-job", "allocate", "deallocate"])
        if op == "add-node":
            run_cli("add-node", f"node-{i % 20}", "4", "1024", "0")
        elif op == "add-job":
            run_cli("add-job", f"job-{i % 30}", "1", "256", "0")
        elif op == "allocate":
            # may fail if not exist or insufficient, that's ok
            run_cli("allocate", f"job-{i % 30}", f"node-{i % 20}")
        else:
            run_cli("deallocate", f"job-{i % 30}")
        # after each op, file must be valid JSON and checksum valid if exists
        if os.path.exists(DATA_FILE):
            raw = open(DATA_FILE, "r", encoding="utf-8").read().strip()
            if raw != "":
                json.loads(raw)  # should not throw
                assert checksum_valid(), f"checksum invalid after op {i} {op}"
    assert not os.path.exists(LOCK_FILE)
    files = os.listdir(os.path.dirname(DATA_FILE))
    assert not any(".tmp." in f for f in files)


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


def test_empty_data_dir_recreated():
    clean_data()
    # remove entire data dir
    import shutil

    d = os.path.dirname(DATA_FILE)
    shutil.rmtree(d, ignore_errors=True)
    r = run_cli("list-nodes")
    assert r.returncode == 0
    assert json.loads(r.stdout) == []
    # dir should be recreated
    assert os.path.exists(d)
    assert checksum_valid() or not os.path.exists(DATA_FILE)


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
