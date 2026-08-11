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


def _create_many_geofences(db, count=100):
    run_cli(db, ["clear"])
    for i in range(count):
        base_lat = (i // 10) * 2.0
        base_lng = (i % 10) * 2.0
        poly = f"{base_lat},{base_lng};{base_lat},{base_lng + 0.8};{base_lat + 0.8},{base_lng + 0.8};{base_lat + 0.8},{base_lng}"
        run_cli(db, ["add", f"zone_{i:03d}", "--polygon", poly, "--name", f"Zone {i}"])


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


def test_relative_index_performance():
    """500-zone lookup latency should be small multiple of 5-zone latency (proves index)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        # 5 zones
        _create_many_geofences(db, count=5)
        port5 = get_free_port()
        proc5 = start_server(db, port5, grid_size="1", cache_size="0")
        try:
            # warm up
            _measure_avg_latency(port5, 0.5, 0.5, repeats=5)
            avg5_inside = _measure_avg_latency(port5, 0.5, 0.5, repeats=20)
            avg5_empty = _measure_avg_latency(port5, 80, 150, repeats=20)
        finally:
            stop_server(proc5)

        # 500 zones – same first zone at 0,0 so inside point same
        _create_many_geofences(db, count=500)
        port500 = get_free_port()
        proc500 = start_server(db, port500, grid_size="1", cache_size="0")
        try:
            _measure_avg_latency(port500, 0.5, 0.5, repeats=5)
            avg500_inside = _measure_avg_latency(port500, 0.5, 0.5, repeats=20)
            avg500_empty = _measure_avg_latency(port500, 80, 150, repeats=20)
        finally:
            stop_server(proc500)

        print(
            f"Relative: 5-zone inside {avg5_inside * 1000:.2f}ms empty {avg5_empty * 1000:.2f}ms | "
            f"500-zone inside {avg500_inside * 1000:.2f}ms empty {avg500_empty * 1000:.2f}ms | "
            f"ratio inside {avg500_inside / (avg5_inside + 1e-6):.2f}x empty {avg500_empty / (avg5_empty + 1e-6):.2f}x"
        )

        # With index, 500 should be within ~5x of 5 (plus small absolute slack for noise)
        # Naive without index would be ~100x (500/5)
        # Use generous factor 8 to avoid flake, plus 0.02s slack for tiny absolute times
        assert avg500_inside <= avg5_inside * 8 + 0.05, (
            f"500-zone inside too slow vs 5-zone: {avg500_inside}s vs {avg5_inside}s ratio {avg500_inside / (avg5_inside + 1e-9):.1f}x, expected index to keep it small"
        )
        assert avg500_empty <= avg5_empty * 8 + 0.05, (
            f"500-zone empty too slow vs 5-zone: {avg500_empty}s vs {avg5_empty}s ratio {avg500_empty / (avg5_empty + 1e-9):.1f}x, expected index to make empty fast"
        )

        # Also ensure both absolute generous upper bound to prevent hangs, but not tight
        assert avg5_inside < 1.0 and avg500_inside < 1.0
        assert avg5_empty < 1.0 and avg500_empty < 1.0

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
    """Heavy concurrent DELETE + lookup must not return null and final state must be empty (tests cache invalidation, not in-flight overlap)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        port = get_free_port()
        proc = start_server(db, port, cache_size="20", grid_size="1")
        try:
            # pre-warm cache with hit
            for _ in range(3):
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                assert_response_arrays_valid(resp)
                assert "sq" in resp.json()["geofences"]

            def lookup_worker():
                for _ in range(50):
                    try:
                        resp = requests.get(
                            f"http://localhost:{port}/lookup?lat=0.5&lng=0.5",
                            timeout=2,
                        )
                        if resp.status_code != 200:
                            return False, f"status {resp.status_code}"
                        assert_response_arrays_valid(resp)
                        data = resp.json()
                        if data.get("geofences") is None:
                            return False, "null during concurrent"
                        # don't enforce stale after delete here – in-flight requests that started
                        # before delete may legitimately return sq after delete flag; final check is stricter
                    except Exception as e:
                        return False, str(e)
                return True, ""

            with ThreadPoolExecutor(max_workers=20) as ex:
                futures = [ex.submit(lookup_worker) for _ in range(20)]
                time.sleep(0.2)
                # delete while lookups in flight
                resp = requests.delete(
                    f"http://localhost:{port}/geofences/sq", timeout=2
                )
                assert resp.status_code == 200
                results = [f.result() for f in futures]

            # show all failures for debugging
            failed = [r for r in results if not r[0]]
            assert not failed, (
                f"concurrent delete stress failed: {failed[:5]} results={results[:5]}"
            )

            # final lookup after all workers done must be empty – tests cache invalidation
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
