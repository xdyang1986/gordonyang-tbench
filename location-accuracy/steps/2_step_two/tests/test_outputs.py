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
