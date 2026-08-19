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


