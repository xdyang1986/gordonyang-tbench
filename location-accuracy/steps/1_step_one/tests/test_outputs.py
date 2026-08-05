"""
Tests for Step 1: Vehicle Location Tracking Service
"""

import os
import json
import subprocess
import tempfile
import shutil
import math
import pytest

SRC_DIR = "/app/src"


@pytest.fixture(scope="session")
def binary():
    assert os.path.isdir(SRC_DIR), f"{SRC_DIR} does not exist"
    # check go files exist
    go_files = []
    for root, _, files in os.walk(SRC_DIR):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))
    assert go_files, f"no .go files found under {SRC_DIR}"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "missing go.mod"

    go = shutil.which("go")
    assert go, "go toolchain not available"

    out_dir = tempfile.mkdtemp(prefix="locationctl_build_")
    bin_path = os.path.join(out_dir, "locationctl")

    # Try building from src dir
    proc = subprocess.run(
        [go, "build", "-o", bin_path, "."], cwd=SRC_DIR, capture_output=True, text=True
    )
    if proc.returncode != 0:
        # Try ./...
        proc2 = subprocess.run(
            [go, "build", "-o", bin_path, "./..."],
            cwd=SRC_DIR,
            capture_output=True,
            text=True,
        )
        assert proc2.returncode == 0, (
            f"go build failed:\n{proc.stdout}\n{proc.stderr}\n{proc2.stdout}\n{proc2.stderr}"
        )
    else:
        assert os.path.isfile(bin_path), "build did not produce binary"

    # Also check go build ./... compiles
    proc_check = subprocess.run(
        [go, "build", "./..."], cwd=SRC_DIR, capture_output=True, text=True
    )
    assert proc_check.returncode == 0, (
        f"go build ./... failed: {proc_check.stdout} {proc_check.stderr}"
    )

    return bin_path


def run_cli(binary, db_path, args, expect_code=None):
    cmd = [binary, "--db", db_path] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if expect_code is not None:
        assert proc.returncode == expect_code, (
            f"cmd {' '.join(cmd)} expected exit {expect_code} got {proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        )
    return proc


def haversine(lat1, lng1, lat2, lng2):
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ---- Tests ----


def test_build_exists(binary):
    assert os.path.isfile(binary)


def test_go_mod_no_external_deps():
    import re

    with open(os.path.join(SRC_DIR, "go.mod")) as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            mod = m.group(2)
            # standard lib has no dot in first path component, external does
            first = mod.split("/")[0]
            assert "." not in first, f"external dep found: {mod}"


def test_update_and_get(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    proc = run_cli(
        binary,
        db,
        [
            "update",
            "veh_123",
            "37.7749",
            "-122.4194",
            "1710000000000",
            "--accuracy",
            "5",
            "--speed",
            "10",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    data = json.loads(proc.stdout.strip())
    assert data["vehicle_id"] == "veh_123"
    assert abs(data["lat"] - 37.7749) < 1e-6
    assert abs(data["lng"] + 122.4194) < 1e-6
    assert data["timestamp_ms"] == 1710000000000
    # get
    proc2 = run_cli(binary, db, ["get", "veh_123"], expect_code=0)
    data2 = json.loads(proc2.stdout.strip())
    assert data2["vehicle_id"] == "veh_123"
    assert data2["timestamp_ms"] == 1710000000000


def test_persistence_across_invocations(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh_a", "10.0", "20.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_b", "11.0", "21.0", "2000"], expect_code=0)
    proc = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(proc.stdout.strip())
    assert len(arr) == 2
    ids = [x["vehicle_id"] for x in arr]
    assert ids == sorted(ids)


def test_stale_update_ignored(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    proc = run_cli(
        binary, db, ["update", "veh1", "38.0", "-123.0", "1000"], expect_code=0
    )
    assert "stale" in proc.stdout.lower()
    proc2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(proc2.stdout.strip())
    assert abs(data["lat"] - 37.0) < 1e-6  # should keep old


def test_same_timestamp_is_stale(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    proc = run_cli(
        binary, db, ["update", "veh1", "38.0", "-123.0", "2000"], expect_code=0
    )
    assert "stale" in proc.stdout.lower()


def test_invalid_lat_lng(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "91", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "-91", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "0", "181", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "0", "-181", "1000"], expect_code=2)


def test_invalid_vehicle_id(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "", "0", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh with space", "0", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "a" * 65, "0", "0", "1000"], expect_code=2)


def test_invalid_timestamp(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "-1"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "0", "0", "abc"], expect_code=2)


def test_get_not_found(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["get", "nonexistent"], expect_code=3)


def test_list_sorted(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh_z", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_a", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_m", "0", "0", "1000"], expect_code=0)
    proc = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(proc.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert ids == ["veh_a", "veh_m", "veh_z"]


def test_list_empty(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    proc = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(proc.stdout.strip())
    assert arr == []


def test_near_haversine_and_sort(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Central point SF 37.7749, -122.4194
    run_cli(
        binary,
        db,
        ["update", "veh_near", "37.7750", "-122.4195", "1000"],
        expect_code=0,
    )
    run_cli(
        binary, db, ["update", "veh_mid", "37.7849", "-122.4094", "1000"], expect_code=0
    )  # ~1.4km away
    run_cli(
        binary, db, ["update", "veh_far", "37.8049", "-122.4294", "1000"], expect_code=0
    )  # ~3.5km
    proc = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "2000"],
        expect_code=0,
    )
    arr = json.loads(proc.stdout.strip())
    # should include veh_near and veh_mid, not veh_far
    ids = [x["vehicle_id"] for x in arr]
    assert "veh_near" in ids
    assert "veh_mid" in ids
    assert "veh_far" not in ids
    # sorted by distance asc
    dists = [x["distance_m"] for x in arr]
    assert dists == sorted(dists)
    # distance correctness for veh_near ~ small
    for obj in arr:
        if obj["vehicle_id"] == "veh_near":
            assert obj["distance_m"] < 20  # ~15m
        # verify haversine matches our reference within tolerance
        expected = haversine(37.7749, -122.4194, obj["lat"], obj["lng"])
        assert abs(obj["distance_m"] - expected) < 5.0


def test_near_sorted_tie_breaker(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Two vehicles at same exact location, tie broken by vehicle_id
    run_cli(binary, db, ["update", "veh_b", "37.0", "-122.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_a", "37.0", "-122.0", "1000"], expect_code=0)
    proc = run_cli(
        binary,
        db,
        ["near", "--lat", "37.0", "--lng", "-122.0", "--radius", "100"],
        expect_code=0,
    )
    arr = json.loads(proc.stdout.strip())
    assert len(arr) == 2
    assert arr[0]["vehicle_id"] == "veh_a"
    assert arr[1]["vehicle_id"] == "veh_b"


def test_near_invalid_radius(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["near", "--lat", "0", "--lng", "0", "--radius", "-1"],
        expect_code=2,
    )
    run_cli(
        binary,
        db,
        ["near", "--lat", "0", "--lng", "0", "--radius", "60000"],
        expect_code=2,
    )


def test_clear(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["clear"], expect_code=0)
    proc = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(proc.stdout.strip())
    assert arr == []


def test_accuracy_defaults(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    proc = run_cli(binary, db, ["update", "veh1", "10", "20", "1000"], expect_code=0)
    data = json.loads(proc.stdout.strip())
    assert abs(data["accuracy"] - 10.0) < 1e-6
    assert abs(data["speed"] - 0.0) < 1e-6
    assert abs(data["heading"] - 0.0) < 1e-6


def test_corrupt_db_handling(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("{invalid json")
    proc = run_cli(binary, db, ["list"], expect_code=4)


def test_db_parent_dirs_created(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "nested", "dir", "db.json")
    proc = run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    assert os.path.isfile(db)


def test_empty_db_file_treated_as_empty(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("")
    proc = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(proc.stdout.strip())
    assert arr == []
