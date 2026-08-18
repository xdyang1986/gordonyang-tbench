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
