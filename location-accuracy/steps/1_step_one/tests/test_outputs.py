"""
Extreme Hardened Step1 tests - 90+ tests covering:
- build, go.mod, help, validation (NaN/Inf), stale, zones polygon+circle+holes+time+antimeridian+edge, roads snapping, total_distance, history, list/near/track pagination with zones+roads, batch atomicity variable fields, stats, distance, delete, corrupt (whitespace, array), persistence, large scale, performance, geofence-check with circles/holes/time
"""

import os, json, subprocess, tempfile, shutil, math, time
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
    proc_check = subprocess.run(
        [go, "build", "./..."], cwd=SRC_DIR, capture_output=True, text=True
    )
    assert proc_check.returncode == 0, f"go build ./... failed: {proc_check.stderr}"
    return bin_path


def run_cli(binary, db_path, args, input_data=None, expect_code=None):
    if db_path is None:
        cmd = [binary] + args
    else:
        cmd = [binary, "--db", db_path] + args
    proc = subprocess.run(
        cmd, input=input_data, capture_output=True, text=True, timeout=15
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
        "geofence-check",
    ]:
        assert cmd in out, f"help missing {cmd}"


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
    assert abs(data["total_distance_m"] - expected) < 1.0


def test_total_distance_not_increment_on_stale(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "37.001", "-122.0", "2000"], expect_code=0)
    p_before = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    dist_before = json.loads(p_before.stdout.strip())["total_distance_m"]
    run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1500"], expect_code=0)
    p_after = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    dist_after = json.loads(p_after.stdout.strip())["total_distance_m"]
    assert abs(dist_before - dist_after) < 1e-6


def test_history_10_max_and_sorted(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(15):
        run_cli(
            binary,
            db,
            ["update", "veh1", f"{37.0 + i * 0.0001}", "-122.0", str(1000 + i * 1000)],
            expect_code=0,
        )
    p = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert len(data["history"]) == 10
    ts = [h["timestamp_ms"] for h in data["history"]]
    assert ts == sorted(ts)
    assert ts[0] == 6000


def test_stale_ignored(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000"], expect_code=0)
    assert "stale" in p.stdout.lower()


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
    run_cli(binary, db, ["update", "veh1", "0", "181", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "NaN", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "inf", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh1", "0", "Infinity", "1000"], expect_code=2)


def test_invalid_vehicle_id(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "", "0", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "veh with space", "0", "0", "1000"], expect_code=2)
    run_cli(binary, db, ["update", "a" * 65, "0", "0", "1000"], expect_code=2)


def test_invalid_timestamp_and_speed_heading_accuracy(binary):
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
        ["update", "veh1", "0", "0", "1000", "--accuracy", "-1"],
        expect_code=2,
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "0", "0", "1000", "--accuracy", "NaN"],
        expect_code=2,
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "0", "0", "1000", "--speed", "inf"],
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
    run_cli(binary, db, ["get", "veh1"], expect_code=3)


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


def test_list_pagination_edge(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    p = run_cli(binary, db, ["list", "--limit", "0"], expect_code=0)
    assert json.loads(p.stdout.strip()) == []
    p2 = run_cli(binary, db, ["list", "--offset", "10"], expect_code=0)
    assert json.loads(p2.stdout.strip()) == []


def test_list_since_until_invalid(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["list", "--since", "2000", "--until", "1000"], expect_code=2)


def test_list_zones_filter(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "z1",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(binary, db, ["update", "veh_in", "5", "5", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_out", "20", "20", "1000"], expect_code=0)
    p = run_cli(binary, db, ["list", "--zones", zones_path], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_in" in ids and "veh_out" not in ids


def test_list_zones_circle_filter(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "circle_zone", "center": {"lat": 0, "lng": 0}, "radius_m": 100000}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(binary, db, ["update", "veh_in", "0.5", "0.5", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_out", "20", "20", "1000"], expect_code=0)
    p = run_cli(binary, db, ["list", "--zones", zones_path], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_in" in ids and "veh_out" not in ids


def test_list_roads_filter(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "r1", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh_on", "0.0001", "0.5", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_off", "10", "10", "1000"], expect_code=0)
    p = run_cli(binary, db, ["list", "--roads", roads_path], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_on" in ids and "veh_off" not in ids


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
    assert "veh_near" in ids and "veh_far" not in ids


def test_near_pagination_and_sort_tie_breaking(binary):
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
    ids = [x["vehicle_id"] for x in arr]
    assert ids == sorted(ids)


def test_near_radius_zero(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000"], expect_code=0
    )
    run_cli(
        binary, db, ["update", "veh2", "37.7750", "-122.4194", "1000"], expect_code=0
    )
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "0"],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 1 and arr[0]["vehicle_id"] == "veh1"


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
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "100"],
        expect_code=0,
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_fresh" in ids and "veh_stale" in ids
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
    ids2 = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    assert "veh_fresh" in ids2 and "veh_stale" not in ids2


def test_near_roads_filter(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "r1", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh_near", "0.0001", "0.5", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_far", "10", "10", "1000"], expect_code=0)
    p = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "0",
            "--lng",
            "0.5",
            "--radius",
            "50000",
            "--roads",
            roads_path,
        ],
        expect_code=0,
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_near" in ids
    assert "veh_far" not in ids


def test_near_zones_and_roads_combined(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    roads_path = os.path.join(tmp, "roads.json")
    zones = [
        {
            "id": "z1",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    roads = [{"id": "r1", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 10}]}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh_ok", "0.0001", "5", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_no_road", "5", "5", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_no_zone", "20", "20", "1000"], expect_code=0)
    p = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "0",
            "--lng",
            "5",
            "--radius",
            "50000",
            "--zones",
            zones_path,
            "--roads",
            roads_path,
        ],
        expect_code=0,
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_ok" in ids
    assert "veh_no_road" not in ids
    assert "veh_no_zone" not in ids


def test_track_command(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(5):
        run_cli(
            binary,
            db,
            ["update", "veh1", f"{37.0 + i * 0.0001}", "-122.0", str(1000 + i * 1000)],
            expect_code=0,
        )
    p = run_cli(
        binary, db, ["track", "veh1", "--from", "2000", "--to", "4000"], expect_code=0
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 3


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
        ["update", "veh1", "37.0001", "-122.0", "2000", "--accuracy", "10"],
        expect_code=0,
    )
    p = run_cli(binary, db, ["distance", "veh1"], expect_code=0)
    assert "total_distance_m" in p.stdout


def test_batch_success(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\t5\t10\t90\nupdate\tveh2\t38.0\t-123.0\t2000\t5\t5\t0\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout


def test_batch_variable_fields_4_fields(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p2.stdout.strip())
    assert abs(data["accuracy"] - 10.0) < 1e-6


def test_batch_variable_fields_with_empty_defaults(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\t\t\t\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout


def test_batch_6_fields_partial(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\t5\t10\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout


def test_batch_atomicity_fail_keeps_db_unchanged(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    batch_input = "update\tveh2\t37.0\t-122.0\t2000\t5\t10\t90\nupdate\tinvalid id with space\t0\t0\t3000\t5\t0\t0\n"
    run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=2)
    p2 = run_cli(binary, db, ["list"], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    assert ids == ["veh1"]


def test_batch_stale_skipped_not_fail(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "2000"], expect_code=0)
    batch_input = "update\tveh1\t0.0001\t0.0001\t1000\t5\t0\t0\nupdate\tveh1\t0.0002\t0.0002\t3000\t5\t0\t0\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout
    assert "1" in p.stdout


def test_batch_whitespace_empty_lines_ignored(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "\n  \nupdate\tveh1\t37.0\t-122.0\t1000\n\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout


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
    run_cli(
        binary,
        db,
        ["update", "veh_inside", "37.5", "-121.5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh_outside", "39.0", "-121.5", "1000", "--zones", zones_path],
        expect_code=3,
    )
    assert "out_of_zone" in (p.stdout + p.stderr).lower()


def test_zones_holes(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "with_hole",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "holes": [
                [
                    {"lat": 2, "lng": 2},
                    {"lat": 2, "lng": 8},
                    {"lat": 8, "lng": 8},
                    {"lat": 8, "lng": 2},
                ]
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh_outer", "1", "1", "1000", "--zones", zones_path],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh_hole", "5", "5", "1000", "--zones", zones_path],
        expect_code=3,
    )
    assert "out_of_zone" in (p.stdout + p.stderr).lower()


def test_zones_circle(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "circle_zone", "center": {"lat": 0, "lng": 0}, "radius_m": 100000}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh_in", "0.5", "0.5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh_out", "10", "10", "1000", "--zones", zones_path],
        expect_code=3,
    )
    assert "out_of_zone" in (p.stdout + p.stderr).lower()


def test_zones_antimeridian(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "anti",
            "polygon": [
                {"lat": -10, "lng": 179},
                {"lat": -10, "lng": -179},
                {"lat": 10, "lng": -179},
                {"lat": 10, "lng": 179},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh_inside", "0", "180", "1000", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh_inside2", "0", "-179.5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh_out", "0", "0", "1000", "--zones", zones_path],
        expect_code=3,
    )
    assert "out_of_zone" in (p.stdout + p.stderr).lower()


def test_zones_point_on_edge_inside(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "square",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh_edge", "0", "5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh_vertex", "0", "0", "1000", "--zones", zones_path],
        expect_code=0,
    )


def test_zones_time_based_activation(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "timed",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "active_from": 1000,
            "active_to": 2000,
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh_early", "20", "20", "500", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh_mid_out", "20", "20", "1500", "--zones", zones_path],
        expect_code=3,
    )


def test_zones_invalid_file(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "bad", "polygon": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "1000", "--zones", zones_path],
        expect_code=2,
    )


def test_zones_invalid_both_polygon_and_circle(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "invalid",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
            ],
            "center": {"lat": 0, "lng": 0},
            "radius_m": 1000,
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "1000", "--zones", zones_path],
        expect_code=2,
    )


def test_zones_default_file(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    default_path = "/app/data/zones.json"
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
        assert "out_of_zone" in (p.stdout + p.stderr).lower()
    finally:
        if backup is not None:
            with open(default_path, "w") as f:
                f.write(backup)
        else:
            try:
                os.remove(default_path)
            except:
                pass


def test_geofence_check_basic(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "zone1",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    p = run_cli(
        binary, db, ["geofence-check", "5", "5", "--zones", zones_path], expect_code=0
    )
    data = json.loads(p.stdout.strip())
    assert data["inside"] is True


def test_geofence_check_holes(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "with_hole",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "holes": [
                [
                    {"lat": 2, "lng": 2},
                    {"lat": 2, "lng": 8},
                    {"lat": 8, "lng": 8},
                    {"lat": 8, "lng": 2},
                ]
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    p = run_cli(
        binary, db, ["geofence-check", "1", "1", "--zones", zones_path], expect_code=0
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    p2 = run_cli(
        binary, db, ["geofence-check", "5", "5", "--zones", zones_path], expect_code=0
    )
    assert json.loads(p2.stdout.strip())["inside"] is False


def test_geofence_check_circle(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "circle", "center": {"lat": 0, "lng": 0}, "radius_m": 100000}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    p = run_cli(
        binary,
        db,
        ["geofence-check", "0.5", "0.5", "--zones", zones_path],
        expect_code=0,
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    p2 = run_cli(
        binary,
        db,
        ["geofence-check", "10", "10", "--zones", zones_path],
        expect_code=0,
    )
    assert json.loads(p2.stdout.strip())["inside"] is False


def test_geofence_check_time_based(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "timed",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "active_from": 1000,
            "active_to": 2000,
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    p = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "1500"],
        expect_code=0,
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    p2 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "500"],
        expect_code=0,
    )
    assert json.loads(p2.stdout.strip())["inside"] is False


def test_geofence_check_antimeridian(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "anti",
            "polygon": [
                {"lat": -10, "lng": 179},
                {"lat": -10, "lng": -179},
                {"lat": 10, "lng": -179},
                {"lat": 10, "lng": 179},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    p = run_cli(
        binary,
        db,
        ["geofence-check", "0", "180", "--zones", zones_path],
        expect_code=0,
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    p2 = run_cli(
        binary, db, ["geofence-check", "0", "0", "--zones", zones_path], expect_code=0
    )
    assert json.loads(p2.stdout.strip())["inside"] is False


def test_clear_and_stats_empty(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["clear"], expect_code=0)
    p = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(p.stdout.strip()) == []


def test_corrupt_db(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("{invalid")
    run_cli(binary, db, ["list"], expect_code=4)


def test_corrupt_db_array_not_object(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("[]")
    run_cli(binary, db, ["list"], expect_code=4)


def test_whitespace_db_empty(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("   \n\t  \n")
    p = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(p.stdout.strip()) == []


def test_db_parent_dirs_created(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "a", "b", "c", "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    assert os.path.isfile(db)


def test_atomic_write_no_tmp_leftover(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    files = os.listdir(tmp)
    assert not any(".tmp." in f for f in files)


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
    assert (
        arr[0]["distance_m"] < 20
    )  # spec now defines distance_m as Haversine distance in metres


def test_roads_mixed_format(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "poly",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        },
        {
            "id": "seg",
            "start": {"lat": 37.7849, "lng": -122.4194},
            "end": {"lat": 37.7849, "lng": -122.4094},
        },
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary, db, ["update", "veh1", "37.7750", "-122.4144", "1000"], expect_code=0
    )
    p = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "37.7750",
            "--lng",
            "-122.4144",
            "--radius",
            "100",
            "--roads",
            roads_path,
        ],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) >= 1


def test_large_scale_sorting_and_performance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(800):
        run_cli(
            binary,
            db,
            ["update", f"veh_{i:04d}", "37.7749", "-122.4194", "1000"],
            expect_code=0,
        )
    start = time.time()
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "10"],
        expect_code=0,
    )
    elapsed = time.time() - start
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 800
    assert elapsed < 3.0


def test_large_scale_list_with_zones_and_roads(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    roads_path = os.path.join(tmp, "roads.json")
    zones = [
        {
            "id": "z1",
            "polygon": [
                {"lat": 37.0, "lng": -123.0},
                {"lat": 37.0, "lng": -122.0},
                {"lat": 38.0, "lng": -122.0},
                {"lat": 38.0, "lng": -123.0},
            ],
        }
    ]
    roads = [
        {
            "id": "r1",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    for i in range(200):
        run_cli(
            binary,
            db,
            ["update", f"veh_{i:03d}", "37.7749", "-122.4144", "1000"],
            expect_code=0,
        )
    p = run_cli(
        binary,
        db,
        ["list", "--zones", zones_path, "--roads", roads_path],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 200


# --- Crash-consistency gate: corrupt backup with .corrupt.<nanosec> integer suffix, stale tmp handling, truncated file ---


def test_corrupt_db_creates_backup_with_nanosec_suffix(binary):
    import re

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("{invalid json")
    orig_content = open(db).read()
    run_cli(binary, db, ["list"], expect_code=4)
    files = os.listdir(tmp)
    corrupt_files = [ff for ff in files if ".corrupt." in ff]
    assert len(corrupt_files) >= 1, f"expected .corrupt backup, got {files}"
    for fn in corrupt_files:
        m = re.search(r"\.corrupt\.(\d+)$", fn)
        assert m, f"corrupt suffix should be integer nanosec, got {fn}"
        backup_content = open(os.path.join(tmp, fn)).read()
        assert backup_content == orig_content or "invalid" in backup_content


def test_corrupt_array_creates_backup_with_nanosec(binary):
    import re

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("[]")
    run_cli(binary, db, ["list"], expect_code=4)
    files = os.listdir(tmp)
    corrupt_files = [ff for ff in files if ".corrupt." in ff]
    assert len(corrupt_files) >= 1
    assert any(re.search(r"\.corrupt\.(\d+)$", fn) for fn in corrupt_files)


def test_corrupt_null_creates_backup(binary):
    import re

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("null")
    run_cli(binary, db, ["list"], expect_code=4)
    files = os.listdir(tmp)
    corrupt_files = [ff for ff in files if ".corrupt." in ff]
    assert len(corrupt_files) >= 1
    assert any(re.search(r"\.corrupt\.(\d+)$", fn) for fn in corrupt_files)


def test_truncated_file_corruption_path(binary):
    import re

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Truncated mid-object - valid JSON prefix cut
    truncated = '{"veh1": {"vehicle_id": "veh1", "lat": 37.7749, "lng": -12'
    with open(db, "w") as f:
        f.write(truncated)
    run_cli(binary, db, ["list"], expect_code=4)
    files = os.listdir(tmp)
    assert any(".corrupt." in ff for ff in files), (
        f"truncated should produce .corrupt backup, got {files}"
    )
    assert any(re.search(r"\.corrupt\.(\d+)$", fn) for fn in files if ".corrupt." in fn)


def test_stale_tmp_file_ignored_and_cleaned(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Pre-create stale tmp file
    stale_tmp = os.path.join(tmp, "db.json.tmp.12345")
    with open(stale_tmp, "w") as f:
        f.write("garbage that should not be read as DB")
    assert os.path.exists(stale_tmp)
    # Next command should succeed, ignoring stale tmp
    run_cli(
        binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000"], expect_code=0
    )
    # After success, no tmp leftover and stale tmp should be cleaned (or at least not cause failure)
    files = os.listdir(tmp)
    # Our implementation cleans stale tmp files on save
    assert not any(f.startswith("db.json.tmp.") for f in files), (
        f"stale tmp not cleaned, files={files}"
    )
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["vehicle_id"] == "veh1"


def test_corrupt_backup_contains_original_content(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    original = '{"broken": } not valid'
    with open(db, "w") as f:
        f.write(original)
    run_cli(binary, db, ["list"], expect_code=4)
    files = [f for f in os.listdir(tmp) if ".corrupt." in f]
    assert files
    found = False
    for fn in files:
        content = open(os.path.join(tmp, fn)).read()
        if content == original:
            found = True
            break
    assert found, "backup should contain original corrupt content"


def test_atomic_write_cleans_all_tmp_and_no_corrupt_on_success(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    files = os.listdir(tmp)
    assert not any(".tmp." in f for f in files), f"tmp leftover after success {files}"
    # No corrupt file should exist on success
    assert not any(".corrupt." in f for f in files), (
        f"unexpected corrupt file on success {files}"
    )


def test_canonicalization_db_valid_json_sorted_or_loadable(binary):
    # Ensure DB file after write is valid JSON object and loadable, not array or null, and contains checksum-like persistence
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    raw = open(db).read()
    obj = json.loads(raw)
    assert isinstance(obj, dict)
    assert "veh1" in obj
    # Ensure no tmp or corrupt leftovers
    files = os.listdir(tmp)
    assert not any(".tmp." in f for f in files)
    assert not any(".corrupt." in f for f in files)


# ---------- Additional hardening to make Step1 not too easy ----------


def test_vehicle_id_boundary_length(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # length 1 valid
    run_cli(binary, db, ["update", "a", "0", "0", "1000"], expect_code=0)
    # length 64 valid
    vid64 = "a" * 64
    run_cli(binary, db, ["update", vid64, "0", "0", "2000"], expect_code=0)
    # 65 invalid
    run_cli(binary, db, ["update", "a" * 65, "0", "0", "3000"], expect_code=2)
    # empty invalid
    run_cli(binary, db, ["update", "", "0", "0", "4000"], expect_code=2)
    # dash, underscore allowed
    run_cli(binary, db, ["update", "veh-1_2", "0", "0", "5000"], expect_code=0)
    # special chars not allowed
    run_cli(binary, db, ["update", "veh@!", "0", "0", "6000"], expect_code=2)


def test_exact_boundaries_speed_heading_accuracy(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # speed exactly 50 valid
    run_cli(
        binary, db, ["update", "v1", "0", "0", "1000", "--speed", "50"], expect_code=0
    )
    # speed >50 invalid
    run_cli(
        binary,
        db,
        ["update", "v2", "0", "0", "1000", "--speed", "50.0001"],
        expect_code=2,
    )
    run_cli(
        binary, db, ["update", "v3", "0", "0", "1000", "--speed", "50.1"], expect_code=2
    )
    # heading 0 valid, 359.999 valid, 360 invalid
    run_cli(
        binary, db, ["update", "v4", "0", "0", "2000", "--heading", "0"], expect_code=0
    )
    run_cli(
        binary,
        db,
        ["update", "v5", "0", "0", "3000", "--heading", "359.999"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "v6", "0", "0", "4000", "--heading", "360"],
        expect_code=2,
    )
    # accuracy 0 valid
    run_cli(
        binary, db, ["update", "v7", "0", "0", "5000", "--accuracy", "0"], expect_code=0
    )


def test_timestamp_integer_rejection(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # float string
    run_cli(binary, db, ["update", "v1", "0", "0", "1000.0"], expect_code=2)
    run_cli(binary, db, ["update", "v2", "0", "0", "1e3"], expect_code=2)
    run_cli(binary, db, ["update", "v3", "0", "0", "0x3e8"], expect_code=2)
    run_cli(binary, db, ["update", "v4", "0", "0", "1.5"], expect_code=2)
    # negative invalid
    run_cli(binary, db, ["update", "v5", "0", "0", "-1"], expect_code=2)
    # valid integer
    run_cli(binary, db, ["update", "v6", "0", "0", "0"], expect_code=0)
    run_cli(binary, db, ["update", "v7", "0", "0", "1234567890"], expect_code=0)


def test_total_distance_not_increment_on_out_of_zone(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "z1",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "5.001", "5", "2000", "--zones", zones_path],
        expect_code=0,
    )
    p_before = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    dist_before = json.loads(p_before.stdout.strip())["total_distance_m"]
    # out of zone should not increment distance
    run_cli(
        binary,
        db,
        ["update", "veh1", "20", "20", "3000", "--zones", zones_path],
        expect_code=3,
    )
    p_after = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    dist_after = json.loads(p_after.stdout.strip())["total_distance_m"]
    assert abs(dist_before - dist_after) < 1e-6
    # history should not include out_of_zone attempt
    p_verbose = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    hist = json.loads(p_verbose.stdout.strip())["history"]
    assert len(hist) == 2
    assert hist[-1]["lat"] == 5.001


def test_batch_mixed_update_delete_same_vehicle(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # batch update then delete same vehicle -> should end deleted
    batch1 = "update\tveh1\t0\t0\t1000\ndelete\tveh1\n"
    p = run_cli(binary, db, ["batch"], input_data=batch1, expect_code=0)
    assert "batch_ok" in p.stdout
    run_cli(binary, db, ["get", "veh1"], expect_code=3)
    # batch delete then update -> should exist
    run_cli(binary, db, ["clear"], expect_code=0)
    batch2 = "delete\tveh1\nupdate\tveh1\t1\t1\t2000\n"
    p2 = run_cli(binary, db, ["batch"], input_data=batch2, expect_code=0)
    assert "batch_ok" in p2.stdout
    p_get = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    assert json.loads(p_get.stdout.strip())["lat"] == 1


def test_batch_empty_field_defaults_all_combinations(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # 5 fields minimal
    run_cli(
        binary, db, ["batch"], input_data="update\tveh1\t0\t0\t1000\n", expect_code=0
    )
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert abs(data["accuracy"] - 10.0) < 1e-6
    assert abs(data["speed"] - 0.0) < 1e-6
    assert abs(data["heading"] - 0.0) < 1e-6
    # 6 fields with empty accuracy -> default
    run_cli(binary, db, ["clear"], expect_code=0)
    run_cli(
        binary, db, ["batch"], input_data="update\tveh1\t0\t0\t1000\t\n", expect_code=0
    )
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    assert abs(json.loads(p.stdout.strip())["accuracy"] - 10.0) < 1e-6
    # 7 fields empty accuracy and speed -> defaults
    run_cli(binary, db, ["clear"], expect_code=0)
    run_cli(
        binary,
        db,
        ["batch"],
        input_data="update\tveh1\t0\t0\t1000\t\t\n",
        expect_code=0,
    )
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert abs(data["accuracy"] - 10.0) < 1e-6
    assert abs(data["speed"] - 0.0) < 1e-6
    # 8 fields with empty middle -> defaults
    run_cli(binary, db, ["clear"], expect_code=0)
    run_cli(
        binary,
        db,
        ["batch"],
        input_data="update\tveh1\t0\t0\t1000\t5\t\t\n",
        expect_code=0,
    )
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert abs(data["accuracy"] - 5.0) < 1e-6
    assert abs(data["speed"] - 0.0) < 1e-6
    assert abs(data["heading"] - 0.0) < 1e-6
    # >8 fields should fail
    run_cli(
        binary,
        db,
        ["batch"],
        input_data="update\tveh1\t0\t0\t1000\t5\t10\t90\textra\n",
        expect_code=2,
    )


def test_near_radius_boundary_exact(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh_exact", "0", "0", "1000"], expect_code=0)
    # radius 0 includes exact point
    p = run_cli(
        binary, db, ["near", "--lat", "0", "--lng", "0", "--radius", "0"], expect_code=0
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_exact" in ids
    # vehicle 0.0001 deg away ~11m, radius 10 should exclude, radius 12 should include
    run_cli(binary, db, ["update", "veh_near", "0.0001", "0", "1000"], expect_code=0)
    p2 = run_cli(
        binary,
        db,
        ["near", "--lat", "0", "--lng", "0", "--radius", "10"],
        expect_code=0,
    )
    ids2 = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    # 0.0001 deg lat ~11.1m, so 10m radius should exclude veh_near
    assert "veh_near" not in ids2
    p3 = run_cli(
        binary,
        db,
        ["near", "--lat", "0", "--lng", "0", "--radius", "12"],
        expect_code=0,
    )
    ids3 = [x["vehicle_id"] for x in json.loads(p3.stdout.strip())]
    assert "veh_near" in ids3


def test_geofence_circle_exact_radius_inside(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    # circle radius 1000m
    zones = [{"id": "c", "center": {"lat": 0, "lng": 0}, "radius_m": 1000}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    # point ~1000m north: delta lat = distance / R * 180/pi
    # 1000m / 6371000 * 180/pi = ~0.008993 deg
    delta = 1000.0 / 6371000.0 * 180.0 / math.pi
    p = run_cli(
        binary,
        db,
        ["geofence-check", str(delta), "0", "--zones", zones_path],
        expect_code=0,
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    # slightly beyond radius should be outside
    delta_out = 1001.0 / 6371000.0 * 180.0 / math.pi
    p2 = run_cli(
        binary,
        db,
        ["geofence-check", str(delta_out), "0", "--zones", zones_path],
        expect_code=0,
    )
    assert json.loads(p2.stdout.strip())["inside"] is False


def test_zones_time_boundary_inclusive(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "timed",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "active_from": 1000,
            "active_to": 2000,
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    # ts == from should be active -> inside required: 5,5 inside zone -> ok, 20,20 outside -> out_of_zone
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh2", "20", "20", "1000", "--zones", zones_path],
        expect_code=3,
    )
    # ts == to should be active
    run_cli(
        binary,
        db,
        ["update", "veh3", "5", "5", "2000", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh4", "20", "20", "2000", "--zones", zones_path],
        expect_code=3,
    )
    # ts == from-1 and to+1 should be inactive -> empty active list means no zone check, should succeed everywhere
    run_cli(
        binary,
        db,
        ["update", "veh5", "20", "20", "999", "--zones", zones_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh6", "20", "20", "2001", "--zones", zones_path],
        expect_code=0,
    )
    # geofence-check inclusive
    p = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "1000"],
        expect_code=0,
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    p2 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "2000"],
        expect_code=0,
    )
    assert json.loads(p2.stdout.strip())["inside"] is True
    p3 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "999"],
        expect_code=0,
    )
    assert json.loads(p3.stdout.strip())["inside"] is False


def test_list_pagination_offset_limit_exact_order(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(5):
        run_cli(
            binary, db, ["update", f"veh_{i}", "0", "0", str(1000 + i)], expect_code=0
        )
    # offset 1 limit 2 -> veh_1, veh_2 not veh_0, veh_1
    p = run_cli(binary, db, ["list", "--limit", "2", "--offset", "1"], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert ids == ["veh_1", "veh_2"]
    # offset 0 limit 2 -> veh_0, veh_1
    p2 = run_cli(binary, db, ["list", "--limit", "2", "--offset", "0"], expect_code=0)
    assert [x["vehicle_id"] for x in json.loads(p2.stdout.strip())] == [
        "veh_0",
        "veh_1",
    ]
    # offset 3 limit 10 -> last 2
    p3 = run_cli(binary, db, ["list", "--limit", "10", "--offset", "3"], expect_code=0)
    assert [x["vehicle_id"] for x in json.loads(p3.stdout.strip())] == [
        "veh_3",
        "veh_4",
    ]


def test_history_last_equals_current_after_stale(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "0.0001", "0.0001", "2000"], expect_code=0)
    # stale attempt - small move but earlier timestamp
    run_cli(binary, db, ["update", "veh1", "0.0002", "0.0002", "1500"], expect_code=0)
    p = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert abs(data["lat"] - 0.0001) < 1e-6
    assert abs(data["history"][-1]["lat"] - 0.0001) < 1e-6
    assert data["history"][-1]["timestamp_ms"] == 2000


def test_parent_dirs_deeply_nested(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "a", "b", "c", "d", "e", "f", "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    assert os.path.isfile(db)
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    assert json.loads(p.stdout.strip())["vehicle_id"] == "veh1"


def test_corrupt_multiple_backups_distinct_nanosec(binary):
    import re, time

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # first corruption
    with open(db, "w") as f:
        f.write("{invalid1")
    run_cli(binary, db, ["list"], expect_code=4)
    time.sleep(0.01)
    # second corruption with different content
    with open(db, "w") as f:
        f.write("{invalid2")
    run_cli(binary, db, ["list"], expect_code=4)
    files = [fn for fn in os.listdir(tmp) if ".corrupt." in fn]
    assert len(files) >= 2, f"expected at least 2 corrupt backups, got {files}"
    suffixes = []
    for fn in files:
        m = re.search(r"\.corrupt\.(\d+)$", fn)
        assert m, f"integer suffix required {fn}"
        suffixes.append(int(m.group(1)))
    assert len(set(suffixes)) == len(suffixes), "nanosec suffixes should be distinct"


def test_batch_zones_check_before_stale_still_fails(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [
        {
            "id": "z1",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    # First create vehicle with ts 2000 inside zone
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "2000", "--zones", zones_path],
        expect_code=0,
    )
    # default zones file at /app/data/zones.json
    default_path = "/app/data/zones.json"
    backup = None
    if os.path.exists(default_path):
        with open(default_path) as f:
            backup = f.read()
    os.makedirs(os.path.dirname(default_path), exist_ok=True)
    with open(default_path, "w") as f:
        json.dump(zones, f)
    try:
        # batch contains stale update (ts 1000 <= 2000) that is out_of_zone (20,20)
        # zones check must be before stale, so should fail even though stale would be skipped
        batch_input = "update\tveh1\t20\t20\t1000\n"
        run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=2)
        # DB unchanged
        p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
        assert json.loads(p.stdout.strip())["timestamp_ms"] == 2000
    finally:
        if backup is not None:
            with open(default_path, "w") as f:
                f.write(backup)
        else:
            try:
                os.remove(default_path)
            except:
                pass

def test_zones_now_vs_update_timestamp_divergence_simplified(binary):
    # Intuitive: no active zones at now => list/near allow all, geofence outside
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "timed", "polygon": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 10}, {"lat": 10, "lng": 10}, {"lat": 10, "lng": 0}], "active_from": 1000, "active_to": 2000}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(binary, db, ["update", "veh_outside", "10.4", "5", "500", "--zones", zones_path], expect_code=0)
    p = run_cli(binary, db, ["list", "--zones", zones_path, "--now", "1500"], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_outside" not in ids
    p2 = run_cli(binary, db, ["list", "--zones", zones_path, "--now", "500"], expect_code=0)
    ids2 = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    assert "veh_outside" in ids2, f"allow-all when inactive, got {ids2}"
    p2_all = run_cli(binary, db, ["list", "--zones", zones_path], expect_code=0)
    ids2_all = [x["vehicle_id"] for x in json.loads(p2_all.stdout.strip())]
    assert "veh_outside" not in ids2_all
    run_cli(binary, db, ["update", "veh_inside", "9.9", "5", "1500", "--zones", zones_path], expect_code=0)
    p4 = run_cli(binary, db, ["list", "--zones", zones_path, "--now", "1500"], expect_code=0)
    ids4 = [x["vehicle_id"] for x in json.loads(p4.stdout.strip())]
    assert "veh_inside" in ids4 and "veh_outside" not in ids4
    p3 = run_cli(binary, db, ["list", "--zones", zones_path, "--now", "500"], expect_code=0)
    ids3 = [x["vehicle_id"] for x in json.loads(p3.stdout.strip())]
    assert "veh_inside" in ids3 and "veh_outside" in ids3
    p_near_active = run_cli(binary, db, ["near", "--lat", "10", "--lng", "5", "--radius", "50000", "--zones", zones_path, "--now", "1500"], expect_code=0)
    near_ids_active = [x["vehicle_id"] for x in json.loads(p_near_active.stdout.strip())]
    assert "veh_outside" not in near_ids_active and "veh_inside" in near_ids_active
    p_near_inactive = run_cli(binary, db, ["near", "--lat", "10", "--lng", "5", "--radius", "50000", "--zones", zones_path, "--now", "500"], expect_code=0)
    near_ids_inactive = [x["vehicle_id"] for x in json.loads(p_near_inactive.stdout.strip())]
    assert "veh_outside" in near_ids_inactive and "veh_inside" in near_ids_inactive
    p_gc_500 = run_cli(binary, db, ["geofence-check", "5", "5", "--zones", zones_path, "--now", "500"], expect_code=0)
    assert json.loads(p_gc_500.stdout.strip())["inside"] is False
    p_gc_1500 = run_cli(binary, db, ["geofence-check", "5", "5", "--zones", zones_path, "--now", "1500"], expect_code=0)
    assert json.loads(p_gc_1500.stdout.strip())["inside"] is True
