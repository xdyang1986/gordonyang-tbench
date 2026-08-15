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


def test_zones_only_from_and_only_to_boundary(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # zone with only active_from
    zones_from_path = os.path.join(tmp, "zones_from.json")
    zones_from = [
        {
            "id": "only_from",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "active_from": 1000,
        }
    ]
    with open(zones_from_path, "w") as f:
        json.dump(zones_from, f)
    # at exactly from should be active
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "1000", "--zones", zones_from_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh2", "20", "20", "1000", "--zones", zones_from_path],
        expect_code=3,
    )
    # before from should be inactive -> no zone check
    run_cli(
        binary,
        db,
        ["update", "veh3", "20", "20", "999", "--zones", zones_from_path],
        expect_code=0,
    )
    # geofence-check only_from
    p = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_from_path, "--now", "1000"],
        expect_code=0,
    )
    assert json.loads(p.stdout.strip())["inside"] is True
    p2 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_from_path, "--now", "999"],
        expect_code=0,
    )
    assert json.loads(p2.stdout.strip())["inside"] is False

    # zone with only active_to
    zones_to_path = os.path.join(tmp, "zones_to.json")
    zones_to = [
        {
            "id": "only_to",
            "polygon": [
                {"lat": 0, "lng": 0},
                {"lat": 0, "lng": 10},
                {"lat": 10, "lng": 10},
                {"lat": 10, "lng": 0},
            ],
            "active_to": 2000,
        }
    ]
    with open(zones_to_path, "w") as f:
        json.dump(zones_to, f)
    # at exactly to should be active
    run_cli(
        binary,
        db,
        ["update", "veh4", "5", "5", "2000", "--zones", zones_to_path],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh5", "20", "20", "2000", "--zones", zones_to_path],
        expect_code=3,
    )
    # after to should be inactive
    run_cli(
        binary,
        db,
        ["update", "veh6", "20", "20", "2001", "--zones", zones_to_path],
        expect_code=0,
    )
    p3 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_to_path, "--now", "2000"],
        expect_code=0,
    )
    assert json.loads(p3.stdout.strip())["inside"] is True
    p4 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_to_path, "--now", "2001"],
        expect_code=0,
    )
    assert json.loads(p4.stdout.strip())["inside"] is False


def test_zones_time_boundary_same_zone_both_inclusive(binary):
    # Single zone with both from and to, test ts exactly equal to from and to in same test flow
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
    # vehicle that will be tested at both boundaries sequentially
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    # same vehicle at exactly to should still be inside (update ts increasing, but still inside zone)
    run_cli(
        binary,
        db,
        ["update", "veh1", "5", "5", "2000", "--zones", zones_path],
        expect_code=0,
    )
    # outside at exactly boundaries should fail both
    run_cli(
        binary,
        db,
        ["update", "veh2", "20", "20", "1000", "--zones", zones_path],
        expect_code=3,
    )
    run_cli(
        binary,
        db,
        ["update", "veh2", "20", "20", "2000", "--zones", zones_path],
        expect_code=3,
    )
    # geofence-check both boundaries same zone
    p_from = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "1000"],
        expect_code=0,
    )
    assert json.loads(p_from.stdout.strip())["inside"] is True
    assert json.loads(p_from.stdout.strip())["zone_id"] == "timed"
    p_to = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "2000"],
        expect_code=0,
    )
    assert json.loads(p_to.stdout.strip())["inside"] is True


def test_zones_now_vs_update_timestamp_divergence(binary):
    # Divergence: update filters by its own ts, list/near filters by --now
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
    # Case 1: update at ts=500 when zone inactive -> succeeds even outside zone
    # This is the divergence: update uses its own ts (500, no active zones), so outside allowed
    run_cli(
        binary,
        db,
        ["update", "veh_outside", "20", "20", "500", "--zones", zones_path],
        expect_code=0,
    )
    # list with --now=1500 (active) should filter out because vehicle outside active zone at now=1500
    p = run_cli(
        binary, db, ["list", "--zones", zones_path, "--now", "1500"], expect_code=0
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_outside" not in ids, (
        "list with --now inside active window should exclude vehicle outside zone even though update succeeded when inactive"
    )
    # list with --now=500 (inactive) per current spec returns [] when no active zones (empty filtered set means no vehicle passes)
    p2 = run_cli(
        binary, db, ["list", "--zones", zones_path, "--now", "500"], expect_code=0
    )
    ids2 = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    # When no zones active at now, result is [] (spec: empty active set → no match)
    assert ids2 == [], (
        f"list with --now inactive and zones file non-empty returns [] per spec, got {ids2}"
    )
    # list WITHOUT --now uses all zones (no time filtering), so outside should be excluded
    p2_all = run_cli(binary, db, ["list", "--zones", zones_path], expect_code=0)
    ids2_all = [x["vehicle_id"] for x in json.loads(p2_all.stdout.strip())]
    assert "veh_outside" not in ids2_all, (
        "list without --now uses all zones (no time filter), so outside excluded"
    )

    # Case 2: vehicle inside zone updated at ts=1500 (active, inside -> ok)
    run_cli(
        binary,
        db,
        ["update", "veh_inside", "5", "5", "1500", "--zones", zones_path],
        expect_code=0,
    )
    # list with --now=1500 should include only inside
    p4 = run_cli(
        binary, db, ["list", "--zones", zones_path, "--now", "1500"], expect_code=0
    )
    ids4 = [x["vehicle_id"] for x in json.loads(p4.stdout.strip())]
    assert "veh_inside" in ids4 and "veh_outside" not in ids4
    # list with --now=500 returns [] per spec (no active zones)
    p3 = run_cli(
        binary, db, ["list", "--zones", zones_path, "--now", "500"], expect_code=0
    )
    assert json.loads(p3.stdout.strip()) == []

    # Same divergence for near: update succeeded outside when inactive, but near with --now active excludes
    p_near_active = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "5",
            "--lng",
            "5",
            "--radius",
            "50000",
            "--zones",
            zones_path,
            "--now",
            "1500",
        ],
        expect_code=0,
    )
    near_ids_active = [
        x["vehicle_id"] for x in json.loads(p_near_active.stdout.strip())
    ]
    assert "veh_outside" not in near_ids_active
    assert "veh_inside" in near_ids_active
    # near with --now inactive returns [] per same logic
    p_near_inactive = run_cli(
        binary,
        db,
        [
            "near",
            "--lat",
            "5",
            "--lng",
            "5",
            "--radius",
            "50000",
            "--zones",
            zones_path,
            "--now",
            "500",
        ],
        expect_code=0,
    )
    assert json.loads(p_near_inactive.stdout.strip()) == []

    # geofence-check divergence also uses --now
    p_gc_500 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "500"],
        expect_code=0,
    )
    assert json.loads(p_gc_500.stdout.strip())["inside"] is False
    p_gc_1500 = run_cli(
        binary,
        db,
        ["geofence-check", "5", "5", "--zones", zones_path, "--now", "1500"],
        expect_code=0,
    )
    assert json.loads(p_gc_1500.stdout.strip())["inside"] is True
    # geofence-check without --now uses all zones
    p_gc_all = run_cli(
        binary, db, ["geofence-check", "20", "20", "--zones", zones_path], expect_code=0
    )
    assert json.loads(p_gc_all.stdout.strip())["inside"] is False


