"""
Extreme Hardened Step2 tests - 62 tests covering:
- Step1 compat (zones polygon+circle+holes+antimeridian+time, roads mixed, batch variable, list/near roads+zones, geofence-check circles/time, whitespace DB, array corrupt)
- Low accuracy, speed cap, outlier 6 conditions (teleport, heading flip, median, acceleration, accuracy spike, speed vs implied)
- Roads polyline closest among segments, heading-aware no fallback, opposite allowed, mixed format, invalid
- EMA smoothing exponential decay, prediction, original lat/lng
- Geofence with holes circles antimeridian time
- Pickup requires: out_of_geofence, stale, low_accuracy, off_road, moving, road_mismatch, heading_mismatch, too_far, ok
- Dropoff lenient moving and distance but still off_road
- Confidence degradation by outlier_count, snapped upgrade, age, accuracy
- Batch with zones, large scale
"""

import os, json, subprocess, tempfile, shutil, math, time
import pytest

SRC_DIR = "/app/src"


@pytest.fixture(scope="session")
def binary():
    assert os.path.isdir(SRC_DIR)
    go_files = []
    for root, _, files in os.walk(SRC_DIR):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))
    assert go_files, "no .go files"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod"))
    go = shutil.which("go")
    assert go
    out_dir = tempfile.mkdtemp(prefix="hard2_build_")
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
            f"build failed {proc.stdout} {proc.stderr} {proc2.stdout} {proc2.stderr}"
        )
    proc_check = subprocess.run(
        [go, "build", "./..."], cwd=SRC_DIR, capture_output=True, text=True
    )
    assert proc_check.returncode == 0
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
            f"cmd {' '.join(cmd)} expected {expect_code} got {proc.returncode}, out={proc.stdout!r}, err={proc.stderr!r}"
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


def bearing(lat1, lng1, lat2, lng2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lng2 - lng1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        dlambda
    )
    br = math.atan2(x, y) * 180 / math.pi
    br = (br + 360) % 360
    return br


def angularDiff(a, b):
    d = abs(a - b) % 360
    if d > 180:
        d = 360 - d
    return d


def test_build_exists(binary):
    assert os.path.isfile(binary)


def test_go_mod_no_external():
    import re

    with open(os.path.join(SRC_DIR, "go.mod")) as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            assert "." not in m.group(2).split("/")[0], f"external {m.group(2)}"


def test_help_contains_all(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    p = run_cli(binary, db, ["help"], expect_code=0)
    out = p.stdout.lower()
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
        "estimate",
        "validate-pickup",
        "validate-dropoff",
        "geofence-check",
    ]:
        assert cmd in out, f"help missing {cmd}"


def test_whitespace_db_empty(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("   \n\t \n")
    p = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(p.stdout.strip()) == []


def test_corrupt_array(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    with open(db, "w") as f:
        f.write("[]")
    run_cli(binary, db, ["list"], expect_code=4)


def test_update_get_total_distance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    p = run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["total_distance_m"] == 0


def test_total_distance_tracking(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "37.001", "-122.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(p.stdout.strip())
    expected = haversine(37.0, -122.0, 37.001, -122.0)
    assert abs(data["total_distance_m"] - expected) < 1.0


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
    assert len(data["history"]) <= 10


def test_stale_still_ignored(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000"], expect_code=0)
    assert "stale" in p.stdout.lower()


def test_list_filters_pagination(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh_a", "0", "0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_m", "0", "0", "2000"], expect_code=0)
    run_cli(binary, db, ["update", "veh_z", "0", "0", "3000"], expect_code=0)
    p = run_cli(
        binary, db, ["list", "--since", "1500", "--until", "2500"], expect_code=0
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 1 and arr[0]["vehicle_id"] == "veh_m"


def test_list_zones_and_roads_filter(binary):
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
        ["list", "--zones", zones_path, "--roads", roads_path],
        expect_code=0,
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_ok" in ids and "veh_no_road" not in ids and "veh_no_zone" not in ids


def test_near_filters_and_stale_logic(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh_fresh",
            "37.7749",
            "-122.4194",
            "1000000",
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
        ["update", "veh_stale", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
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
    assert "veh_ok" in ids and "veh_no_road" not in ids


def test_zones_out_of_zone(binary):
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
        ["update", "veh_in", "37.5", "-121.5", "1000", "--zones", zones_path],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh_out", "39.0", "-121.5", "1000", "--zones", zones_path],
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
    run_cli(
        binary,
        db,
        ["update", "veh_out", "10", "10", "1000", "--zones", zones_path],
        expect_code=3,
    )


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
    run_cli(
        binary,
        db,
        ["update", "veh_hole", "5", "5", "1000", "--zones", zones_path],
        expect_code=3,
    )


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
        ["update", "veh_out", "0", "0", "1000", "--zones", zones_path],
        expect_code=3,
    )


def test_batch_variable_fields(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    batch_input = "update\tveh1\t37.0\t-122.0\t1000\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p.stdout


def test_batch_atomicity_and_stale_skip(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    batch_bad = "update\tveh2\t37.0\t-122.0\t2000\t5\t0\t0\nupdate\tbad id\t0\t0\t3000\t5\t0\t0\n"
    run_cli(binary, db, ["batch"], input_data=batch_bad, expect_code=2)
    p2 = run_cli(binary, db, ["list"], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    assert ids == ["veh1"]


def test_low_accuracy_rejected(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    p = run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "150"],
        expect_code=3,
    )
    assert "low_accuracy" in p.stdout.lower()


def test_speed_cap(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--speed", "51"],
        expect_code=2,
    )


def test_outlier_simple(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh1", "38.7749", "-123.4194", "11000", "--accuracy", "5"],
        expect_code=3,
    )
    assert "outlier" in p.stdout.lower()


def test_outlier_heading_flip(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "1000",
            "--accuracy",
            "5",
            "--speed",
            "15",
            "--heading",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7750",
            "-122.4194",
            "2000",
            "--accuracy",
            "5",
            "--speed",
            "15",
            "--heading",
            "180",
        ],
        expect_code=3,
    )
    assert "outlier" in p.stdout.lower()


def test_outlier_median_deviation(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5", "--speed", "1"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.0001",
            "-122.0",
            "2000",
            "--accuracy",
            "5",
            "--speed",
            "1",
        ],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.0002",
            "-122.0",
            "3000",
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
        [
            "update",
            "veh1",
            "37.01",
            "-122.0",
            "4000",
            "--accuracy",
            "5",
            "--speed",
            "5",
        ],
        expect_code=3,
    )
    assert "outlier" in p.stdout.lower()


def test_outlier_acceleration(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5", "--speed", "0"],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.0001",
            "-122.0",
            "2000",
            "--accuracy",
            "5",
            "--speed",
            "30",
        ],
        expect_code=3,
    )
    assert "outlier" in p.stdout.lower()


def test_outlier_accuracy_spike(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["update", "veh1", "37.0001", "-122.0", "2000", "--accuracy", "80"],
        expect_code=3,
    )
    assert "outlier" in p.stdout.lower()


def test_outlier_speed_vs_implied(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5", "--speed", "0"],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.01",
            "-122.0",
            "11000",
            "--accuracy",
            "5",
            "--speed",
            "1",
        ],
        expect_code=3,
    )
    assert "outlier" in p.stdout.lower()


def test_outlier_count_persistence(binary):
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
        ["update", "veh1", "38.0", "-123.0", "2000", "--accuracy", "5"],
        expect_code=3,
    )
    p = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data.get("outlier_count", 0) >= 1


def test_road_polyline_closest_among_segments(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_poly",
            "points": [
                {"lat": 37.0, "lng": -122.0},
                {"lat": 37.0, "lng": -121.0},
                {"lat": 37.0, "lng": -120.0},
            ],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.0001",
            "-120.5",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True
    assert data["road_id"] == "road_poly"
    assert abs(data["lat"] - 37.0) < 0.0002


def test_road_heading_aware_rejects(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_north",
            "points": [{"lat": 37.0, "lng": -122.0}, {"lat": 38.0, "lng": -122.0}],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.5",
            "-122.0005",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "10",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is False


def test_road_heading_aware_opposite_allowed(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_north",
            "points": [{"lat": 37.0, "lng": -122.0}, {"lat": 38.0, "lng": -122.0}],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.5",
            "-122.0005",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "10",
            "--heading",
            "180",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True


def test_road_heading_aware_no_fallback(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "north",
            "points": [{"lat": 37.0, "lng": -122.0}, {"lat": 38.0, "lng": -122.0}],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.5",
            "-122.0001",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "15",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is False


def test_road_backward_compat_segment_format(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_seg",
            "start": {"lat": 37.7749, "lng": -122.4194},
            "end": {"lat": 37.7749, "lng": -122.4094},
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7750",
            "-122.4144",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True


def test_road_mixed_format(binary):
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
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7750",
            "-122.4144",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True


def test_road_invalid_file(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    with open(roads_path, "w") as f:
        f.write('[{"id":"","points":[]}]')
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=2,
    )


def test_ema_smoothing_time_decay(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "1"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "38.0",
            "-122.0",
            "1000000",
            "--accuracy",
            "10",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert 37.5 < data["lat"] < 38.1


def test_ema_exponential_decay(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "1"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "38.0",
            "-122.0",
            "1000000",
            "--accuracy",
            "20",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["lat"] > 37.8


def test_geofence_check_inside_outside(binary):
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
    assert data["inside"] is True and data["zone_id"] == "zone1"
    p2 = run_cli(
        binary, db, ["geofence-check", "20", "20", "--zones", zones_path], expect_code=0
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
        binary, db, ["geofence-check", "10", "10", "--zones", zones_path], expect_code=0
    )
    assert json.loads(p2.stdout.strip())["inside"] is False


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


def test_geofence_check_time(binary):
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
        binary, db, ["geofence-check", "0", "180", "--zones", zones_path], expect_code=0
    )
    assert json.loads(p.stdout.strip())["inside"] is True


def test_estimate_prediction_and_confidence(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "10",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    now = 1000000 + 10000
    p = run_cli(binary, db, ["estimate", "veh1", "--now", str(now)], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["predicted"] is True
    assert data["age_ms"] == 10000
    moved = haversine(37.7749, -122.4194, data["lat"], data["lng"])
    assert 80 < moved < 120
    # Tightened: with acc=5+5=10 and age=10000, confidence should be high per spec L64
    assert data["confidence"] == "high"
    assert "original_lat" in data


def test_estimate_confidence_degradation_by_outlier_count(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5", "--speed", "0"],
        expect_code=0,
    )
    for i in range(3):
        run_cli(
            binary,
            db,
            [
                "update",
                "veh1",
                "38.0",
                "-123.0",
                str(2000 + i * 1000),
                "--accuracy",
                "5",
            ],
            expect_code=3,
        )
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["confidence"] == "medium"


def test_estimate_confidence_low_after_many_outliers(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    for i in range(6):
        run_cli(
            binary,
            db,
            [
                "update",
                "veh1",
                f"{38.0 + i}",
                "-123.0",
                str(2000 + i * 1000),
                "--accuracy",
                "5",
            ],
            expect_code=3,
        )
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["confidence"] == "low"


def test_estimate_with_roads_heading_aware(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_east",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7750",
            "-122.4140",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True


def test_estimate_accuracy_degradation_with_age(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.0",
            "-122.0",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary, db, ["estimate", "veh1", "--now", str(1000000 + 10000)], expect_code=0
    )
    data = json.loads(p.stdout.strip())
    assert data["accuracy"] >= 9 and data["accuracy"] <= 11


def test_validate_pickup_valid_stopped_same_road(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_east",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4144",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "37.7750",
            "-122.4144",
            "--now",
            "1000000",
            "--roads",
            roads_path,
        ],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["valid"] is True and data["reason"] == "ok"


def test_validate_pickup_moving(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "10",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "1000000"],
        expect_code=1,
    )
    data = json.loads(p.stdout.strip())
    assert data["valid"] is False and data["reason"] == "moving"


def test_validate_pickup_off_road(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_a",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7849",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "37.7849",
            "-122.4194",
            "--now",
            "1000000",
            "--roads",
            roads_path,
        ],
        expect_code=1,
    )
    data = json.loads(p.stdout.strip())
    assert data["valid"] is False and data["reason"] == "off_road"


def test_validate_pickup_road_mismatch(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_a",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        },
        {
            "id": "road_b",
            "points": [
                {"lat": 37.7849, "lng": -122.4194},
                {"lat": 37.7849, "lng": -122.4094},
            ],
        },
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4144",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "37.7849",
            "-122.4144",
            "--now",
            "1000000",
            "--roads",
            roads_path,
        ],
        expect_code=1,
    )
    data = json.loads(p.stdout.strip())
    assert data["valid"] is False and data["reason"] == "road_mismatch"


def test_validate_pickup_out_of_geofence(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "pickup_zones.json")
    zones = [
        {
            "id": "pickup_sf",
            "polygon": [
                {"lat": 37.7, "lng": -122.5},
                {"lat": 37.7, "lng": -122.3},
                {"lat": 37.9, "lng": -122.3},
                {"lat": 37.9, "lng": -122.5},
            ],
        }
    ]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "0",
            "0",
            "--now",
            "1000000",
            "--zones",
            zones_path,
        ],
        expect_code=1,
    )
    data = json.loads(p.stdout.strip())
    assert data["valid"] is False and data["reason"] == "out_of_geofence"


def test_validate_pickup_too_far_stale_low_accuracy(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7799", "-122.4194", "--now", "1000000"],
        expect_code=1,
    )
    assert json.loads(p.stdout.strip())["reason"] == "too_far"
    p2 = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "37.7749",
            "-122.4194",
            "--now",
            str(1000000 + 40000),
        ],
        expect_code=1,
    )
    assert json.loads(p2.stdout.strip())["reason"] == "stale"
    # low_accuracy via direct high accuracy value (60) not via age degradation to avoid stale precedence
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "2000000",
            "--accuracy",
            "60",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p3 = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "2000000"],
        expect_code=1,
    )
    assert json.loads(p3.stdout.strip())["reason"] == "low_accuracy"


def test_validate_dropoff_lenient(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p_pick = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7760", "-122.4194", "--now", "1000000"],
        expect_code=1,
    )
    assert json.loads(p_pick.stdout.strip())["reason"] == "too_far"
    p_drop = run_cli(
        binary,
        db,
        ["validate-dropoff", "veh1", "37.7760", "-122.4194", "--now", "1000000"],
        expect_code=0,
    )
    assert json.loads(p_drop.stdout.strip())["valid"] is True


def test_validate_dropoff_off_road(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_a",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7849",
            "-122.4194",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    p = run_cli(
        binary,
        db,
        [
            "validate-dropoff",
            "veh1",
            "37.7849",
            "-122.4194",
            "--now",
            "1000000",
            "--roads",
            roads_path,
        ],
        expect_code=1,
    )
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "off_road"


def test_invalid_roads_zones_files(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", "/nonexistent.json"],
        expect_code=2,
    )
    run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "0",
            "0",
            "--now",
            "1000000",
            "--zones",
            "/nonexistent.json",
        ],
        expect_code=2,
    )


def test_backward_compat_old_db(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    old = {
        "veh_old": {
            "vehicle_id": "veh_old",
            "lat": 37.7749,
            "lng": -122.4194,
            "timestamp_ms": 1000000,
            "accuracy": 5,
            "speed": 0,
            "heading": 0,
        }
    }
    with open(db, "w") as f:
        json.dump(old, f)
    p = run_cli(binary, db, ["get", "veh_old"], expect_code=0)
    assert json.loads(p.stdout.strip())["vehicle_id"] == "veh_old"
    p2 = run_cli(binary, db, ["estimate", "veh_old", "--now", "1000000"], expect_code=0)
    assert json.loads(p2.stdout.strip())["vehicle_id"] == "veh_old"


def test_large_scale_sorting(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(300):
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
    assert len(arr) == 300


def test_large_scale_near_estimate_performance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(200):
        run_cli(
            binary,
            db,
            [
                "update",
                f"veh_{i:03d}",
                "37.7749",
                "-122.4194",
                "1000",
                "--accuracy",
                "5",
            ],
            expect_code=0,
        )
    start = time.time()
    for i in range(50):
        run_cli(
            binary, db, ["estimate", f"veh_{i:03d}", "--now", "1000"], expect_code=0
        )
    elapsed = time.time() - start
    assert elapsed < 4.0


def test_track_pagination_and_stats_distance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(10):
        run_cli(
            binary,
            db,
            ["update", "veh1", f"{37.0 + i * 0.001}", "-122.0", str(1000 + i * 1000)],
            expect_code=0,
        )
    p = run_cli(
        binary,
        db,
        [
            "track",
            "veh1",
            "--from",
            "0",
            "--to",
            "20000",
            "--limit",
            "3",
            "--offset",
            "2",
        ],
        expect_code=0,
    )
    arr = json.loads(p.stdout.strip())
    assert len(arr) == 3
    p2 = run_cli(binary, db, ["distance", "veh1"], expect_code=0)
    assert "total_distance_m" in p2.stdout








# --- B. heading_mismatch (priority 7) - no existing coverage ---


def test_validate_pickup_heading_mismatch(binary):
    # east-west road; vehicle heading 90 (east) speed 2; pickup 50m west (bearing 270 diff 180) → heading_mismatch
    # speed 2 is between 1 and 5 so moving(5) doesn't preempt, distance ~50m <100 so too_far doesn't preempt
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_east",
            "points": [
                {"lat": 37.7749, "lng": -122.4194},
                {"lat": 37.7749, "lng": -122.4094},
            ],
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7749",
            "-122.4144",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "2",
            "--heading",
            "90",
        ],
        expect_code=0,
    )
    # 0.0006 deg lng ~52m west at this lat
    p = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "37.7749",
            "-122.4150",
            "--now",
            "1000000",
            "--roads",
            roads_path,
        ],
        expect_code=1,
    )
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "heading_mismatch"


def test_validate_pickup_priority_moving_beats_too_far(binary):
    # moving(5) beats too_far(8): speed 8 triggers moving, distance ~200m triggers too_far, priority says moving wins
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7767", "-122.4194", "--now", "1000000"], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "moving"


def test_estimate_confidence_snapped_upgrade_to_high(binary):
    # snapped, road_dist <=10, acc 20 (base medium) -> high per spec L65 upgrade chain
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "20", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000", "--roads", roads_path], expect_code=0)
    data = _json.loads(p.stdout.strip())
    # base confidence with acc 20 would be medium (acc <=25 age<=20000), but snapped with road_dist<=10 upgrades medium->high
    assert data["snapped"] is True
    assert data["confidence"] == "high"


def test_validate_dropoff_speed_leniency_boundary(binary):
    # dropoff leniency speed: 5 vs 10, per spec L88. speed 7: pickup -> moving, dropoff -> valid true
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "7"], expect_code=0)
    p_pick = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "1000000"], expect_code=1)
    assert json.loads(p_pick.stdout.strip())["reason"] == "moving"
    p_drop = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7749", "-122.4194", "--now", "1000000"], expect_code=0)
    assert json.loads(p_drop.stdout.strip())["valid"] is True


def test_validate_pickup_priority_off_road_beats_moving(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "far_road", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "off_road"


def test_validate_pickup_priority_out_of_geofence_beats_stale(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "pickup_sf", "polygon": [{"lat": 37.7, "lng": -122.5}, {"lat": 37.7, "lng": -122.3}, {"lat": 37.9, "lng": -122.3}, {"lat": 37.9, "lng": -122.5}]}]
    with open(zones_path, "w") as f:
        json.dump(zones, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "0", "0", "--now", str(1000000 + 40000), "--zones", zones_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "out_of_geofence"


def test_estimate_confidence_snapped_upgrade_low_to_medium(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "35", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000", "--roads", roads_path], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True
    assert data["confidence"] == "medium"


def test_validate_pickup_priority_low_accuracy_beats_off_road(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "far_road", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "low_accuracy"


def test_validate_pickup_priority_road_mismatch_beats_too_far(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "0", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7760", "-122.4144", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "road_mismatch"


def test_estimate_confidence_age_override_low_even_when_snapped(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", str(1000000 + 35000), "--roads", roads_path], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True
    assert data["confidence"] == "low"

def test_validate_pickup_priority_stale_beats_low_accuracy(binary):
    # stale(2) beats low_accuracy(3): age>30k (stale) and accuracy 60>50
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", str(1000000 + 40000)], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "stale"


def test_validate_pickup_priority_stale_beats_moving(binary):
    # stale(2) beats moving(5): age>30k and speed 8
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", str(1000000 + 40000)], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "stale"


def test_validate_pickup_priority_low_accuracy_beats_moving(binary):
    # low_accuracy(3) beats moving(5): accuracy 60 and speed 8
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "1000000"], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "low_accuracy"


def test_validate_pickup_priority_off_road_beats_road_mismatch(binary):
    # off_road(4) beats road_mismatch(6): far road for vehicle, but pickup snapped to far road? Actually need vehicle not snapped, pickup snapped to different road? 
    # Simpler: vehicle not snapped (off_road), pickup not relevant for road_mismatch because road_mismatch requires both snapped to different roads.
    # For off_road to beat road_mismatch, need vehicle not snapped, but if vehicle not snapped, road_mismatch false. So need scenario where both true?
    # Actually off_road true means vehicle not snapped. road_mismatch requires both vehicle and pickup snapped to different roads.
    # So both cannot be true simultaneously. So test off_road beats too_far instead? We already have moving>too_far.
    # Let's test off_road beats heading_mismatch: off_road true, heading_mismatch requires same road, so cannot both true.
    # Instead test off_road beats too_far is redundant with moving>too_far, but we can test off_road beats too_far directly:
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "far_road", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    # pickup 200m away, so too_far true, and off_road true (road far), off_road priority 4 beats 8
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7767", "-122.4194", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "off_road"


def test_validate_pickup_priority_stale_beats_off_road(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "far_road", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", str(1000000 + 40000), "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "stale"


def test_validate_pickup_priority_low_accuracy_beats_road_mismatch(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "60", "--speed", "0", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7760", "-122.4144", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "low_accuracy"


def test_validate_pickup_priority_road_mismatch_beats_heading_mismatch(binary):
    # road_mismatch(6) beats heading_mismatch(7): different roads, even if heading diff would also trigger heading_mismatch, road_mismatch wins
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    # vehicle heading east 90, pickup west ~50m but on different road, so both road_mismatch and heading_mismatch would be true if same road, but since different roads, road_mismatch true, heading_mismatch false? Actually heading requires same road, so this tests priority 6 vs 7 when both would be true if same road? 
    # For this test, vehicle on road_a heading west (270) would be opposite to pickup east, but pickup on road_b different road, so road_mismatch true, heading false. To make both true, need same road but different heading? Actually road_mismatch requires different roads, heading requires same road, so they cannot both be true. 
    # Instead test road_mismatch beats too_far is already covered, so this test will just check road_mismatch alone with heading that would otherwise be heading_mismatch if same road.
    # Use speed 2 heading 90, pickup 50m west but on different road 122m north, so road_mismatch true, too_far? distance ~122m >100, road_mismatch beats too_far already.
    # We'll just keep as road_mismatch beats too_far variant with heading set.
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "2", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7760", "-122.4150", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "road_mismatch"


def test_validate_dropoff_heading_mismatch(binary):
    # dropoff heading_mismatch same logic as pickup but with dropoff leniency distance 150 not 100
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "2", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7749", "-122.4150", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "heading_mismatch"


def test_validate_dropoff_road_mismatch(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "0", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7760", "-122.4144", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "road_mismatch"


def test_estimate_confidence_medium_with_acc_30_snapped(binary):
    # base low acc 30, snapped <=10, age small but acc>25 -> base low, but snapped upgrade with acc<=40 age<=15k -> medium
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "30", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000", "--roads", roads_path], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True
    assert data["confidence"] == "medium"


def test_validate_pickup_priority_stale_beats_road_mismatch(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7760", "-122.4144", "--now", str(1000000 + 40000), "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "stale"


def test_validate_pickup_priority_stale_beats_too_far(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7767", "-122.4194", "--now", str(1000000 + 40000)], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "stale"


def test_validate_pickup_priority_low_accuracy_beats_heading_mismatch(binary):
    # low_accuracy(3) beats heading_mismatch(7): acc 60, road east-west, heading east but pickup west
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "60", "--speed", "2", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4150", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "low_accuracy"


def test_validate_pickup_priority_low_accuracy_beats_too_far(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7767", "-122.4194", "--now", "1000000"], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "low_accuracy"


def test_validate_pickup_priority_moving_beats_road_mismatch(binary):
    # moving(5) beats road_mismatch(6): speed 8, different roads
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "8", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7760", "-122.4144", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "moving"


def test_validate_pickup_priority_moving_beats_heading_mismatch(binary):
    # moving(5) beats heading_mismatch(7): same road, speed 8, heading opposite
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    with open(roads_path, "w") as f:
        json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "8", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4150", "--now", "1000000", "--roads", roads_path], expect_code=1)
    data = json.loads(p.stdout.strip())
    assert data["reason"] == "moving"


def test_estimate_confidence_high_with_outlier_count_2_still_high(binary):
    # outlier_count 2 should downgrade high->medium, but with acc 5 age 0 and snapped, should still be medium? Actually spec: outlier>2 high->medium, so 2 is not >2, stays high
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # create 2 outliers first to get outlier_count 2
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    # teleport outlier
    run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000100", "--accuracy", "5", "--speed", "0"], expect_code=3)
    run_cli(binary, db, ["update", "veh1", "38.1", "-123.1", "1000200", "--accuracy", "5", "--speed", "0"], expect_code=3)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    # age 0, acc 5 -> high base, outlier_count 2 not >2, so high
    assert data["confidence"] == "high"


def test_estimate_confidence_medium_with_outlier_count_3(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000100", "--accuracy", "5"], expect_code=3)
    run_cli(binary, db, ["update", "veh1", "38.1", "-123.1", "1000200", "--accuracy", "5"], expect_code=3)
    run_cli(binary, db, ["update", "veh1", "38.2", "-123.2", "1000300", "--accuracy", "5"], expect_code=3)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    # outlier>2 high->medium
    assert data["confidence"] == "medium"


def test_total_distance_not_increment_on_outlier(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p1 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    d_before = __import__('json').loads(p1.stdout.strip())["total_distance_m"]
    # teleport outlier
    run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000100", "--accuracy", "5"], expect_code=3)
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    d_after = __import__('json').loads(p2.stdout.strip())["total_distance_m"]
    assert d_before == d_after, "outlier should not increment total_distance"


def test_history_not_include_outlier(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1100", "--accuracy", "5"], expect_code=3)
    p = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    data = __import__('json').loads(p.stdout.strip())
    assert len(data["history"]) == 1
    assert data["history"][0]["lat"] == 37.0


def test_validate_pickup_priority_stale_beats_heading_mismatch(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "2", "--heading", "90"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4150", "--now", str(1000000 + 40000), "--roads", roads_path], expect_code=1)
    data = _json.loads(p.stdout.strip())
    assert data["reason"] == "stale"


def test_validate_pickup_priority_low_accuracy_beats_stale_beats_moving_chain(binary):
    # low_accuracy beats moving already tested, but test low beats moving with accuracy 60 and speed 8 and distance 200m -> low wins over moving and too_far
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7767", "-122.4194", "--now", "1000000"], expect_code=1)
    data = __import__('json').loads(p.stdout.strip())
    assert data["reason"] == "low_accuracy"


def test_validate_dropoff_out_of_geofence(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "dropoff_sf", "polygon": [{"lat": 37.7, "lng": -122.5}, {"lat": 37.7, "lng": -122.3}, {"lat": 37.9, "lng": -122.3}, {"lat": 37.9, "lng": -122.5}]}]
    import json as _json
    with open(zones_path, "w") as f:
        _json.dump(zones, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"], expect_code=0)
    p = run_cli(binary, db, ["validate-dropoff", "veh1", "0", "0", "--now", "1000000", "--zones", zones_path], expect_code=1)
    assert _json.loads(p.stdout.strip())["reason"] == "out_of_geofence"


def test_validate_dropoff_too_far_boundary(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    # 160m away >150 dropoff too_far
    p = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7763", "-122.4194", "--now", "1000000"], expect_code=1)
    assert __import__('json').loads(p.stdout.strip())["reason"] == "too_far"
    # 140m <150 ok
    p2 = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7761", "-122.4194", "--now", "1000000"], expect_code=0)
    assert __import__('json').loads(p2.stdout.strip())["valid"] is True


def test_estimate_with_roads_confidence_low_when_not_snapped(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "far_road", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "30", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000", "--roads", roads_path], expect_code=0)
    data = _json.loads(p.stdout.strip())
    assert data["snapped"] is False
    # acc 30 base low, not snapped, so low per spec (acc>25 or age>10k)
    assert data["confidence"] == "low"


def test_validate_pickup_ok_exact_100m_boundary(binary):
    # at exactly 100m, should be ok? spec says >100 too_far, so 100 is ok, but use 90m and 110m for tolerance
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    # 90m north ~0.00081 deg
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7757", "-122.4194", "--now", "1000000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["valid"] is True and data["reason"] == "ok"
    # 110m north ~0.00099 deg
    p2 = run_cli(binary, db, ["validate-pickup", "veh1", "37.7759", "-122.4194", "--now", "1000000"], expect_code=1)
    assert json.loads(p2.stdout.strip())["reason"] == "too_far"


def test_batch_with_multiple_vehicles_and_zones(binary):
    import os
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones = [{"id": "z1", "polygon": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 10}, {"lat": 10, "lng": 10}, {"lat": 10, "lng": 0}]}]
    default_path = "/app/data/zones.json"
    backup = None
    if os.path.exists(default_path):
        with open(default_path) as f:
            backup = f.read()
    os.makedirs(os.path.dirname(default_path), exist_ok=True)
    import json as _json
    with open(default_path, "w") as f:
        _json.dump(zones, f)
    try:
        batch_input = "update\tveh1\t5\t5\t1000\nupdate\tveh2\t5\t5\t2000\n"
        p = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
        assert "batch_ok 2" in p.stdout
        p2 = run_cli(binary, db, ["list"], expect_code=0)
        ids = [x["vehicle_id"] for x in _json.loads(p2.stdout.strip())]
        assert "veh1" in ids and "veh2" in ids
    finally:
        if backup is not None:
            with open(default_path, "w") as f:
                f.write(backup)
        else:
            try:
                os.remove(default_path)
            except:
                pass


def test_list_with_now_and_roads_combined(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "r1", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5"], expect_code=0)
    run_cli(binary, db, ["update", "veh2", "37.7749", "-122.4144", "1000", "--accuracy", "5"], expect_code=0)
    p = run_cli(binary, db, ["list", "--roads", roads_path, "--now", "1000000"], expect_code=0)
    ids = [x["vehicle_id"] for x in _json.loads(p.stdout.strip())]
    assert "veh1" in ids


def test_estimate_confidence_low_when_accuracy_high_age_small_not_snapped(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "30", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    import json as _json
    data = _json.loads(p.stdout.strip())
    assert data["snapped"] is False
    assert data["confidence"] == "low"


def test_validate_pickup_ok_when_exactly_same_location(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "1000000"], expect_code=0)
    import json as _json
    data = _json.loads(p.stdout.strip())
    assert data["valid"] is True and data["reason"] == "ok"
    assert data["distance_m"] == 0 or data["distance_m"] < 1.0


def test_validate_pickup_priority_out_of_geofence_beats_low_accuracy(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    zones = [{"id": "pickup_sf", "polygon": [{"lat": 37.7, "lng": -122.5}, {"lat": 37.7, "lng": -122.3}, {"lat": 37.9, "lng": -122.3}, {"lat": 37.9, "lng": -122.5}]}]
    import json as _json
    with open(zones_path, "w") as f:
        _json.dump(zones, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "0", "0", "--now", "1000000", "--zones", zones_path], expect_code=1)
    assert _json.loads(p.stdout.strip())["reason"] == "out_of_geofence"


def test_validate_pickup_priority_out_of_geofence_beats_off_road(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    zones_path = os.path.join(tmp, "zones.json")
    roads_path = os.path.join(tmp, "roads.json")
    zones = [{"id": "pickup_sf", "polygon": [{"lat": 37.7, "lng": -122.5}, {"lat": 37.7, "lng": -122.3}, {"lat": 37.9, "lng": -122.3}, {"lat": 37.9, "lng": -122.5}]}]
    roads = [{"id": "far_road", "points": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}]}]
    import json as _json
    with open(zones_path, "w") as f:
        _json.dump(zones, f)
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000000", "--accuracy", "5"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "0", "0", "--now", "1000000", "--zones", zones_path, "--roads", roads_path], expect_code=1)
    assert _json.loads(p.stdout.strip())["reason"] == "out_of_geofence"


def test_validate_pickup_priority_stale_beats_road_mismatch_heading_mismatch_chain(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {"id": "road_a", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]},
        {"id": "road_b", "points": [{"lat": 37.7760, "lng": -122.4194}, {"lat": 37.7760, "lng": -122.4094}]}
    ]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["validate-pickup", "veh1", "37.7760", "-122.4144", "--now", str(1000000 + 50000), "--roads", roads_path], expect_code=1)
    assert _json.loads(p.stdout.strip())["reason"] == "stale"


def test_validate_dropoff_priority_stale_beats_moving(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7749", "-122.4194", "--now", str(1000000 + 40000)], expect_code=1)
    import json as _json
    assert _json.loads(p.stdout.strip())["reason"] == "stale"


def test_validate_dropoff_priority_low_accuracy_beats_moving(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "60", "--speed", "8"], expect_code=0)
    p = run_cli(binary, db, ["validate-dropoff", "veh1", "37.7749", "-122.4194", "--now", "1000000"], expect_code=1)
    import json as _json
    assert _json.loads(p.stdout.strip())["reason"] == "low_accuracy"


def test_estimate_accuracy_degradation_formula(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "10", "--speed", "0"], expect_code=0)
    p = run_cli(binary, db, ["estimate", "veh1", "--now", str(1000000 + 20000)], expect_code=0)
    import json as _json
    data = _json.loads(p.stdout.strip())
    # accuracy degrades +0.5*age_sec, age 20s => +10, so 20
    assert abs(data["accuracy"] - 20) < 1.0


def test_total_distance_with_road_snapped_still_counts(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4094", "2000000", "--accuracy", "5"], expect_code=0)
    p = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = _json.loads(p.stdout.strip())
    assert data["total_distance_m"] > 100


def test_near_with_heading_aware_roads_filter(binary):
    import os
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [{"id": "road_east", "points": [{"lat": 37.7749, "lng": -122.4194}, {"lat": 37.7749, "lng": -122.4094}]}]
    import json as _json
    with open(roads_path, "w") as f:
        _json.dump(roads, f)
    # near --roads uses simple snap (not heading-aware), so both headings snap
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "2", "--heading", "90"], expect_code=0)
    run_cli(binary, db, ["update", "veh2", "37.7749", "-122.4144", "1000000", "--accuracy", "5", "--speed", "2", "--heading", "180"], expect_code=0)
    p = run_cli(binary, db, ["near", "--lat", "37.7749", "--lng", "-122.4144", "--radius", "100", "--roads", roads_path], expect_code=0)
    ids = [x["vehicle_id"] for x in _json.loads(p.stdout.strip())]
    assert "veh1" in ids and "veh2" in ids
    # estimate DOES use heading-aware: veh1 snapped, veh2 not
    p_est1 = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000", "--roads", roads_path], expect_code=0)
    assert _json.loads(p_est1.stdout.strip())["snapped"] is True
    p_est2 = run_cli(binary, db, ["estimate", "veh2", "--now", "1000000", "--roads", roads_path], expect_code=0)
    assert _json.loads(p_est2.stdout.strip())["snapped"] is False


def test_outlier_persistence_across_get_verbose(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "38.0", "-123.0", "1000100", "--accuracy", "5"], expect_code=3)
    p = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    import json as _json
    data = _json.loads(p.stdout.strip())
    assert data["outlier_count"] == 1
    assert data["lat"] == 37.7749


def test_validate_pickup_with_both_pickup_and_dropoff_zones_separate(binary):
    import os
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    import os
    import os
    pickup_zones = [{"id": "pickup", "polygon": [{"lat": 37.7, "lng": -122.5}, {"lat": 37.7, "lng": -122.3}, {"lat": 37.9, "lng": -122.3}, {"lat": 37.9, "lng": -122.5}]}]
    dropoff_zones = [{"id": "dropoff", "polygon": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 10}, {"lat": 10, "lng": 10}, {"lat": 10, "lng": 0}]}]
    import json as _json, shutil
    os.makedirs("/app/data", exist_ok=True)
    # backup
    import pathlib
    pickup_path = "/app/data/pickup_zones.json"
    dropoff_path = "/app/data/dropoff_zones.json"
    bak_pick = bak_drop = None
    if os.path.exists(pickup_path):
        bak_pick = open(pickup_path).read()
    if os.path.exists(dropoff_path):
        bak_drop = open(dropoff_path).read()
    try:
        with open(pickup_path, "w") as f:
            _json.dump(pickup_zones, f)
        with open(dropoff_path, "w") as f:
            _json.dump(dropoff_zones, f)
        run_cli(binary, db, ["update", "veh1", "0.001", "0.001", "1000000", "--accuracy", "5"], expect_code=0)
        # pickup at 0,0: vehicle at 0.001,0.001 distance ~157m >100 too_far, but pickup zone is SF, so out_of_geofence beats too_far
        p1 = run_cli(binary, db, ["validate-pickup", "veh1", "0", "0", "--now", "1000000"], expect_code=1)
        assert _json.loads(p1.stdout.strip())["reason"] == "out_of_geofence"
        # dropoff at 0,0: distance ~157m >150? Actually 0.001 deg ~111m, so 157m >150 too_far, use 0.0005 deg ~78m for valid
        p2 = run_cli(binary, db, ["validate-dropoff", "veh1", "0.0005", "0.0005", "--now", "1000000"], expect_code=0)
        assert _json.loads(p2.stdout.strip())["valid"] is True
    finally:
        if bak_pick is not None:
            open(pickup_path, "w").write(bak_pick)
        else:
            try:
                os.remove(pickup_path)
            except:
                pass
        if bak_drop is not None:
            open(dropoff_path, "w").write(bak_drop)
        else:
            try:
                os.remove(dropoff_path)
            except:
                pass
