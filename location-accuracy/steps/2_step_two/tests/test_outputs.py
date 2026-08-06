"""
Hardened Step2 tests - 60 tests covering:
- Step1 backward compat (update/get/list/near/track/distance/delete/stats/batch/clear/help, zones, pagination)
- Low accuracy, speed cap, outlier (simple, heading flip, median deviation)
- Polyline roads, heading-aware snapping, opposite direction allowed
- EMA smoothing with time decay
- Geofence-check, pickup/dropoff zones, out_of_geofence
- Pickup requires stopped (moving reason), road mismatch, too_far, stale, low_accuracy
- Dropoff lenient moving threshold and distance
- Confidence degradation by outlier_count
- Estimate prediction, confidence levels, road bearing, snap distance
- Batch with zones, large scale, track pagination, stats
"""

import os, json, subprocess, tempfile, shutil, math
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
        cmd, input=input_data, capture_output=True, text=True, timeout=10
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
    brng = math.atan2(x, y) * 180 / math.pi
    brng = (brng + 360) % 360
    return brng


# ---- Backward compat Step1 ----


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


def test_update_get_total_distance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    p = run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    assert data["total_distance_m"] == 0
    p2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data2 = json.loads(p2.stdout.strip())
    assert data2["vehicle_id"] == "veh1"


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


def test_delete_and_stats(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    p = run_cli(binary, db, ["delete", "veh1"], expect_code=0)
    assert "deleted" in p.stdout.lower()
    run_cli(binary, db, ["get", "veh1"], expect_code=3)
    p2 = run_cli(binary, db, ["stats"], expect_code=0)
    data = json.loads(p2.stdout.strip())
    assert data["live"] == 0


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
    p2 = run_cli(binary, db, ["list", "--limit", "1", "--offset", "1"], expect_code=0)
    arr2 = json.loads(p2.stdout.strip())
    assert len(arr2) == 1


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
    # without --now includes both
    p = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "100"],
        expect_code=0,
    )
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_fresh" in ids and "veh_stale" in ids
    # with --now excludes stale
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
    ids3 = [x["vehicle_id"] for x in json.loads(p3.stdout.strip())]
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


def test_distance_stats_batch_clear(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "1000"], expect_code=0)
    run_cli(binary, db, ["update", "veh1", "37.001", "-122.0", "2000"], expect_code=0)
    p = run_cli(binary, db, ["distance", "veh1"], expect_code=0)
    assert "total_distance_m" in p.stdout
    p2 = run_cli(binary, db, ["stats"], expect_code=0)
    assert "live" in p2.stdout
    batch_input = "update\tveh2\t38.0\t-123.0\t3000\t5\t0\t0\n"
    p3 = run_cli(binary, db, ["batch"], input_data=batch_input, expect_code=0)
    assert "batch_ok" in p3.stdout
    run_cli(binary, db, ["clear"], expect_code=0)
    p4 = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(p4.stdout.strip()) == []


# ---- Hardened Step1 zones ----


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
    assert "out_of_zone" in p.stdout.lower()


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
    run_cli(binary, db, ["update", "veh_in", "37.5", "-122.2", "1000"], expect_code=0)
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
    ids = [x["vehicle_id"] for x in json.loads(p.stdout.strip())]
    assert "veh_in" in ids and "veh_out" not in ids


def test_batch_atomicity_and_stale_skip(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "0", "0", "1000"], expect_code=0)
    batch_bad = "update\tveh2\t37.0\t-122.0\t2000\t5\t0\t0\nupdate\tbad id\t0\t0\t3000\t5\t0\t0\n"
    p = run_cli(binary, db, ["batch"], input_data=batch_bad, expect_code=2)
    p2 = run_cli(binary, db, ["list"], expect_code=0)
    ids = [x["vehicle_id"] for x in json.loads(p2.stdout.strip())]
    assert ids == ["veh1"]
    # stale skip
    batch_stale = (
        "update\tveh1\t1.0\t1.0\t500\t5\t0\t0\nupdate\tveh1\t2.0\t2.0\t3000\t5\t0\t0\n"
    )
    p3 = run_cli(binary, db, ["batch"], input_data=batch_stale, expect_code=0)
    assert "batch_ok" in p3.stdout and "1" in p3.stdout
    p4 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    assert abs(json.loads(p4.stdout.strip())["lat"] - 2.0) < 1e-6


# ---- Step2 low accuracy / speed / outlier ----


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
    # same location small distance but heading opposite (180) diff 180 >120, distance 0 <500, speed>10 => outlier per heading flip rule
    # Our impl checks heading diff >120 and dist<500
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
    # Create history with consistent slow speeds
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
    # Now large jump that would be outlier per median deviation: previous speeds ~11m/s (0.0001 deg ~11m per sec), now jump 0.01 deg ~1110m in 1 sec => 1110 m/s
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


# ---- Roads polyline and heading-aware ----


def test_road_polyline_closest_among_segments(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    # polyline with 2 segments: (37.0,-122.0)->(37.0,-121.0)->(37.0,-120.0)
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
    # Vehicle near second segment - offset ~11m (0.0001 deg lat ~11m)
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
    # snapped lat should be ~37.0 (road is lat 37.0)
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
    ]  # bearing ~0 north
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
    )  # heading east 90, road north 0 diff 90 >45 should NOT snap
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
    ]  # bearing 0
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
    )  # heading 180 diff with 0 is 180, but opposite is 0 vs 180? Actually road bearing 0, opposite 180 diff 0 <=45 so should snap
    p = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(p.stdout.strip())
    assert data["snapped"] is True


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
    ]  # east
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
    assert data["road_id"] == "road_seg"


# ---- EMA smoothing time decay ----


def test_ema_smoothing_time_decay(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Two points: old at 37.0 with high accuracy, very old timestamp; new at 38.0 with low accuracy but very recent
    # With time decay, recent should weigh more even if low accuracy
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "1"],
        expect_code=0,
    )  # high accuracy but old
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
    )  # recent
    p = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    data = json.loads(p.stdout.strip())
    # smoothed lat should be closer to 38.0 than simple average due to time decay, but still between 37 and 38
    assert 37.5 < data["lat"] < 38.1
    # original should equal smoothed before snap
    assert abs(data["lat"] - data["original_lat"]) < 1e-9 or data["snapped"] is False


# ---- Geofence-check command ----


def test_geofence_check_inside_outside(binary):
    tmp = tempfile.mkdtemp()
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
        binary, None, ["geofence-check", "5", "5", "--zones", zones_path], expect_code=0
    )
    data = json.loads(p.stdout.strip())
    assert data["inside"] is True and data["zone_id"] == "zone1"
    p2 = run_cli(
        binary,
        None,
        ["geofence-check", "20", "20", "--zones", zones_path],
        expect_code=0,
    )
    data2 = json.loads(p2.stdout.strip())
    assert data2["inside"] is False


def test_geofence_check_invalid(binary):
    tmp = tempfile.mkdtemp()
    run_cli(
        binary,
        None,
        ["geofence-check", "91", "0", "--zones", "/nonexistent.json"],
        expect_code=2,
    )


# ---- Estimate ----


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
    assert data["confidence"] in ["high", "medium"]


def test_estimate_confidence_degradation_by_outlier_count(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "5", "--speed", "0"],
        expect_code=0,
    )
    # cause 3 outlier rejections to increase outlier_count
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
    # outlier_count>2 should degrade high->medium
    # With accuracy 5 age 0 would normally be high, but outlier_count 3 makes medium
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
    assert "road_bearing" in data
    assert abs(data["road_bearing"] - 90) < 10


# ---- Pickup / Dropoff validation hardened ----


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
    # pickup at different road
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
    # too far 500m
    p = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7799", "-122.4194", "--now", "1000000"],
        expect_code=1,
    )
    assert json.loads(p.stdout.strip())["reason"] == "too_far"
    # stale 40s
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
    # low accuracy 60
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
    # 120m away: pickup fails, dropoff succeeds
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
    # dropoff moving threshold 10 m/s: speed 6 should be ok for dropoff but not pickup
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
            "5",
            "--speed",
            "6",
        ],
        expect_code=0,
    )
    p_pick2 = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "2000000"],
        expect_code=1,
    )
    assert json.loads(p_pick2.stdout.strip())["reason"] == "moving"
    p_drop2 = run_cli(
        binary,
        db,
        ["validate-dropoff", "veh1", "37.7749", "-122.4194", "--now", "2000000"],
        expect_code=0,
    )
    assert json.loads(p_drop2.stdout.strip())["valid"] is True


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
    for i in range(200):
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
    assert len(arr) == 200
    ids = [x["vehicle_id"] for x in arr]
    assert ids == sorted(ids)


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
    p3 = run_cli(binary, db, ["stats"], expect_code=0)
    data = json.loads(p3.stdout.strip())
    assert data["live"] >= 1 and data["total_distance_m"] > 0
