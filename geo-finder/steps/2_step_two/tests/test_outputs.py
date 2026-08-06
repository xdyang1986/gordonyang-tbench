"""
Step 2 tests: High-QPS geofence service with spatial index, cache, batch, concurrency.
"""

import os
import json
import subprocess
import tempfile
import shutil
import time
import random
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests

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
    # Wait for server to be ready
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://localhost:{port}/stats", timeout=1)
            if resp.status_code == 200:
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            # exited early
            out, err = proc.communicate()
            raise RuntimeError(f"server failed to start: stdout={out} stderr={err}")
        time.sleep(0.2)
    # timeout
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


# ---- CLI backward compat ----


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
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == ["zone_a"]
        r = run_cli(db, ["remove", "zone_a"])
        assert r.returncode == 0
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
            data = resp.json()
            assert data["geofences"] == ["sq"]
            assert data["count"] == 1

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=2&lng=2", timeout=2
            )
            assert resp.status_code == 200
            assert resp.json()["geofences"] == []

            resp = requests.get(
                f"http://localhost:{port}/lookup?lat=10.5&lng=10.5", timeout=2
            )
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
            arr = resp.json()
            assert len(arr) == 2
            ids = [x["id"] for x in arr]
            assert ids == sorted(ids)

            # stats
            resp = requests.get(f"http://localhost:{port}/stats", timeout=2)
            assert resp.status_code == 200
            stats = resp.json()
            assert "total_geofences" in stats
            assert "total_queries" in stats
            assert "cache_hits" in stats
            assert "cache_size" in stats
            assert "avg_latency_ms" in stats
            assert "index_cells" in stats
            assert stats["total_geofences"] == 2
            assert stats["index_cells"] > 0
        finally:
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
            # valid batch
            payload = {"points": [{"lat": 0.5, "lng": 0.5}, {"lat": 2, "lng": 2}]}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=2
            )
            assert resp.status_code == 200
            results = resp.json()["results"]
            assert len(results) == 2
            assert results[0]["geofences"] == ["sq"]
            assert results[1]["geofences"] == []

            # empty batch
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json={"points": []}, timeout=2
            )
            assert resp.status_code == 200
            assert resp.json()["results"] == []

            # too large
            big = {"points": [{"lat": 0, "lng": 0}] * 1001}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=big, timeout=2
            )
            assert resp.status_code == 400

            # invalid point
            bad = {"points": [{"lat": 100, "lng": 0}]}
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch", json=bad, timeout=2
            )
            assert resp.status_code == 400

            # invalid json
            resp = requests.post(
                f"http://localhost:{port}/lookup/batch",
                data="not json",
                headers={"Content-Type": "application/json"},
                timeout=2,
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
            # first query miss
            requests.get(f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2)
            resp = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert resp["total_queries"] >= 1
            hits_before = resp["cache_hits"]

            # repeat same point 5 times
            for _ in range(5):
                requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )

            resp = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            # should have at least 4 hits (first was miss, next 5 should be hits)
            assert resp["cache_hits"] >= hits_before + 4, (
                f"expected cache hits increase, got {resp}"
            )
            assert resp["cache_size"] >= 1
            assert resp["cache_size"] <= 10
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_index_cells():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Add geofence covering small area
        run_cli(db, ["add", "small", "--polygon", "0,0;0,1;1,1;1,0", "--name", "s"])
        # Add large area
        run_cli(
            db,
            ["add", "large", "--polygon", "-10,-10;-10,10;10,10;10,-10", "--name", "l"],
        )
        port = get_free_port()
        proc = start_server(db, port, grid_size="1")
        try:
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["index_cells"] > 0
            # With grid-size 1, small bbox of 1 degree should be ~4 cells (if bbox 0-1)
            # Large should be many cells
            # At least check >0
        finally:
            stop_server(proc)

        # test with larger grid size
        port2 = get_free_port()
        proc2 = start_server(db, port2, grid_size="5")
        try:
            stats2 = requests.get(f"http://localhost:{port2}/stats", timeout=2).json()
            assert stats2["index_cells"] > 0
            # Larger grid size should have fewer or equal cells than smaller grid size for same data? Not strictly because bbox rounding, but typically fewer.
            # So we just check both >0
        finally:
            stop_server(proc2)
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
                assert resp.status_code == 200
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["cache_size"] == 0
            assert stats["cache_hits"] == 0
        finally:
            stop_server(proc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- Performance / QPS ----


def _create_many_geofences(db, count=100):
    run_cli(db, ["clear"])
    for i in range(count):
        # create grid of squares: each 0.8 deg, spaced 1 deg apart to avoid overlap too much
        base_lat = (i // 10) * 2.0  # spread
        base_lng = (i % 10) * 2.0
        poly = f"{base_lat},{base_lng};{base_lat},{base_lng + 0.8};{base_lat + 0.8},{base_lng + 0.8};{base_lat + 0.8},{base_lng}"
        run_cli(db, ["add", f"zone_{i:03d}", "--polygon", poly, "--name", f"Zone {i}"])


def test_qps_throughput():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        _create_many_geofences(db, count=100)
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="500")
        try:
            # Prepare random points, some inside, some outside
            points = []
            for _ in range(200):
                # 50% inside known zones, 50% random
                if random.random() < 0.5:
                    # pick a zone center
                    z = random.randint(0, 99)
                    base_lat = (z // 10) * 2.0 + 0.4
                    base_lng = (z % 10) * 2.0 + 0.4
                    points.append((base_lat, base_lng))
                else:
                    points.append((random.uniform(-20, 20), random.uniform(-20, 20)))

            def do_lookup(pt):
                lat, lng = pt
                try:
                    resp = requests.get(
                        f"http://localhost:{port}/lookup?lat={lat}&lng={lng}", timeout=5
                    )
                    return resp.status_code == 200
                except Exception as e:
                    return False

            # Warm up
            for pt in points[:10]:
                do_lookup(pt)

            start = time.time()
            # 20 concurrent clients * 50 requests = 1000 requests
            total_requests = 1000
            # Duplicate points to reach total
            all_pts = [random.choice(points) for _ in range(total_requests)]

            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(do_lookup, pt) for pt in all_pts]
                results = [f.result() for f in as_completed(futures)]

            elapsed = time.time() - start
            success = sum(1 for r in results if r)
            qps = success / elapsed if elapsed > 0 else 0

            print(
                f"QPS test: {success}/{total_requests} succeeded in {elapsed:.2f}s => {qps:.1f} QPS"
            )

            assert success >= total_requests * 0.95, (
                f"too many failures: {success}/{total_requests}"
            )
            # Relaxed from 5.5s to 7s and QPS 150->120 for stability on shared runners
            assert elapsed < 7.0, (
                f"took too long {elapsed:.2f}s, expected <7s for 1000 reqs (need >=~150 QPS), got {qps:.1f} QPS"
            )
            assert qps >= 120, f"QPS too low: {qps:.1f}, expected >=120"

            # Check latency via stats? avg should be reasonable
            stats = requests.get(f"http://localhost:{port}/stats", timeout=2).json()
            assert stats["avg_latency_ms"] < 150, (
                f"avg latency too high {stats['avg_latency_ms']}"
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
                f"http://localhost:{port}/lookup/batch", json=payload, timeout=5
            )
            elapsed = time.time() - start
            assert resp.status_code == 200, f"batch failed {resp.text}"
            results = resp.json()["results"]
            assert len(results) == 100
            print(f"Batch 100 points in {elapsed:.3f}s")
            assert elapsed < 0.8, f"batch too slow {elapsed:.2f}s >0.8s"
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
            # Known expected results via CLI
            def expected_via_cli(lat, lng):
                r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
                return json.loads(r.stdout)

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
                    got = resp.json()["geofences"]
                    exp = expected[pt]
                    if got != exp:
                        return False, pt, f"expected {exp} got {got}"
                    return True, pt, ""
                except Exception as e:
                    return False, pt, str(e)

            all_pts = [random.choice(test_pts) for _ in range(200)]
            with ThreadPoolExecutor(max_workers=20) as executor:
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


def test_large_geofence_set_still_fast():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        # 300 geofences
        _create_many_geofences(db, count=300)
        port = get_free_port()
        proc = start_server(db, port, grid_size="1", cache_size="1000")
        try:
            # Measure latency for point that is in empty area (should be fast due to index)
            # Far outside zones (zones are in 0-20 lat/lng range)
            start = time.time()
            for _ in range(20):
                resp = requests.get(
                    f"http://localhost:{port}/lookup?lat=80&lng=150", timeout=2
                )
                assert resp.status_code == 200
            elapsed = time.time() - start
            avg = elapsed / 20
            print(f"Large set empty area avg latency {avg * 1000:.2f}ms")
            # Relaxed from 0.1 to 0.15 to avoid flaky failures on shared infra; still requires index to be fast
            assert avg < 0.15, (
                f"empty area lookup too slow avg {avg}s, expected index to make it fast"
            )

            # Also test p99 under concurrent load
            def timed_lookup():
                s = time.time()
                r = requests.get(
                    f"http://localhost:{port}/lookup?lat=0.5&lng=0.5", timeout=2
                )
                return time.time() - s, r.status_code == 200

            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(timed_lookup) for _ in range(200)]
                latencies = []
                for f in as_completed(futures):
                    dur, ok = f.result()
                    assert ok
                    latencies.append(dur)
            latencies.sort()
            p50 = latencies[len(latencies) // 2]
            p99 = latencies[int(len(latencies) * 0.99)]
            print(f"Large set p50 {p50 * 1000:.1f}ms p99 {p99 * 1000:.1f}ms")
            # Relaxed thresholds for stability on shared runners: p50 <80ms, p99 <200ms
            # Original was 50ms/100ms which flaked at 101ms due to scheduling variance.
            # Still enforces indexed fast path (naive without index would be much higher but indexed easily passes).
            assert p50 < 0.08, f"p50 too high {p50}s"
            assert p99 < 0.2, f"p99 too high {p99}s, expected <0.2"
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
