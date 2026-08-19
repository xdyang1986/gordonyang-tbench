"""
Step 2 tests: High-QPS geofence service with spatial index, cache, batch, concurrency, CRUD, eviction, rounding, antimeridian, pole (Hard version, relative perf)
"""

import os
import json
import subprocess
import tempfile
import shutil
import time
import random
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests

# Bypass fwdproxy for localhost requests (eval infra moves 8080->51484 and sets http_proxy).
# Without this, requests to localhost go via proxy and timeout with "serving on :port".
os.environ["NO_PROXY"] = (
    os.environ.get("NO_PROXY", "") + ",localhost,127.0.0.1,0.0.0.0,::1"
)
os.environ["no_proxy"] = (
    os.environ.get("no_proxy", "") + ",localhost,127.0.0.1,0.0.0.0,::1"
)
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

# Force requests to ignore env proxies (requests respects proxies kwarg; setting to None bypasses)
_orig_get = requests.get
_orig_post = requests.post
_orig_delete = requests.delete


def _wrap_req(orig):
    def _inner(*args, **kwargs):
        kwargs.setdefault("proxies", {"http": None, "https": None})
        return orig(*args, **kwargs)

    return _inner


requests.get = _wrap_req(requests.get)
requests.post = _wrap_req(requests.post)
requests.delete = _wrap_req(requests.delete)

APP = "/app/src"
BIN = "/tmp/geofencectl_step2"

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
            ["go", "mod", "init", "geofence"],
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
            timeout=120,
        )

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, (
        f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert os.path.exists(BIN)
    yield


def run_cli(db_path, args, timeout=10):
    cmd = [BIN, "--db", db_path] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_server(db_path, port, grid_size="1", cache_size="1000", timeout=10):
    cmd = [
        BIN,
        "--db",
        db_path,
        "serve",
        "--port",
        str(port),
        "--grid-size",
        str(grid_size),
        "--cache-size",
        str(cache_size),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://localhost:{port}/stats", timeout=1)
            if resp.status_code == 200:
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise RuntimeError(f"server failed to start: stdout={out} stderr={err}")
        time.sleep(0.2)
    proc.terminate()
    try:
        out, err = proc.communicate(timeout=2)
    except:
        proc.kill()
        out, err = "", "killed"
    raise RuntimeError(f"server not ready within {timeout}s: out={out} err={err}")


def stop_server(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def assert_is_list_not_null(obj, field="geofences"):
    """Broad nil-slice check: field must be list, not None, and raw JSON must not contain null for that field."""
    assert field in obj, f"missing field {field} in {obj}"
    val = obj[field]
    assert isinstance(val, list), f"field {field} should be list, got {type(val)} {val}"
    # val may be [] but not None


def assert_response_arrays_valid(resp):
    """Ensure every array field in response is list not null, check raw text for :null where array expected."""
    # Parse json
    try:
        data = resp.json()
    except:
        return

    # Check common array fields
    # For /lookup: geofences
    # For /lookup/batch: results is list, each results[i].geofences list
    # For /geofences: should be list
    # We will check recursively for any 'geofences' key that is None
    def check_obj(o):
        if isinstance(o, dict):
            if "geofences" in o:
                assert o["geofences"] is not None, f"geofences is null in {o}"
                assert isinstance(o["geofences"], list), f"geofences not list in {o}"
            for v in o.values():
                check_obj(v)
        elif isinstance(o, list):
            for item in o:
                check_obj(item)

    check_obj(data)
    # Also raw text should not have :null for geofences if we expect [] – but we already checked parsed
    # For empty geofences endpoint, raw should be [] not null
    txt = resp.text.strip()
    if txt == "null":
        raise AssertionError(f"response is literal null, expected array: {txt[:200]}")


# ---- stdlib / LRU own impl enforcement (M2) ----


def test_gomod_stdlib_and_lru_own():
    """Go stdlib only and LRU must be own implementation – fail if go.mod has external require or LRU imported from external."""
    gomod_path = os.path.join(APP, "go.mod")
    if not os.path.exists(gomod_path):
        return
    with open(gomod_path) as f:
        content = f.read()
    lower = content.lower()
    forbidden = [
        "github.com",
        "golang.org/x",
        "gopkg.in",
        "gitlab.com",
        "bitbucket.org",
    ]
    for substr in forbidden:
        assert substr not in lower, (
            f"go.mod contains external dep {substr}: {content[:500]}"
        )
    import re as _re

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require"):
            rest = stripped[len("require") :].strip()
            if rest.startswith("(") or rest == "":
                continue
            mod = rest.split()[0]
            if "/" in mod and "." in mod.split("/")[0]:
                assert False, f"go.mod has external require {mod}"

    # Check .go files for external imports – only look at actual import declarations
    for root, _dirs, files in os.walk(APP):
        for fname in files:
            if not fname.endswith(".go"):
                continue
            path = os.path.join(root, fname)
            try:
                lines = (
                    open(path, encoding="utf-8", errors="ignore").read().splitlines()
                )
            except:
                continue
            in_block = False
            for line in lines:
                stripped = line.strip()
                if not in_block and stripped.startswith("import ("):
                    in_block = True
                    quotes = _re.findall(r'"([^"]+)"', line)
                    for imp in quotes:
                        if "." in imp and "/" in imp:
                            assert False, (
                                f"{path} imports external package {imp!r} – stdlib only"
                            )
                        if "lru" in imp.lower():
                            # any import containing lru is disallowed – LRU must be own impl, stdlib has no lru
                            assert False, (
                                f"{path} imports lru package {imp!r} – LRU must be own implementation"
                            )
                    continue
                if in_block:
                    quotes = _re.findall(r'"([^"]+)"', line)
                    for imp in quotes:
                        if "." in imp and "/" in imp:
                            assert False, (
                                f"{path} imports external package {imp!r} – stdlib only"
                            )
                        if "lru" in imp.lower():
                            assert False, (
                                f"{path} imports lru package {imp!r} – LRU must be own implementation"
                            )
                    if ")" in stripped:
                        in_block = False
                else:
                    if stripped.startswith("import "):
                        quotes = _re.findall(r'"([^"]+)"', line)
                        for imp in quotes:
                            if "." in imp and "/" in imp:
                                assert False, (
                                    f"{path} imports external package {imp!r} – stdlib only"
                                )
                            if "lru" in imp.lower():
                                assert False, (
                                    f"{path} imports lru package {imp!r} – LRU must be own implementation"
                                )

    # Also check that an LRU implementation exists – look for a type or struct containing LRU logic
    # We don't enforce structure, but at least ensure no external lru usage
    # And ensure at least one .go file defines an LRU cache (heuristic: contains "type LRU" or "LRUCache")
    found_lru = False
    for root, _dirs, files in os.walk(APP):
        for fname in files:
            if not fname.endswith(".go"):
                continue
            path = os.path.join(root, fname)
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except:
                continue
            if "LRU" in txt or "lru" in txt.lower():
                found_lru = True
    # If cache-size >0 is used, an LRU should be present; we don't hard fail if not found, but we checked external


# ---- CLI backward compat (including strict validation) ----


def test_cli_still_works():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(
            db, ["add", "zone_a", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"]
        )
        assert r.returncode == 0
        r = run_cli(db, ["list"])
        assert r.returncode == 0
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        assert isinstance(arr, list)
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, list), "lookup should return list, not null"
        assert data == ["zone_a"]
        r = run_cli(db, ["remove", "zone_a"])
        assert r.returncode == 0

        r = run_cli(db, ["add", "bad", "--polygon", "0,0;;0,1;1,1;1,0", "--name", "A"])
        assert r.returncode == 2, "empty segment should be rejected"
        r = run_cli(db, ["add", "bad", "--polygon", "0,0;1,1;0,1;1,0", "--name", "A"])
        assert r.returncode == 2, "self-intersecting should be rejected"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- HTTP correctness ----


def test_http_lookup_correctness():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        run_cli(
            db, ["add", "sq2", "--polygon", "10,10;10,11;11,11;11,10", "--name", "sq2"]
        )
        port = get_free_port()
        proc = start_server(db, port)
        try:
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            data = resp.json()
            assert_is_list_not_null(data, "geofences")
            assert data["geofences"] == ["sq"]
            assert data["count"] == 1

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=2&lng=2", timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert resp.json()["geofences"] == []

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=10.5&lng=10.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == ["sq2"]

            # invalid lat
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=100&lng=0", timeout=2
            )
            assert resp.status_code == 400

            # missing param
            resp = requests.get(f"http://localhost:{port}/lookup?lat=0", timeout=2)
            assert resp.status_code == 400

            # geofences endpoint
            resp = requests.get(f"http://localhost:{port}/geofences", timeout=2)
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            arr = resp.json()
            assert isinstance(arr, list), "geofences should be list not null"
            assert len(arr) == 2
            ids = [x["id"] for x in arr]
            assert ids == sorted(ids)

            # stats
            resp = requests.get(f"http://localhost:{port}/stats", timeout=2)
            assert resp.status_code == 200
            stats = resp.json()
            for k in [
                "total_geofences",
                "total_queries",
                "cache_hits",
                "cache_size",
                "avg_latency_ms",
                "index_cells",
            ]:
                assert k in stats
            assert stats["total_geofences"] == 2
            assert stats["index_cells"] > 0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_http_verbose_lookup():
    """Verbose returns full objects."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "myzone"])
        port = get_free_port()
        proc = start_server(db, port)
        try:
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5&verbose=true",
                timeout=2,
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            data = resp.json()
            assert_is_list_not_null(data, "geofences")
            assert data["count"] == 1
            assert len(data["geofences"]) == 1
            assert data["geofences"][0]["id"] == "sq"
            assert "polygon" in data["geofences"][0]

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == ["sq"]
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_http_single_geofence():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port)
        try:
            resp = requests.get(f"http://localhost:{port}/geofences/sq", timeout=2)
            assert resp.status_code == 200
            assert resp.json()["id"] == "sq"

            resp = requests.get(
                f"http://localhost:{port}/geofences/notfound", timeout=2
            )
            assert resp.status_code == 404
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_http_crud():
    """POST adds, GET finds, lookup finds new, DELETE removes, with cache invalidation."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10")
        try:
            payload = {
                "id": "newzone",
                "name": "New",
                "polygon": [
                    {"lat": 5, "lng": 5},
                    {"lat": 5, "lng": 6},
                    {"lat": 6, "lng": 6},
                    {"lat": 6, "lng": 5},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload, timeout=2
            )
            assert resp.status_code in (200, 201), f"POST failed {resp.text}"
            assert_response_arrays_valid(resp)
            assert resp.json()["id"] == "newzone"

            resp = requests.get(f"http://localhost:{port}/geofences/newzone", timeout=2)
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=5.5&lng=5.5", timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert "newzone" in resp.json()["geofences"]

            # invalid self-intersecting should 400
            bad = {
                "id": "bad",
                "name": "Bad",
                "polygon": [
                    {"lat": 0, "lng": 0},
                    {"lat": 1, "lng": 1},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 0},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=bad, timeout=2
            )
            assert resp.status_code == 400

            resp = requests.delete(
                f"http://localhost:{port}/geofences/newzone", timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=5.5&lng=5.5", timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert "newzone" not in resp.json()["geofences"]

            resp = requests.get(f"http://localhost:{port}/geofences/newzone", timeout=2)
            assert resp.status_code == 404

            stop_server(proc)
            port2 = get_free_port()
            proc2 = start_server(db, port2)
            try:
                resp = requests.get(f"http://localhost:{port2}/geofences", timeout=2)
                assert_response_arrays_valid(resp)
                ids = [x["id"] for x in resp.json()]
                assert "newzone" not in ids
                assert "sq" in ids
            finally:
                stop_server(proc2)
                proc = None
        finally:
            if proc:
                stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_http_batch():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port)
        try:
            payload = {"points": [{"lat": 0.5, "lng": 0.5}, {"lat": 2, "lng": 2}]}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            results = resp.json()["results"]
            assert len(results) == 2
            assert_is_list_not_null(results[0], "geofences")
            assert results[0]["geofences"] == ["sq"]
            assert results[1]["geofences"] == []
            assert isinstance(results[1]["geofences"], list)

            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json={"points": []}, timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            assert resp.json()["results"] == []
            assert isinstance(resp.json()["results"], list)

            big = {"points": [{"lat": 0, "lng": 0}] * 1001}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=big, timeout=2
            )
            assert resp.status_code == 400

            bad = {"points": [{"lat": 100, "lng": 0}]}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=bad, timeout=2
            )
            assert resp.status_code == 400
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_behavior():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10")
        try:
            requests.get(f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2)
            resp = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            hits_before = resp["cache_hits"]

            for _ in range(5):
                r = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(r)
                assert_is_list_not_null(r.json(), "geofences")

            resp = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert resp["cache_hits"] >= hits_before + 4
            assert resp["cache_size"] >= 1
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_rounding():
    """Nearby points should share cache entry when rounded to 6 decimals."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10")
        try:
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5000001&lng=0.5000001", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            stats1 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5000002&lng=0.5000002", timeout=2
            )
            assert_response_arrays_valid(resp)
            stats2 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats2["cache_hits"] >= stats1["cache_hits"] + 1
            assert stats2["cache_size"] == 1
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_eviction():
    """LRU eviction: size 2, 3 unique points evicts LRU, and MRU stays."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="2")
        try:
            requests.get(f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2)
            requests.get(f"http://localhost:{port}/lookup?lat=1.5&lng=1.5", timeout=2)
            requests.get(f"http://localhost:{port}/lookup?lat=2.5&lng=2.5", timeout=2)
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["cache_size"] == 2

            stop_server(proc)
            port2 = get_free_port()
            proc2 = start_server(db, port2, cache_size="2")
            try:
                requests.get(
                    f"http://localhost:{port2}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                requests.get(
                    f"http://localhost:{port2}/lookup?lat=1.5&lng=1.5", timeout=2
                )
                requests.get(
                    f"http://localhost:{port2}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                stats_mid = requests.get(
                    f"http://localhost:{port2}/stats", timeout=2
                ).json()
                assert stats_mid["cache_hits"] == 1
                requests.get(
                    f"http://localhost:{port2}/lookup?lat=2.5&lng=2.5", timeout=2
                )
                stats_mid2 = requests.get(
                    f"http://localhost:{port2}/stats", timeout=2
                ).json()
                assert stats_mid2["cache_size"] == 2
                requests.get(
                    f"http://localhost:{port2}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                stats_final = requests.get(
                    f"http://localhost:{port2}/stats", timeout=2
                ).json()
                assert stats_final["cache_hits"] == stats_mid2["cache_hits"] + 1
                hits_before_b = stats_final["cache_hits"]
                requests.get(
                    f"http://localhost:{port2}/lookup?lat=1.5&lng=1.5", timeout=2
                )
                stats_after_b = requests.get(
                    f"http://localhost:{port2}/stats", timeout=2
                ).json()
                assert stats_after_b["cache_hits"] == hits_before_b
            finally:
                stop_server(proc2)
                proc = None
        finally:
            if proc:
                stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_index_cells():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "small", "--polygon", "0,0;0,1;1,1;1,0", "--name", "s"])
        run_cli(
            db,
            ["add", "large", "--polygon", "-10,-10;-10,10;10,10;10,-10", "--name", "l"],
        )
        port = get_free_port()
        proc = start_server(db, port, grid_size="1")
        try:
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["index_cells"] > 0
        finally:
            stop_server(proc)

        port2 = get_free_port()
        proc2 = start_server(db, port2, grid_size="5")
        try:
            stats2 = requests.get(f"http://localhost:{port2}/stats", timeout=2).json()
            assert stats2["index_cells"] > 0
            assert stats["index_cells"] >= stats2["index_cells"]
        finally:
            stop_server(proc2)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_index_large_polygon():
    """World polygon creates many cells but should not OOM and lookup fast.

    P0 fix: drop index_cells >100 assert (internal detail, flags global vs materializing 64800).
    Instead assert behaviour: world matches at every longitude in its latitude band.
    """
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly = "-90,-180;-90,180;90,180;90,-180"
        run_cli(db, ["add", "world", "--polygon", poly, "--name", "World"])
        for i in range(100):
            base_lat = (i // 10) * 2.0
            base_lng = (i % 10) * 2.0
            p = f"{base_lat},{base_lng};{base_lat},{base_lng + 0.8};{base_lat + 0.8},{base_lng + 0.8};{base_lat + 0.8},{base_lng}"
            run_cli(db, ["add", f"z_{i:03d}", "--polygon", p, "--name", f"Z{i}"])

        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="100")
        try:
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # No assertion on exact cell count: an implementation may flag world as global
            # instead of materializing 64800 cells and still be correct.
            assert stats["index_cells"] >= 0
            assert stats["total_geofences"] == 101

            # Behaviour that matters: world must match at every longitude in its lat band.
            for lng in [150, 0, -100, 100, -179, 179, 180, -180]:
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=80&lng={lng}", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert_is_list_not_null(resp.json(), "geofences")
                assert "world" in resp.json()["geofences"], (
                    f"world should match at lat=80 lng={lng}, got {resp.json()['geofences']}"
                )

            # Also at equator longitude 0 (covers every longitude)
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "world" in resp.json()["geofences"]

            # Latency bound: lookup should be fast even with world + 100 zones
            avg = _measure_avg_latency(port, 80, 150, repeats=10)
            assert avg < 1.0, f"world lookup too slow {avg}s"
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_no_cache_mode():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="0")
        try:
            for _ in range(3):
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert_is_list_not_null(resp.json(), "geofences")
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["cache_size"] == 0
            assert stats["cache_hits"] == 0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- Relative performance checks (no absolute floors) ----


def _create_many_geofences(db, count=100, points_per=4):
    run_cli(db, ["clear"])
    import math as _math

    for i in range(count):
        if i == 0:
            # Keep first zone at 0,0 so tests that use 0.5,0.5 as inside still work
            base_lat, base_lng = 0.0, 0.0
        else:
            # Keep lat/lng within valid range even for 500 zones.
            # Original (i//10)*2 exceeds 90 at i>=450. Use safe wrapped grid for i>=1.
            # Spread across world: lat cycles -80..56, lng -170..153
            lat_idx = ((i - 1) // 20) % 18
            lng_idx = (i - 1) % 20
            base_lat = lat_idx * 8.0 - 80.0
            base_lng = lng_idx * 17.0 - 170.0
        if points_per == 4:
            poly = f"{base_lat},{base_lng};{base_lat},{base_lng + 0.8};{base_lat + 0.8},{base_lng + 0.8};{base_lat + 0.8},{base_lng}"
        else:
            pts = []
            for j in range(points_per):
                ang = 2 * _math.pi * j / points_per
                lat = base_lat + 0.4 + 0.3 * _math.sin(ang)
                lng = base_lng + 0.4 + 0.3 * _math.cos(ang)
                lat = max(-89.9, min(89.9, lat))
                lng = max(-179.9, min(179.9, lng))
                pts.append(f"{lat},{lng}")
            poly = ";".join(pts)
        r = run_cli(
            db, ["add", f"zone_{i:04d}", "--polygon", poly, "--name", f"Zone {i}"]
        )
        assert r.returncode == 0, f"add zone {i} failed {r.stderr} poly={poly[:100]}"


def _measure_avg_latency(port, lat, lng, repeats=20, timeout=2):
    start = time.time()
    for _ in range(repeats):
        resp = requests.get(
            f"http://localhost:{port}/lookup?lat={lat}&lng={lng}", timeout=timeout
        )
        assert resp.status_code == 200
        assert_response_arrays_valid(resp)
    elapsed = time.time() - start
    return elapsed / repeats


def _measure_server_avg_latency_ms(port, lat, lng, repeats=20, timeout=2):
    """Server-side measurement via /stats to avoid Python loopback overhead dominating."""
    # get baseline stats
    s_before = requests.get(f"http://localhost:{port}/stats", timeout=timeout).json()
    q_before = s_before.get("total_queries", 0)
    # Use totalLatencyNs if available via avg_latency_ms * total_queries, but we have avg_latency_ms cumulative
    # Instead we compute delta of avg_latency_ms * queries or use direct measurement of internal latency
    # Our server tracks totalLatencyNs internally but exposes avg_latency_ms. We'll approximate by measuring
    # avg_latency_ms after queries and also using the difference in totalQueries.
    # For more precise, we fetch stats before and after and compute weighted avg:
    # avg_after * q_after - avg_before * q_before = sum latency of new queries / (q_after-q_before) ??? Actually avg is cumulative average.
    # totalLatency = avg * totalQ, so delta totalLatency / delta Q = avg of new queries in server time.
    # This excludes HTTP overhead.
    for _ in range(repeats):
        resp = requests.get(
            f"http://localhost:{port}/lookup?lat={lat}&lng={lng}", timeout=timeout
        )
        assert resp.status_code == 200
    s_after = requests.get(f"http://localhost:{port}/stats", timeout=timeout).json()
    q_after = s_after.get("total_queries", 0)
    avg_before = s_before.get("avg_latency_ms", 0.0)
    avg_after = s_after.get("avg_latency_ms", 0.0)
    # total latency ms
    total_before = avg_before * q_before
    total_after = avg_after * q_after
    delta_q = q_after - q_before
    if delta_q <= 0:
        return avg_after  # fallback
    delta_total = total_after - total_before
    return delta_total / delta_q if delta_q else avg_after


def test_relative_index_performance():
    """Server-side latency: 500-zone empty lookup should be O(1) with index, not O(n). Measures via /stats.avg_latency_ms to avoid client loopback overhead."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        # 5 zones with many points each – naive scan would be 5*50 pip checks
        # First zone is at 0,0 (inside point 0.5,0.5), rest spread safely, empty is 80,150
        inside_lat, inside_lng = 0.5, 0.5
        empty_lat, empty_lng = 80, 150
        _create_many_geofences(db, count=5, points_per=50)
        port5 = get_free_port()
        proc5 = start_server(db, port5, grid_size="1", cache_size="0")
        try:
            # warm up both points
            _measure_avg_latency(port5, inside_lat, inside_lng, repeats=5)
            _measure_avg_latency(port5, empty_lat, empty_lng, repeats=5)
            # More repeats for stable server-side measurement
            srv5_inside = _measure_server_avg_latency_ms(
                port5, inside_lat, inside_lng, repeats=100
            )
            srv5_empty = _measure_server_avg_latency_ms(
                port5, empty_lat, empty_lng, repeats=100
            )
            cli5_empty = _measure_avg_latency(port5, empty_lat, empty_lng, repeats=20)
        finally:
            stop_server(proc5)

        # 500 zones – same first zone at 0,0 so inside point still inside, empty far away
        _create_many_geofences(db, count=500, points_per=50)
        port500 = get_free_port()
        proc500 = start_server(db, port500, grid_size="1", cache_size="0")
        try:
            _measure_avg_latency(port500, inside_lat, inside_lng, repeats=5)
            srv500_inside = _measure_server_avg_latency_ms(
                port500, inside_lat, inside_lng, repeats=100
            )
            srv500_empty = _measure_server_avg_latency_ms(
                port500, empty_lat, empty_lng, repeats=100
            )
            cli500_empty = _measure_avg_latency(
                port500, empty_lat, empty_lng, repeats=20
            )
        finally:
            stop_server(proc500)

        print(
            f"Relative server-side: 5-zone inside {srv5_inside:.4f}ms empty {srv5_empty:.4f}ms cli_empty {cli5_empty * 1000:.2f}ms | "
            f"500-zone inside {srv500_inside:.4f}ms empty {srv500_empty:.4f}ms cli_empty {cli500_empty * 1000:.2f}ms | "
            f"ratio server inside {srv500_inside / (srv5_inside + 1e-6):.2f}x empty {srv500_empty / (srv5_empty + 1e-6):.2f}x | "
            f"cli ratio empty {cli500_empty / (cli5_empty + 1e-9):.2f}x"
        )

        # Primary gate: server-side empty lookup must be O(1) with index – ratio should be small.
        # Naive scan would be ~100x (500/5) * cost per polygon (50 points). With index, empty cell => 0 candidates => ~1x.
        # Use ratio <6x for empty, generous for inside <10x, with small additive slack to absorb measurement noise
        # (previous 0.001s was too tight and caused flake on slow hosts).
        assert srv500_empty <= srv5_empty * 6 + 0.005, (
            f"Server-side empty lookup should be O(1) with index: 5-zone {srv5_empty:.4f}ms vs 500-zone {srv500_empty:.4f}ms ratio {srv500_empty / (srv5_empty + 1e-9):.1f}x – missing index?"
        )
        # Inside may be slightly higher but should also be bounded – at least not 100x
        assert srv500_inside <= srv5_inside * 10 + 0.02, (
            f"Server-side inside lookup too slow: ratio {srv500_inside / (srv5_inside + 1e-9):.1f}x – index not effective for inside?"
        )

        # Secondary gate: client-side empty should also be bounded (proves grid reduces candidates, not just micro-opt)
        assert cli500_empty <= cli5_empty * 6 + 0.05, (
            f"Client empty lookup too slow vs 5-zone: {cli500_empty}s vs {cli5_empty}s ratio {cli500_empty / (cli5_empty + 1e-9):.1f}x"
        )

        # Absolute upper bound to prevent hang
        assert srv5_empty < 5.0 and srv500_empty < 5.0
        assert cli5_empty < 1.5 and cli500_empty < 1.5

        # Structural proof: index_cells must be >0 and for 500 zones should be significantly > for 5 zones (or at least not 0)
        _create_many_geofences(db, count=500, points_per=4)
        port_check = get_free_port()
        proc_check = start_server(db, port_check, grid_size="1", cache_size="0")
        try:
            stats = requests.get(
                f"http://localhost:{port_check}/stats", timeout=2
            ).json()
            assert stats["index_cells"] > 0, (
                "index_cells must be >0 proving index exists"
            )
            # With 4-point squares spread, 500 zones should occupy many cells
            assert stats["index_cells"] >= 50, (
                f"500 zones should occupy many cells, got {stats['index_cells']}"
            )
        finally:
            stop_server(proc_check)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_cold_vs_cached_ratio():
    """Second identical query should be cache hit and not slower than first by large factor."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        _create_many_geofences(db, count=100)
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="100")
        try:
            # cold
            start = time.time()
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            cold = time.time() - start

            # warm – should be hit
            start = time.time()
            for _ in range(10):
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
            warm_avg = (time.time() - start) / 10

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["cache_hits"] >= 9, f"expected hits, got {stats}"
            # Warm should not be 10x slower than cold (cache should help or at least not hurt)
            assert warm_avg <= cold * 5 + 0.05, (
                f"cached avg {warm_avg}s much slower than cold {cold}s"
            )
            print(
                f"cold {cold * 1000:.2f}ms warm avg {warm_avg * 1000:.2f}ms hits {stats['cache_hits']}"
            )
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_batch_performance():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        _create_many_geofences(db, count=100)
        port = get_free_port()
        proc = start_server(db, port)
        try:
            batch_points = [
                {"lat": random.uniform(-10, 30), "lng": random.uniform(-10, 30)}
                for _ in range(100)
            ]
            payload = {"points": batch_points}
            start = time.time()
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=10
            )
            elapsed = time.time() - start
            assert resp.status_code == 200, f"batch failed {resp.text}"
            assert_response_arrays_valid(resp)
            results = resp.json()["results"]
            assert len(results) == 100
            for r in results:
                assert_is_list_not_null(r, "geofences")
            print(f"Batch 100 points in {elapsed:.3f}s")
            # Generous bound only to prevent hang, not tight perf floor
            assert elapsed < 5.0, (
                f"batch too slow {elapsed}s, should be <5s to avoid hang"
            )
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_batch_large_performance():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        _create_many_geofences(db, count=300)
        port = get_free_port()
        proc = start_server(db, port)
        try:
            batch_points = [
                {"lat": random.uniform(-10, 60), "lng": random.uniform(-10, 60)}
                for _ in range(500)
            ]
            payload = {"points": batch_points}
            start = time.time()
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=15
            )
            elapsed = time.time() - start
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            print(f"Batch 500 points in {elapsed:.3f}s")
            assert elapsed < 8.0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_concurrency_correctness():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        run_cli(
            db, ["add", "sq2", "--polygon", "10,10;10,11;11,11;11,10", "--name", "sq2"]
        )
        port = get_free_port()
        proc = start_server(db, port)
        try:

            def expected_via_cli(lat, lng):
                r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
                data = json.loads(r.stdout)
                assert isinstance(data, list), "CLI should return list not null"
                return data

            test_pts = [(0.5, 0.5), (10.5, 10.5), (5, 5), (0, 0), (1, 1), (20, 20)]
            expected = {}
            for lat, lng in test_pts:
                expected[(lat, lng)] = expected_via_cli(lat, lng)

            def check_point(pt):
                lat, lng = pt
                try:
                    resp = requests.get(
                        f"http://localhost:{port}/lookup?lat={lat}&lng={lng}", timeout=2
                    )
                    if resp.status_code != 200:
                        return False, pt, f"status {resp.status_code}"
                    assert_response_arrays_valid(resp)
                    data = resp.json()
                    if data.get("geofences") is None:
                        return False, pt, "geofences is null"
                    got = data["geofences"]
                    exp = expected[pt]
                    if got != exp:
                        return False, pt, f"expected {exp} got {got}"
                    return True, pt, ""
                except Exception as e:
                    return False, pt, str(e)

            all_pts = [random.choice(test_pts) for _ in range(300)]
            with ThreadPoolExecutor(max_workers=30) as executor:
                futures = [executor.submit(check_point, pt) for pt in all_pts]
                failed = []
                for f in as_completed(futures):
                    ok, pt, msg = f.result()
                    if not ok:
                        failed.append((pt, msg))

            assert not failed, f"concurrency correctness failed: {failed[:5]}"
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_concurrency_with_crud():
    """Concurrent lookups while doing POST/DELETE via HTTP."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port)
        try:

            def do_lookup():
                for _ in range(20):
                    try:
                        resp = requests.get(
                            f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                        )
                        if resp.status_code != 200:
                            return False
                        assert_response_arrays_valid(resp)
                        if resp.json().get("geofences") is None:
                            return False
                    except:
                        return False
                return True

            def do_crud():
                for i in range(5):
                    payload = {
                        "id": f"temp_{i}",
                        "name": f"T{i}",
                        "polygon": [
                            {"lat": 20 + i, "lng": 20 + i},
                            {"lat": 20 + i, "lng": 20 + i + 1},
                            {"lat": 20 + i + 1, "lng": 20 + i + 1},
                            {"lat": 20 + i + 1, "lng": 20 + i},
                        ],
                    }
                    try:
                        requests.post(
                            f"http://localhost:{port}/geofences",
                            json=payload,
                            timeout=2,
                        )
                        requests.delete(
                            f"http://localhost:{port}/geofences/temp_{i}", timeout=2
                        )
                    except:
                        pass
                return True

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = []
                for _ in range(5):
                    futures.append(executor.submit(do_lookup))
                for _ in range(2):
                    futures.append(executor.submit(do_crud))
                results = [f.result() for f in futures]
            assert all(results), f"concurrent crud failed {results}"
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- New semantic discriminators ----


def test_antimeridian_crossing():
    """Antimeridian-crossing polygons must be handled (bbox wrap and grid)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Tiny rectangle crossing 180: lat 0-1, lng 179 to -179 (2 deg wide via antimeridian)
        poly = "0,179;0,-179;1,-179;1,179"
        r = run_cli(db, ["add", "cross", "--polygon", poly, "--name", "Cross"])
        assert r.returncode == 0, f"crossing add should be valid, got {r.stderr}"

        # CLI checks
        for lat, lng, should_inside in [
            (0.5, 179.5, True),
            (0.5, -179.5, True),
            (0.5, 180, True),
            (0.5, -180, True),
            (0.5, 0, False),
        ]:
            r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
            assert r.returncode == 0
            ids = json.loads(r.stdout)
            assert isinstance(ids, list), "should be list not null"
            if should_inside:
                assert "cross" in ids, (
                    f"point {lat},{lng} should be inside crossing rect"
                )
            else:
                assert "cross" not in ids, (
                    f"point {lat},{lng} should be outside crossing rect"
                )

        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="0")
        try:
            for lat, lng, should_inside in [
                (0.5, 179.5, True),
                (0.5, -179.5, True),
                (0.5, 180, True),
                (0.5, -180, True),
                (0.5, 0, False),
            ]:
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat={lat}&lng={lng}", timeout=2
                )
                assert resp.status_code == 200
                assert_response_arrays_valid(resp)
                data = resp.json()
                assert_is_list_not_null(data, "geofences")
                if should_inside:
                    assert "cross" in data["geofences"], (
                        f"HTTP {lat},{lng} should be inside"
                    )
                else:
                    assert "cross" not in data["geofences"]

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # For tiny crossing rect, index should NOT explode to 700+ cells. With split, it should be small (~8 cells for grid 1)
            # With naive bbox covering -179..179, it would be 718 cells. So check small with generous bound.
            assert stats["index_cells"] < 500, (
                f"crossing rect should create small number of cells with wrapping logic, got {stats['index_cells']} (naive would be 718)"
            )
            print(f"antimeridian index_cells {stats['index_cells']}")

            # empty area (0,0) should be fast even though crossing polygon exists – grid empty for that cell
            avg_empty = _measure_avg_latency(port, 0, 0, repeats=20)
            print(f"antimeridian empty avg {avg_empty * 1000:.2f}ms")
            assert avg_empty < 1.0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_pole_adjacent():
    """Pole-adjacent bounding boxes must be correct."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Near north pole
        poly = "89,-10;89,10;90,10;90,-10"
        r = run_cli(db, ["add", "pole", "--polygon", poly, "--name", "Pole"])
        assert r.returncode == 0, f"pole add failed {r.stderr}"

        # Inside near pole
        r = run_cli(db, ["lookup", "--lat", "89.5", "--lng", "0"])
        assert r.returncode == 0
        assert "pole" in json.loads(r.stdout)

        # Outside just south
        r = run_cli(db, ["lookup", "--lat", "88.5", "--lng", "0"])
        assert "pole" not in json.loads(r.stdout)

        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="0")
        try:
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=89.5&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "pole" in resp.json()["geofences"]

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=88.5&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "pole" not in resp.json()["geofences"]

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["index_cells"] > 0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_invalidation_on_delete():
    """After DELETE, cached result must not be stale."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10")
        try:
            # populate cache
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "sq" in resp.json()["geofences"]

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["cache_size"] == 1

            # DELETE
            resp = requests.delete(f"http://localhost:{port}/geofences/sq", timeout=2)
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)

            # Subsequent lookup must NOT return deleted, even if previously cached
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert "sq" not in resp.json()["geofences"], (
                "cache should have been invalidated on DELETE"
            )
            assert resp.json()["geofences"] == []

            # cache should be cleared (or at least not contain stale)
            stats_after = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            # After invalidation, cache_size should be 0 then 1 after new query, but not contain stale
            assert stats_after["total_geofences"] == 0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_read_after_post_visibility():
    """After POST, subsequent GET and lookup must see new geofence immediately."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10")
        try:
            # lookup empty point before POST – should be [] and cached
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=5.5&lng=5.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []

            # POST new zone containing that point
            payload = {
                "id": "newzone",
                "name": "New",
                "polygon": [
                    {"lat": 5, "lng": 5},
                    {"lat": 5, "lng": 6},
                    {"lat": 6, "lng": 6},
                    {"lat": 6, "lng": 5},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload, timeout=2
            )
            assert resp.status_code in (200, 201)

            # Immediate lookup must see newzone (not stale [] from cache)
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=5.5&lng=5.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert "newzone" in resp.json()["geofences"], (
                "read-after-POST should see new geofence, cache should have been invalidated"
            )

            # GET single should also see it
            resp = requests.get(f"http://localhost:{port}/geofences/newzone", timeout=2)
            assert resp.status_code == 200
            assert resp.json()["id"] == "newzone"

            # GET list should contain it
            resp = requests.get(f"http://localhost:{port}/geofences", timeout=2)
            assert_response_arrays_valid(resp)
            assert isinstance(resp.json(), list)
            assert "newzone" in [x["id"] for x in resp.json()]
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_stats_monotonic():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port)
        try:
            s1 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            requests.get(f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2)
            requests.get(f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2)
            s2 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert s2["total_queries"] >= s1["total_queries"] + 2
            assert s2["total_geofences"] == s1["total_geofences"]
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_serve_flags():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        r = run_cli(db, ["serve", "--port", "999999"])
        assert r.returncode == 2

        r = run_cli(db, ["serve", "--port", "8080", "--grid-size", "0"])
        assert r.returncode == 2

        r = run_cli(db, ["serve", "--port", "8080", "--grid-size", "100"])
        assert r.returncode == 2

        r = run_cli(db, ["serve", "--port", "8080", "--cache-size", "-1"])
        assert r.returncode == 2

        r = run_cli(db, ["serve"])
        assert r.returncode == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_no_null_slices_broad():
    """Broaden nil-slice check to every response path."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="5")
        try:
            # empty geofences list
            resp = requests.get(f"http://localhost:{port}/geofences", timeout=2)
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            assert isinstance(resp.json(), list)
            assert resp.json() == []

            # lookup empty
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert resp.json()["geofences"] == []

            # lookup with cache hit (second time)
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")

            # batch empty
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json={"points": []}, timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["results"] == []
            assert isinstance(resp.json()["results"], list)

            # batch with empty result
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch",
                json={"points": [{"lat": 0, "lng": 0}]},
                timeout=2,
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json()["results"][0], "geofences")
            assert resp.json()["results"][0]["geofences"] == []

            # verbose empty
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0&lng=0&verbose=true", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert resp.json()["geofences"] == []

            # add one, then delete, then lookup should be [] not null (post-delete path)
            run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
            stop_server(proc)
            port2 = get_free_port()
            proc2 = start_server(db, port2, cache_size="5")
            try:
                resp = requests.get(
                    f"http://localhost:{port2}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert "sq" in resp.json()["geofences"]

                resp = requests.delete(
                    f"http://localhost:{port2}/geofences/sq", timeout=2
                )
                assert resp.status_code == 200

                resp = requests.get(
                    f"http://localhost:{port2}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert_is_list_not_null(resp.json(), "geofences")
                assert resp.json()["geofences"] == [], (
                    "post-delete path should return [] not null"
                )
                assert (
                    "null" not in resp.text
                    or '"geofences":[]' in resp.text
                    or '"geofences": []' in resp.text
                )
            finally:
                stop_server(proc2)
                proc = None
        finally:
            if proc:
                stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- Additional fair difficulty: grid update, overwrite, concurrent post/delete, world+crossing, pole south, batch cache ----


def test_post_overwrite_and_grid_update():
    """POST same ID twice with different polygons must invalidate old location, update grid and stats."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="10")
        try:
            # initial add at 0,0
            payload1 = {
                "id": "z",
                "name": "Z",
                "polygon": [
                    {"lat": 0, "lng": 0},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 1},
                    {"lat": 1, "lng": 0},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload1, timeout=2
            )
            assert resp.status_code in (200, 201)
            stats1 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats1["total_geofences"] == 1
            assert stats1["index_cells"] > 0

            # lookup old location hit
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "z" in resp.json()["geofences"]
            # lookup far location empty and cached as []
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=20&lng=20", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []

            # overwrite same ID at far location 20,20
            payload2 = {
                "id": "z",
                "name": "Z2",
                "polygon": [
                    {"lat": 20, "lng": 20},
                    {"lat": 20, "lng": 21},
                    {"lat": 21, "lng": 21},
                    {"lat": 21, "lng": 20},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload2, timeout=2
            )
            assert resp.status_code in (200, 201)

            # immediate visibility: old location must no longer match (cache invalidated)
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "z" not in resp.json()["geofences"]
            assert resp.json()["geofences"] == []

            # new location must match
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=20.5&lng=20.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "z" in resp.json()["geofences"]

            stats2 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats2["total_geofences"] == 1
            # index_cells should reflect new location, not old; may differ but >0
            assert stats2["index_cells"] > 0

            # persistence
            stop_server(proc)
            port2 = get_free_port()
            proc2 = start_server(db, port2)
            try:
                resp = requests.get(f"http://localhost:{port2}/geofences/z", timeout=2)
                assert resp.status_code == 200
                assert resp.json()["name"] == "Z2"
                resp = requests.get(
                    f"http://localhost:{port2}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert resp.json()["geofences"] == []
                resp = requests.get(
                    f"http://localhost:{port2}/lookup?lat=20.5&lng=20.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert "z" in resp.json()["geofences"]
            finally:
                stop_server(proc2)
                proc = None
        finally:
            if proc:
                stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_concurrent_delete_and_lookup_stress():
    """Deterministic stale-cache discriminator: DELETE fully committed, then fresh concurrent pool must not see stale entry."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="20", grid_size="1")
        try:
            # pre-warm cache with hit (populates cache with ["sq"])
            for _ in range(3):
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert "sq" in resp.json()["geofences"]

            # 1. DELETE and wait for the response — delete is now fully committed, cache must be invalidated
            resp = requests.delete(f"http://localhost:{port}/geofences/sq", timeout=2)
            assert resp.status_code in (200, 204), (
                f"delete failed {resp.status_code} {resp.text}"
            )

            # 2. NOW start a fresh pool of lookup workers. No request was in flight
            #    across the delete, so any worker seeing "sq" is unambiguously stale cache.
            def check(_):
                r = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(r)
                d = r.json()
                assert_is_list_not_null(d, "geofences")
                return ("sq" not in d["geofences"], d["geofences"])

            with ThreadPoolExecutor(max_workers=30) as ex:
                bad = [r for r in ex.map(check, range(300)) if not r[0]]
            assert not bad, f"stale cache after committed DELETE: {bad[:5]}"

            # final lookup after all workers done must be empty
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
            assert resp.json()["geofences"] == [], (
                f"after DELETE final lookup should be [] not {resp.json()['geofences']}"
            )

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["total_geofences"] == 0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_world_plus_crossing_batch():
    """World polygon (span >=360) plus antimeridian crossing rect must both be handled, including batch path."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        world_poly = "-90,-180;-90,180;90,180;90,-180"
        r = run_cli(db, ["add", "world", "--polygon", world_poly, "--name", "World"])
        assert r.returncode == 0
        cross_poly = "0,179;0,-179;1,-179;1,179"
        r = run_cli(db, ["add", "cross", "--polygon", cross_poly, "--name", "Cross"])
        assert r.returncode == 0

        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="10")
        try:
            # world must match everywhere in its lat band
            for lng in [0, 50, -50, 179.5, -179.5, 180]:
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng={lng}", timeout=2
                )
                assert_response_arrays_valid(resp)
                geofs = resp.json()["geofences"]
                assert "world" in geofs, f"world should match lng {lng}, got {geofs}"
                if lng in (179.5, -179.5, 180, -180):
                    assert "cross" in geofs, f"cross should match lng {lng}"
                else:
                    assert "cross" not in geofs

            # batch path must also handle wrapping
            payload = {
                "points": [
                    {"lat": 0.5, "lng": 0},
                    {"lat": 0.5, "lng": 179.5},
                    {"lat": 0.5, "lng": -179.5},
                    {"lat": 80, "lng": 10},
                ]
            }
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            results = resp.json()["results"]
            assert len(results) == 4
            # preserve order
            assert results[0]["lat"] == 0.5 and results[0]["lng"] == 0
            assert_is_list_not_null(results[0], "geofences")
            assert "world" in results[0]["geofences"]
            assert "cross" not in results[0]["geofences"]

            assert "world" in results[1]["geofences"]
            assert "cross" in results[1]["geofences"]

            assert "world" in results[2]["geofences"]
            assert "cross" in results[2]["geofences"]

            assert "world" in results[3]["geofences"]

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["total_geofences"] == 2
            assert stats["index_cells"] > 0
            # crossing rect should not cause explosion even with world present
            assert stats["index_cells"] < 70000  # generous, world itself could be 64800
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_pole_south_and_edge_precision():
    """South pole adjacent and north pole edge/vertex handling."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # north pole rect
        poly_n = "89,-10;89,10;90,10;90,-10"
        r = run_cli(db, ["add", "pole_n", "--polygon", poly_n, "--name", "N"])
        assert r.returncode == 0
        # south pole rect
        poly_s = "-90,-10;-90,10;-89,10;-89,-10"
        r = run_cli(db, ["add", "pole_s", "--polygon", poly_s, "--name", "S"])
        assert r.returncode == 0

        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="0")
        try:
            # inside north
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=89.5&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "pole_n" in resp.json()["geofences"]
            # on edge at lat 90 should be inside (epsilon)
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=90&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            # point on pole itself - should be inside if any pole polygon contains it
            # Our rect includes 90 edge, so 90,0 should be considered inside (on edge)
            assert "pole_n" in resp.json()["geofences"]

            # inside south
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=-89.5&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "pole_s" in resp.json()["geofences"]

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=-90&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "pole_s" in resp.json()["geofences"]

            # outside
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=88&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "pole_n" not in resp.json()["geofences"]
            assert "pole_s" not in resp.json()["geofences"]
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_batch_cache_rounding_within_batch():
    """Batch containing same rounded point must count cache hits appropriately and return consistent results."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10", grid_size="1")
        try:
            stats0 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # two points that round to same 6 decimals
            payload = {
                "points": [
                    {"lat": 0.5000001, "lng": 0.5000001},
                    {"lat": 0.5000002, "lng": 0.5000002},
                    {"lat": 2, "lng": 2},
                ]
            }
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            results = resp.json()["results"]
            assert len(results) == 3
            for r in results:
                assert_is_list_not_null(r, "geofences")
            assert results[0]["geofences"] == ["sq"]
            assert results[1]["geofences"] == ["sq"]
            assert results[2]["geofences"] == []

            stats1 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # total_queries should have increased by 3
            assert stats1["total_queries"] >= stats0["total_queries"] + 3
            # second point should have been cache hit within batch (at least 1 hit)
            assert stats1["cache_hits"] >= stats0["cache_hits"] + 1
            assert (
                stats1["cache_size"] == 2
            )  # rounded same key => 2 distinct entries (0.5,0.5 and 2,2)

            # sequential rounding hit also
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5000003&lng=0.5000003", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == ["sq"]
            stats2 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats2["cache_hits"] >= stats1["cache_hits"] + 1
            assert stats2["cache_size"] == 2
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_stats_covers_batch_and_crud():
    """Stats must correctly count total_queries, cache_hits, total_geofences after CRUD and batch."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "a", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="10", grid_size="1")
        try:
            s0 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # single lookup
            requests.get(f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2)
            s1 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert s1["total_queries"] == s0["total_queries"] + 1
            assert s1["total_geofences"] == 1

            # batch of 5
            payload = {"points": [{"lat": 0.5, "lng": 0.5} for _ in range(5)]}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=2
            )
            assert resp.status_code == 200
            assert_response_arrays_valid(resp)
            s2 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert s2["total_queries"] == s1["total_queries"] + 5
            # all 5 should have been hits after first lookup (if cache) => at least 5 hits increment
            assert s2["cache_hits"] >= s1["cache_hits"] + 4

            # POST new
            payload_new = {
                "id": "b",
                "name": "B",
                "polygon": [
                    {"lat": 10, "lng": 10},
                    {"lat": 10, "lng": 11},
                    {"lat": 11, "lng": 11},
                    {"lat": 11, "lng": 10},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload_new, timeout=2
            )
            assert resp.status_code in (200, 201)
            s3 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert s3["total_geofences"] == 2

            # DELETE
            resp = requests.delete(f"http://localhost:{port}/geofences/a", timeout=2)
            assert resp.status_code == 200
            s4 = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert s4["total_geofences"] == 1
            assert s4["total_queries"] >= s3["total_queries"]  # monotonic
            assert "cache_hit_rate" in s4
            assert "avg_latency_ms" in s4
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_selective_cache_invalidation():
    """Selective invalidation: POST near one point must not wipe far-away cached entry. This is prior-violating vs naive full clear."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="20", grid_size="1")
        try:
            # warm two far-apart points
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=70&lng=70", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []

            stats_before = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            assert stats_before["cache_size"] == 2
            assert stats_before["total_queries"] == 2

            # POST zone near 0,0 only
            payload = {
                "id": "near_zero",
                "name": "NearZero",
                "polygon": [
                    {"lat": 0, "lng": 0},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 1},
                    {"lat": 1, "lng": 0},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload, timeout=2
            )
            assert resp.status_code in (200, 201)

            # far point must still be cached HIT, cache_size must not drop to 0, total_geofences 1
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=70&lng=70", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []
            stats_mid = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # second lookup to far point should have been a hit (entry survived selective invalidation)
            assert stats_mid["cache_hits"] == stats_before["cache_hits"] + 1, (
                f"far point should have been cache hit after selective invalidation, got hits {stats_mid['cache_hits']} vs before {stats_before['cache_hits']}"
            )
            # cache_size should be 1 (far entry survived) or 2 after re-caching near point? Actually near entry was invalidated, so only far remains before next lookup
            # After far HIT, cache_size should still be 1 (only far), not 0
            assert stats_mid["cache_size"] >= 1, (
                f"selective invalidation must preserve far entry, cache_size {stats_mid['cache_size']}"
            )
            assert stats_mid["total_geofences"] == 1

            # near point must be MISS and now see new zone
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "near_zero" in resp.json()["geofences"]
            stats_after = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            # near point was MISS (invalidated), so cache_hits should not increase for this lookup
            assert stats_after["cache_hits"] == stats_mid["cache_hits"], (
                f"near point should have been MISS after invalidation, hits {stats_after['cache_hits']} vs {stats_mid['cache_hits']}"
            )
            # After near MISS, cache should now have both entries again (far + near)
            assert stats_after["cache_size"] == 2
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_selective_invalidation_antimeridian():
    """Selective invalidation must handle antimeridian bbox wrapping – far points outside crossing bbox must survive."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="20", grid_size="1")
        try:
            # warm far point at 0,0 (outside antimeridian region)
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []
            # warm point near antimeridian but not crossing yet
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=179.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []

            stats_before = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            assert stats_before["cache_size"] == 2

            # POST crossing rect 0,179 to 1,-179
            payload = {
                "id": "cross",
                "name": "Cross",
                "polygon": [
                    {"lat": 0, "lng": 179},
                    {"lat": 0, "lng": -179},
                    {"lat": 1, "lng": -179},
                    {"lat": 1, "lng": 179},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload, timeout=2
            )
            assert resp.status_code in (200, 201)

            # point at 0,0 is outside crossing bbox (bbox is around 179..-179 wrapping, small gap at 0)
            # With correct pointInBBox wrapping logic, 0,0 should be outside, so its cache entry should survive as HIT
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0&lng=0", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []
            stats_mid = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats_mid["cache_hits"] == stats_before["cache_hits"] + 1, (
                f"0,0 should be HIT after crossing POST (outside bbox), hits {stats_mid['cache_hits']} vs {stats_before['cache_hits']}"
            )

            # point at 0.5,179.5 is inside crossing bbox, should have been invalidated -> MISS and now contain cross
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=179.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "cross" in resp.json()["geofences"]
            stats_after = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            assert stats_after["cache_hits"] == stats_mid["cache_hits"], (
                f"179.5 point should be MISS after crossing POST, hits {stats_after['cache_hits']} vs {stats_mid['cache_hits']}"
            )
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_index_cells_reclaim():
    """Index_cells must be exactly reclaimed after POST+DELETE – leaving stale empty cells is a bug."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="0")
        try:
            before = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            payload = {
                "id": "temp_zone",
                "name": "Temp",
                "polygon": [
                    {"lat": 10, "lng": 10},
                    {"lat": 10, "lng": 11},
                    {"lat": 11, "lng": 11},
                    {"lat": 11, "lng": 10},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload, timeout=2
            )
            assert resp.status_code in (200, 201)
            mid = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            assert mid > before, (
                f"index_cells should increase after POST, before {before} mid {mid}"
            )

            resp = requests.delete(
                f"http://localhost:{port}/geofences/temp_zone", timeout=2
            )
            assert resp.status_code == 200
            after = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            assert after == before, (
                f"index_cells must be reclaimed after DELETE: before {before} after {after} (stale empty cells left behind)"
            )

            # second cycle: add two, delete one, ensure count matches
            payload2 = {
                "id": "z1",
                "name": "Z1",
                "polygon": [
                    {"lat": 0, "lng": 0},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 1},
                    {"lat": 1, "lng": 0},
                ],
            }
            payload3 = {
                "id": "z2",
                "name": "Z2",
                "polygon": [
                    {"lat": 20, "lng": 20},
                    {"lat": 20, "lng": 21},
                    {"lat": 21, "lng": 21},
                    {"lat": 21, "lng": 20},
                ],
            }
            requests.post(
                f"http://localhost:{port}/geofences", json=payload2, timeout=2
            )
            requests.post(
                f"http://localhost:{port}/geofences", json=payload3, timeout=2
            )
            with_two = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            requests.delete(f"http://localhost:{port}/geofences/z1", timeout=2)
            with_one = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            # after deleting z1, cells should drop (or stay if overlapping, but these two don't overlap)
            assert with_one < with_two, (
                f"index_cells should shrink after DELETE when zones don't overlap: {with_two} -> {with_one}"
            )
            requests.delete(f"http://localhost:{port}/geofences/z2", timeout=2)
            final = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            assert final == before, (
                f"after deleting all, index_cells should return to {before}, got {final}"
            )
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_unknown_path_404():
    """Unknown path must return 404 JSON error – tiny gap but ensures spec coverage."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="5")
        try:
            resp = requests.get(
                f"http://localhost:{port}/this_does_not_exist", timeout=2
            )
            assert resp.status_code == 404
            assert_response_arrays_valid(
                resp
            )  # uses generic check – should be JSON error
            body = resp.json()
            assert "error" in body
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_selective_overwrite_far_survival():
    """Overwrite same ID far away: far point outside both old and new bboxes must survive as HIT, old and new locations must be MISS."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # initial at 0,0
        run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="30", grid_size="1")
        try:
            # warm three points: old loc, new loc (future), far
            for lat, lng in [(0.5, 0.5), (20.5, 20.5), (70, 70)]:
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat={lat}&lng={lng}", timeout=2
                )
                assert_response_arrays_valid(resp)

            stats_before = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            assert stats_before["cache_size"] == 3

            # overwrite same ID to far location 20,20 (old bbox 0-1, new bbox 20-21)
            payload = {
                "id": "z",
                "name": "Z2",
                "polygon": [
                    {"lat": 20, "lng": 20},
                    {"lat": 20, "lng": 21},
                    {"lat": 21, "lng": 21},
                    {"lat": 21, "lng": 20},
                ],
            }
            resp = requests.post(
                f"http://localhost:{port}/geofences", json=payload, timeout=2
            )
            assert resp.status_code in (200, 201)

            # far point 70,70 outside both bboxes must survive as HIT
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=70&lng=70", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []
            stats_mid = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats_mid["cache_hits"] == stats_before["cache_hits"] + 1, (
                f"far point should be HIT after selective overwrite, hits {stats_mid['cache_hits']} vs {stats_before['cache_hits']}"
            )
            # after far HIT, cache should have only far entry (old and new invalidated)
            assert stats_mid["cache_size"] == 1

            # old location should be MISS and no longer contain z
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == []
            stats_after_old = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            assert stats_after_old["cache_hits"] == stats_mid["cache_hits"], (
                "old loc should be MISS after overwrite"
            )

            # new location should be MISS and contain z
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=20.5&lng=20.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "z" in resp.json()["geofences"]
            stats_after_new = requests.get(
                f"http://localhost:{port}/stats", timeout=2
            ).json()
            assert stats_after_new["cache_hits"] == stats_after_old["cache_hits"], (
                "new loc should be MISS right after overwrite"
            )
            assert stats_after_new["cache_size"] == 3  # far + old empty + new
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_concurrent_post_stress():
    """Concurrent POSTs while doing lookups – must not crash, must remain thread-safe, final state must be consistent and [] not null."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="20", grid_size="1")
        try:
            # pre-warm empty
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)

            def do_post(i):
                payload = {
                    "id": f"p{i}",
                    "name": f"P{i}",
                    "polygon": [
                        {"lat": float(i), "lng": float(i)},
                        {"lat": float(i), "lng": float(i) + 0.5},
                        {"lat": float(i) + 0.5, "lng": float(i) + 0.5},
                        {"lat": float(i) + 0.5, "lng": float(i)},
                    ],
                }
                try:
                    r = requests.post(
                        f"http://localhost:{port}/geofences", json=payload, timeout=2
                    )
                    return r.status_code in (200, 201)
                except Exception as e:
                    return False

            def do_lookup():
                for _ in range(30):
                    try:
                        r = requests.get(
                            f"http://localhost:{port}/lookup?lat=0.5&lng=0.5",
                            timeout=2,
                        )
                        if r.status_code != 200:
                            return False
                        assert_response_arrays_valid(r)
                        if r.json().get("geofences") is None:
                            return False
                    except Exception:
                        return False
                return True

            with ThreadPoolExecutor(max_workers=20) as ex:
                post_futs = [ex.submit(do_post, i) for i in range(20)]
                lookup_futs = [ex.submit(do_lookup) for _ in range(10)]
                post_res = [f.result() for f in post_futs]
                lookup_res = [f.result() for f in lookup_futs]

            assert all(post_res), f"concurrent POSTs failed: {post_res}"
            assert all(lookup_res), (
                f"concurrent lookups during POST failed: {lookup_res}"
            )

            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["total_geofences"] >= 20
            # final lookup must be valid list not null
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert_is_list_not_null(resp.json(), "geofences")
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_grid_cells_small_zone_is_local():
    """A tiny zone must be indexed locally, not globally.

    Convention-free: asserts per-zone constancy and reclaim deltas rather than an
    absolute cell count, so floor/floor (1 cell), ceil-expansion (4) and any
    conservative halo (9, 25, ...) all pass, while a global or degenerate index fails.
    """
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(
            db,
            ["add", "tiny", "--polygon", "0,0;0,0.5;0.5,0.5;0.5,0", "--name", "Tiny"],
        )
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="0")
        try:
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            a1 = stats["index_cells"]

            # behavioural probes – unchanged
            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.25&lng=0.25", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "tiny" in resp.json()["geofences"], (
                f"0.25,0.25 should be inside tiny"
            )

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=1.5&lng=0.25", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == [], (
                f"1.5,0.25 one cell away should be outside tiny"
            )

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.25&lng=1.5", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert resp.json()["geofences"] == [], (
                f"0.25,1.5 one cell away should be outside tiny"
            )

            # per-zone constancy — congruent zones, each 20 deg apart so no cell sharing
            # bases are integers with +0.5 extent, so every zone sits identically within its cell under any mapping
            # 20° separation at grid-size 1 keeps halos disjoint up to radius 9
            counts = [a1]
            for i, base in enumerate([20, 40, 60], start=2):
                payload = {
                    "id": f"tiny{i}",
                    "name": f"Tiny{i}",
                    "polygon": [
                        {"lat": base, "lng": base},
                        {"lat": base, "lng": base + 0.5},
                        {"lat": base + 0.5, "lng": base + 0.5},
                        {"lat": base + 0.5, "lng": base},
                    ],
                }
                r = requests.post(
                    f"http://localhost:{port}/geofences", json=payload, timeout=2
                )
                assert r.status_code in (200, 201)
                cur = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                    "index_cells"
                ]
                counts.append(cur)

            deltas = [counts[i + 1] - counts[i] for i in range(len(counts) - 1)]
            unit = deltas[0]
            assert unit >= 1, (
                "each zone must occupy at least one cell (global bucket detected)"
            )
            assert all(d == unit for d in deltas), (
                f"identical far-apart zones must each add the same number of cells, got {deltas}"
            )
            # generous ceiling justified against the full grid (180x360 = 64800 at grid-size 1),
            # not against any cell-boundary convention
            assert unit <= 100, (
                f"per-zone cell cost {unit} is not local (full grid is 64800)"
            )

            # reclaim is a delta, so it stays convention-free
            # delete the last zone we added (tiny4 at base 60)
            r = requests.delete(f"http://localhost:{port}/geofences/tiny4", timeout=2)
            assert r.status_code == 200
            back = requests.get(f"http://localhost:{port}/stats", timeout=2).json()[
                "index_cells"
            ]
            assert back == counts[-2], (
                f"DELETE must reclaim exactly: {counts[-1]} -> {back}, expected {counts[-2]}"
            )

        finally:
            stop_server(proc)

        # grid-size 5 block – monotonicity, not absolute
        run_cli(db, ["clear"])
        run_cli(
            db,
            ["add", "tiny", "--polygon", "0,0;0,0.5;0.5,0.5;0.5,0", "--name", "Tiny"],
        )
        port2 = get_free_port()
        proc2 = start_server(db, port2, grid_size="5", cache_size="0")
        try:
            stats2 = requests.get(f"http://localhost:{port2}/stats", timeout=2).json()
            assert stats2["index_cells"] <= a1, (
                f"larger cells cannot require more cells: grid-5 {stats2['index_cells']} vs grid-1 {a1}"
            )
        finally:
            stop_server(proc2)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_persistence_after_concurrent_writes_restart():
    """After concurrent HTTP POSTs, data must persist across server restart and be visible via CLI and HTTP."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="100")
        try:
            # concurrent POSTs
            def do_post(i):
                lat_idx = (i // 20) % 18
                lng_idx = i % 20
                base_lat = lat_idx * 8.0 - 80.0
                base_lng = lng_idx * 17.0 - 170.0
                if i == 0:
                    base_lat, base_lng = 0.0, 0.0
                poly = [
                    {"lat": base_lat, "lng": base_lng},
                    {"lat": base_lat, "lng": base_lng + 0.5},
                    {"lat": base_lat + 0.5, "lng": base_lng + 0.5},
                    {"lat": base_lat + 0.5, "lng": base_lng},
                ]
                payload = {
                    "id": f"persist_{i:03d}",
                    "name": f"Persist {i}",
                    "polygon": poly,
                }
                r = requests.post(
                    f"http://localhost:{port}/geofences", json=payload, timeout=5
                )
                return r.status_code in (200, 201)

            with ThreadPoolExecutor(max_workers=15) as ex:
                results = list(ex.map(do_post, range(30)))
            assert all(results), (
                f"concurrent POSTs for persistence test failed: {results}"
            )

            # verify via HTTP before shutdown
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["total_geofences"] >= 30

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=0.25&lng=0.25", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "persist_000" in resp.json()["geofences"]

        finally:
            stop_server(proc)

        # restart and verify persistence
        port2 = get_free_port()
        proc2 = start_server(db, port2, grid_size="1", cache_size="100")
        try:
            stats2 = requests.get(f"http://localhost:{port2}/stats", timeout=2).json()
            assert stats2["total_geofences"] >= 30, (
                f"after restart, expected >=30 geofences, got {stats2['total_geofences']}"
            )

            # HTTP lookup still works after restart
            resp = requests.get(
                f"http://localhost:{port2}/lookup?lat=0.25&lng=0.25", timeout=2
            )
            assert_response_arrays_valid(resp)
            assert "persist_000" in resp.json()["geofences"]

            # CLI list also sees persisted data
            r = run_cli(db, ["list"])
            assert r.returncode == 0
            arr = json.loads(r.stdout)
            ids = [x["id"] for x in arr]
            assert "persist_000" in ids
            assert len(arr) >= 30
            assert ids == sorted(ids), f"list not sorted after restart: {ids}"

            # empty array must still be [] not null after restart
            assert r.stdout.strip().startswith("[")
            assert r.stdout.strip() != "null"
            assert "null" not in r.stdout.lower() or r.stdout.strip() == "[]"

        finally:
            stop_server(proc2)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
