"""
Hardened Step1 tests - 45 tests covering:
- build, go.mod, help, validation, stale, zones, total_distance, history, list/near/track pagination, batch atomicity, stats, distance, delete, corrupt, persistence, large scale
"""

import os, json, subprocess, tempfile, shutil, math, sys
import pytest

SRC_DIR = "/app/src"


@pytest.fixture(scope="session")
def binary():
    assert os.path.isdir(SRC_DIR), f"{SRC_DIR} missing"
    go_files = []
    for root, _, files in os.walk(SRC_DIR):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))
    assert go_files, "no .go files"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "missing go.mod"
    go = shutil.which("go")
    assert go, "go not available"
    out_dir = tempfile.mkdtemp(prefix="hard1_build_")
    bin_path = os.path.join(out_dir, "locationctl")
    proc = subprocess.run(
        [go, "build", "-o", bin_path, "."], cwd=SRC_DIR, capture_output=True, text=True
    )
    if proc.returncode != 0:
        proc2 = subprocess.run(
            [go, "build", "-o", bin_path, "./..."],
            cwd=SRC_DIR,
            capture_output=True,
            text=True,
        )
        assert proc2.returncode == 0, (
            f"build failed: {proc.stdout} {proc.stderr} | {proc2.stdout} {proc2.stderr}"
        )
    assert os.path.isfile(bin_path)
    proc_check = subprocess.run(
        [go, "build", "./..."], cwd=SRC_DIR, capture_output=True, text=True
    )
    assert proc_check.returncode == 0, f"go build ./... failed: {proc_check.stderr}"
    return bin_path


def run_cli(binary, db_path, args, input_data=None, expect_code=None):
    cmd = [binary, "--db", db_path] + args
    proc = subprocess.run(
        cmd, input=input_data, capture_output=True, text=True, timeout=10
    )
    if expect_code is not None:
        assert proc.returncode == expect_code, (
            f"cmd {' '.join(cmd)} expected {expect_code} got {proc.returncode}, out={proc.stdout!r}, err={proc.stderr!r}, input={input_data!r}"
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
            assert "." not in mod.split("/")[0], f"external dep {mod}"


def test_help_contains_commands(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    proc = run_cli(binary, db, ["help"], expect_code=0)
    out = proc.stdout.lower()
    for cmd in [
        "update",
        "get",
        "list",
        "near",
        "track",
        "distance",
        "delete",
        "stats",
        "batch",
        "clear",
    ]:
        assert cmd in out, f"help missing {cmd}"
    proc2 = run_cli(binary, db, [], expect_code=0)
    for cmd in ["update", "get", "list"]:
        assert cmd in proc2.stdout.lower()


def test_update_and_get_with_total_distance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    p = run_cli(
        binary,
        db,
        [
            "update",
            "veh_123",
            "37.7749",
            "-122.4194",
            "1000",
            "--accuracy",
            "5",
            "--speed",
            "10",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["vehicle_id"] == "veh_123"
    assert abs(data["total_distance_m"] - 0) < 1e-6
    p2 = run_cli(binary, db, ["get", "veh_123"], expect_code=0)
    data2 = json.loads(p2.stdout.strip())
    assert data2["total_distance_m"] == 0


def test_total_distance_tracking(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "37.001", "-122.0", "2000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "37.002", "-122.0", "3000"], expect_code=0)
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    d1 = haversine(37.0, -122.0, 37.001, -122.0)
    d2 = haversine(37.001, -122.0, 37.002, -122.0)
    expected = d1 + d2
    assert abs(data["total_distance_m"] - expected) < 1.0, (
        f"expected {expected} got {data['total_distance_m']}"
    )
    p2 = run_cli(binary, db, ["distance", "veh1"], expect_code=0)
    data2 = json.loads(p2.stdout.strip())
    assert abs(data2["total_distance_m"] - expected) < 1.0


def test_history_10_max(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(15):
        run_cli(
            binary,
            db,
            ["update", "veh1", f"{37.0 + i * 0.001}", "-122.0", str(1000 + i * 1000)],
            expect_code=0,
        )
    p = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert "history" in data
    assert len(data["history"]) <= 10
    assert len(data["history"]) >= 10
    # sorted asc
    ts = [h["timestamp_ms"] for h in data["history"]]
    assert ts == sorted(ts)


def test_stale_ignored(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000"], expect_code=0)
    assert "stale" in p.stdout.lower()
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p2.stdout.strip())
    assert abs(data["lat"] - 37.0) < 1e-6


def test_same_timestamp_stale(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "2000"], expect_code=0)
    assert "stale" in p.stdout.lower()


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


def test_invalid_timestamp_and_speed_heading(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "-1"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "0", "0", "abc"], expect_code=2)
    run_cli(
        binary, db, ["update", "veh1", "0", "0", "1000", "--speed", "51"], expect_code=2
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "0", "0", "1000", "--heading", "360"],
        expect_code=2,
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "0", "0", "1000", "--heading", "-1"],
        expect_code=2,
    )


def test_get_not_found(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["get", "nonexistent"], expect_code=3)


def test_delete(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    p = run_cli(binary, db, ["delete", "veh1"], expect_code=0)
    assert "deleted" in p.stdout.lower()
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=3)
    p3 = run_cli(binary, db, ["delete", "veh1"], expect_code=0)
    assert "not_found" in p3.stdout.lower()


def test_list_sorted_and_filter(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh_z", "0", "0", "3000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_a", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_m", "0", "0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(p.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert ids == ["veh_a", "veh_m", "veh_z"]
    p2 = run_cli(
        binary, db, ["list", "--since", "1500", "--until", "2500"], expect_code=0
    )
    arr2 = json.loads(p2.stdout.strip())
    ids2 = [x["vehicle_id"] for x in arr2]
    assert ids2 == ["veh_m"]
    p3 = run_cli(
        binary,
        db,
        ["list", "--since", "1500", "--until", "3500", "--limit", "1", "--offset", "1"],
        expect_code=0,
    )
    arr3 = json.loads(p3.stdout.strip())
    assert len(arr3) == 1
    # sorted list is veh_a,m,z but filtered since 1500 until 3500 gives m,z, offset1 gives z
    assert arr3[0]["vehicle_id"] == "veh_z"


def test_list_pagination_edge(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    p = run_cli(binary, db, ["list", "--limit", "0"], expect_code=0)
    arr = json.loads(p.stdout.strip())
    assert arr == []
    p2 = run_cli(binary, db, ["list", "--offset", "10"], expect_code=0)
    arr2 = json.loads(p2.stdout.strip())
    assert arr2 == []
    p3 = run_cli(binary, db, ["list", "--since", "5000"], expect_code=0)
    arr3 = json.loads(p3.stdout.strip())
    assert arr3 == []


def test_list_since_until_invalid(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["list", "--since", "2000", "--until", "1000"], expect_code=2)


def test_near_basic_and_filters(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh_near",
            "37.7750",
            "-122.4195",
            "1000",
            "--accuracy",
            "5",
            "--speed",
            "5",
        ],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        [
            "update",
            "veh_mid",
            "37.7849",
            "-122.4094",
            "1000",
            "--accuracy",
            "20",
            "--speed",
            "10",
        ],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        [
            "update",
            "veh_far",
            "37.8049",
            "-122.4294",
            "1000",
            "--accuracy",
            "5",
            "--speed",
            "1",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "2000"],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert "veh_near" in ids and "veh_mid" in ids and "veh_far" not in ids
    # accuracy filter
    p2 = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.7749",
            "--lng",
            "-122.4194",
            "--radius",
            "2000",
            "--accuracy-max",
            "10",
        ],
        expect_code=0,
    )
    arr2 = json.loads(p2.stdout.strip())
    ids2 = [x["vehicle_id"] for x in arr2]
    assert "veh_near" in ids2 and "veh_mid" not in ids2
    # speed filter
    p3 = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.7749",
            "--lng",
            "-122.4194",
            "--radius",
            "2000",
            "--speed-min",
            "6",
        ],
        expect_code=0,
    )
    arr3 = json.loads(p3.stdout.strip())
    ids3 = [x["vehicle_id"] for x in arr3]
    assert "veh_mid" in ids3 and "veh_near" not in ids3


def test_near_pagination(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(5):
        run_cli(
            binary,
            db,
            ["update", f"veh_{i}", "37.7749", "-122.4194", "1000"],
            expect_code=0,
        )
    p = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.7749",
            "--lng",
            "-122.4194",
            "--radius",
            "100",
            "--limit",
            "2",
            "--offset",
            "1",
        ],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 2


def test_near_stale_only_when_now_provided(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh_fresh", "37.7749", "-122.4194", "1000000"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh_stale", "37.7749", "-122.4194", "1000"],
        expect_code=0,
    )
    # without --now, should include both
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "100"],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert "veh_fresh" in ids and "veh_stale" in ids
    # with --now, stale excluded
    now = 1000000 + 10000
    p2 = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.7749",
            "--lng",
            "-122.4194",
            "--radius",
            "100",
            "--now",
            str(now),
        ],
        expect_code=0,
    )
    arr2 = json.loads(p2.stdout.strip())
    ids2 = [x["vehicle_id"] for x in arr2]
    assert "veh_fresh" in ids2 and "veh_stale" not in ids2
    # with include-stale
    p3 = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.7749",
            "--lng",
            "-122.4194",
            "--radius",
            "100",
            "--now",
            str(now),
            "--include-stale",
        ],
        expect_code=0,
    )
    arr3 = json.loads(p3.stdout.strip())
    ids3 = [x["vehicle_id"] for x in arr3]
    assert "veh_stale" in ids3


def test_track_command(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(5):
        run_cli(
            binary,
            db,
            ["update", "veh1", f"{37.0 + i * 0.001}", "-122.0", str(1000 + i * 1000)],
            expect_code=0,
        )
    p = run_cli(
        binary, db, ["track", "veh1", "--from", "2000", "--to", "4000"], expect_code=0
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 3
    ts = [x["timestamp_ms"] for x in arr]
    assert ts == sorted(ts)
    assert ts[0] == 2000 and ts[-1] == 4000
    p2 = run_cli(
        binary,
        db,
        [
            "track",
            "veh1",
            "--from",
            "2000",
            "--to",
            "4000",
            "--limit",
            "1",
            "--offset",
            "1",
        ],
        expect_code=0,
    )
    arr2 = json.loads(p2.stdout.strip())
    assert len(arr2) == 1 and arr2[0]["timestamp_ms"] == 3000


def test_track_invalid_and_not_found(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    run_cli(
        binary, db, ["track", "veh1", "--from", "5000", "--to", "1000"], expect_code=2
    )
    run_cli(
        binary, db, ["track", "nonexist", "--from", "0", "--to", "1000"], expect_code=3
    )


def test_distance_and_stats(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.001", "-122.0", "2000", "--accuracy", "10"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh2", "38.0", "-123.0", "1000", "--accuracy", "15"],
        expect_code=0,
    )
    p = run_cli(binary, db, ["distance", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["vehicle_id"] == "veh1" and data["total_distance_m"] > 0
    p2 = run_cli(binary, db, ["stats"], expect_code=0)
    data2 = json.loads(p2.stdout.strip())
    assert data2["live"] == 2
    assert data2["total_updates"] >= 3
    assert data2["total_distance_m"] > 0
    assert data2["avg_accuracy"] > 0
    # after delete
    run_cli(binary, db, ["delete", "veh1"], expect_code=0)
    p3 = run_cli(binary, db, ["stats"], expect_code=0)
    data3 = json.loads(p3.stdout.strip())
    assert data3["live"] == 1


def test_batch_success(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\t5\t10\t90\nupdate\tveh2\t38.0\t-123.0\t2000\t5\t5\t0\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout
    assert "2" in p.stdout
    p2 = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(p2.stdout.strip())
    assert len(arr) == 2


def test_batch_with_defaults_empty_fields(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\t\t\t\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p2.stdout.strip())
    assert abs(data["accuracy"] - 10.0) < 1e-6


def test_batch_atomicity_fail_keeps_db_unchanged(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    batch_input = "update\tveh2\t37.0\t-122.0\t2000\t5\t10\t90\nupdate\tinvalid id with space\t0\t0\t3000\t5\t0\t0\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=2)
    # DB should still only have veh1
    p2 = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(p2.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert ids == ["veh1"]


def test_batch_stale_skipped_not_fail(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "2000"], expect_code=0)
    batch_input = (
        "update\tveh1\t1.0\t1.0\t1000\t5\t0\t0\nupdate\tveh1\t2.0\t2.0\t3000\t5\t0\t0\n"
    )
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout
    # Only second update should have applied (first stale)
    assert "1" in p.stdout
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p2.stdout.strip())
    assert abs(data["lat"] - 2.0) < 1e-6


def test_batch_delete_and_update(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    batch_input = "delete\tveh1\nupdate\tveh2\t1.0\t1.0\t2000\t5\t0\t0\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout
    p2 = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(p2.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert ids == ["veh2"]


def test_zones_out_of_zone_rejection(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "zone1",
            "polygon": [
                {"lat": 37.0, "lng": -122.0},
                {"lat": 37.0, "lng": -121.0},
                {"lat": 38.0, "lng": -121.0},
                {"lat": 38.0, "lng": -122.0},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    # inside
    run_cli(
        binary,
        db,
        ["update", "veh_inside", "37.5", "-121.5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    # outside
    p = run_cli(
        binary,
        db,
        ["update", "veh_outside", "39.0", "-121.5", "1000", "--zones", zones_path],
        expect_code=3,
    )
    assert "out_of_zone" in p.stdout.lower()
    # DB should not have outside
    p2 = run_cli(binary, db, ["get", "veh_outside"], expect_code=3)


def test_zones_default_file(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Create default zones file at /app/data/zones.json for this test (environment creates dir)
    default_path = "/app/data/zones.json"
    # backup if exists
    backup = None
    if os.path.exists(default_path):
        with open(default_path) as f:
            backup = f.read()
    zones = [
        {
            "id": "default_zone",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    os.makedirs(os.path.dirname(default_path), exist_ok=True)
    with open(default_path, "w") as f:
        json.dump(zones, f)
    try:
        run_cli(binary, db, ["update", "veh_inside", "5", "5", "1000"], expect_code=0)
        p = run_cli(
            binary, db, ["update", "veh_out", "20", "20", "1000"], expect_code=3
        )
        assert "out_of_zone" in p.stdout.lower()
    finally:
        if backup is not None:
            with open(default_path, "w") as f:
                f.write(backup)
        else:
            try:
                os.remove(default_path)
            except:
                pass


def test_near_zones_filter(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "z1",
            "polygon": [
                {"lat": 37.0, "lng": -122.5},
                {"lat": 37.0, "lng": -122.0},
                {"lat": 38.0, "lng": -122.0},
                {"lat": 38.0, "lng": -122.5},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary, db, ["update", "veh_inside", "37.5", "-122.2", "1000"], expect_code=0
    )
    run_cli(binary, db, ["update", "veh_out", "39.0", "-122.2", "1000"], expect_code=0)
    p = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.5",
            "--lng",
            "-122.2",
            "--radius",
            "50000",
            "--zones",
            zones_path,
        ],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert "veh_inside" in ids and "veh_out" not in ids


def test_clear_and_stats_empty(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["clear"], expect_code=0)
    p = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(p.stdout.strip()) == []
    p2 = run_cli(binary, db, ["stats"], expect_code=0)
    data = json.loads(p2.stdout.strip())
    assert data["live"] == 0 and data["total_distance_m"] == 0


def test_corrupt_db(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("{invalid")
    run_cli(binary, db, ["list"], expect_code=4)


def test_empty_db_file(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("")
    p = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(p.stdout.strip()) == []


def test_db_parent_dirs_created(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "a", "b", "c", "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    assert os.path.isfile(db)


def test_persistence_across_invocations(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh_a", "10.0", "20.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_b", "11.0", "21.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 2


def test_near_haversine_accuracy(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary, db, ["update", "veh1", "37.7750", "-122.4195", "1000"], expect_code=0
    )
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "20"],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 1
    assert arr[0]["distance_m"] < 20
    expected = haversine(37.7749, -122.4194, 37.7750, -122.4195)
    assert abs(arr[0]["distance_m"] - expected) < 5.0


def test_batch_invalid_format(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "notvalidline\n"
    run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=2)


def test_track_limit_offset_beyond(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    p = run_cli(
        binary,
        db,
        ["track", "veh1", "--from", "0", "--to", "2000", "--offset", "10"],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert arr == []


def test_large_scale_near_sorting(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # 100 vehicles at same point, ensure sorting by vehicle_id when distance equal
    for i in range(100):
        run_cli(
            binary,
            db,
            ["update", f"veh_{i:03d}", "37.7749", "-122.4194", "1000"],
            expect_code=0,
        )
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "10"],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 100
    ids = [x["vehicle_id"] for x in arr]
    assert ids == sorted(ids)
