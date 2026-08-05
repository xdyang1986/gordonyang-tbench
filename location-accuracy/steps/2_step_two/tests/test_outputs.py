"""
Tests for Step 2: Improve Location Accuracy
Covers:
- Backward compatibility with Step1 commands
- Low accuracy filter
- Outlier / teleport detection
- Speed sanity cap
- History maintenance
- Stale detection via --now and --include-stale
- Road snapping
- Estimation with prediction and confidence
- Pickup/dropoff validation
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
    out_dir = tempfile.mkdtemp(prefix="locationctl_build2_")
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
    assert proc_check.returncode == 0, f"go build ./... failed"
    return bin_path


def run_cli(binary, db_path, args, expect_code=None):
    cmd = [binary, "--db", db_path] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if expect_code is not None:
        assert proc.returncode == expect_code, (
            f"cmd {' '.join(cmd)} expected {expect_code} got {proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}"
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


# ---- Backward Compatibility ----


def test_step1_commands_still_work(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    proc = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(proc.stdout.strip())
    assert data["vehicle_id"] == "veh1"
    proc2 = run_cli(binary, db, ["list"], expect_code=0)
    arr = json.loads(proc2.stdout.strip())
    assert len(arr) == 1
    proc3 = run_cli(
        binary,
        db,
        ["near", "--lat", "37.7749", "--lng", "-122.4194", "--radius", "100"],
        expect_code=0,
    )
    arr3 = json.loads(proc3.stdout.strip())
    assert len(arr3) >= 1


def test_stale_still_ignored(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(binary, db, ["update", "veh1", "37.0", "-122.0", "2000"], expect_code=0)
    proc = run_cli(
        binary, db, ["update", "veh1", "38.0", "-123.0", "1000"], expect_code=0
    )
    assert "stale" in proc.stdout.lower()


# ---- Accuracy Filtering ----


def test_low_accuracy_rejected(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # accuracy 150 > 100 threshold should be rejected
    proc = run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "150"],
        expect_code=3,
    )
    assert "low_accuracy" in proc.stdout.lower()
    # DB should stay empty
    proc2 = run_cli(binary, db, ["list"], expect_code=0)
    assert json.loads(proc2.stdout.strip()) == []


def test_low_accuracy_boundary(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # accuracy exactly 100 should be accepted (threshold >100 rejects)
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--accuracy", "100"],
        expect_code=0,
    )
    # 100.1 should be rejected
    run_cli(
        binary,
        db,
        ["update", "veh2", "37.0", "-122.0", "1000", "--accuracy", "100.1"],
        expect_code=3,
    )


def test_speed_sanity_cap(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # speed >50 should be invalid argument exit 2
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--speed", "51"],
        expect_code=2,
    )
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--speed", "100"],
        expect_code=2,
    )
    # speed 50 should be okay
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.0", "-122.0", "1000", "--speed", "50"],
        expect_code=0,
    )


# ---- Outlier Detection ----


def test_outlier_rejected(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # First location SF
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    # Second location ~100km away, only 10s later -> implied speed ~10000 m/s >50, dt<300, distance>1000, both accuracies <50 => outlier
    proc = run_cli(
        binary,
        db,
        ["update", "veh1", "38.7749", "-123.4194", "11000", "--accuracy", "5"],
        expect_code=3,
    )
    assert "outlier" in proc.stdout.lower()
    # Verify old location still kept
    proc2 = run_cli(binary, db, ["get", "veh1"], expect_code=0)
    data = json.loads(proc2.stdout.strip())
    assert abs(data["lat"] - 37.7749) < 1e-6


def test_outlier_not_triggered_if_dt_large(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    # Same jump but dt = 400s (>300) should NOT be outlier, allow
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "38.7749",
            "-123.4194",
            str(1000 + 400 * 1000),
            "--accuracy",
            "5",
        ],
        expect_code=0,
    )


def test_outlier_not_triggered_if_low_accuracy_old(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # old accuracy >=50, should not trigger outlier logic (requires both <50)
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000", "--accuracy", "60"],
        expect_code=0,
    )
    proc = run_cli(
        binary,
        db,
        ["update", "veh1", "38.7749", "-123.4194", "11000", "--accuracy", "5"],
        expect_code=0,
    )
    # Should succeed because old accuracy high enough
    data = json.loads(proc.stdout.strip())
    assert abs(data["lat"] - 38.7749) < 1e-6


def test_outlier_not_triggered_small_distance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    # Small distance 100m in 10s => 10 m/s <50, not outlier
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7750", "-122.4195", "11000", "--accuracy", "5"],
        expect_code=0,
    )


# ---- History ----


def test_history_maintained(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    for i in range(7):
        run_cli(
            binary,
            db,
            [
                "update",
                "veh1",
                f"{37.0 + i * 0.001}",
                "-122.0",
                str(1000 + i * 1000),
                "--accuracy",
                "5",
            ],
            expect_code=0,
        )
    # Get verbose should show history of up to 5
    proc = run_cli(binary, db, ["get", "veh1", "--verbose"], expect_code=0)
    try:
        data = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        # fallback: maybe verbose still prints base? then check raw db file for history
        with open(db) as f:
            raw = json.load(f)
        assert "veh1" in raw
        entry = raw["veh1"]
        assert "history" in entry
        assert len(entry["history"]) <= 5
        assert len(entry["history"]) >= 1
        return
    # If verbose returns history field
    if "history" in data:
        assert len(data["history"]) <= 5


# ---- Stale Detection in near ----


def test_near_excludes_stale_by_default_with_now(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh_fresh", "37.7749", "-122.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    run_cli(
        binary,
        db,
        ["update", "veh_stale", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    # now = 1000000 + 10000 (10s after fresh)
    # fresh age = 10s, not stale; stale age = 999000+10000 ~1009s >30s stale
    now = 1000000 + 10000
    proc = run_cli(
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
    arr = json.loads(proc.stdout.strip())
    ids = [x["vehicle_id"] for x in arr]
    assert "veh_fresh" in ids
    assert "veh_stale" not in ids


def test_near_include_stale_flag(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh_stale", "37.7749", "-122.4194", "1000", "--accuracy", "5"],
        expect_code=0,
    )
    now = 1000000
    proc = run_cli(
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
    arr = json.loads(proc.stdout.strip())
    assert len(arr) == 1
    assert arr[0]["vehicle_id"] == "veh_stale"


# ---- Road Snapping ----


def test_road_snapping(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Create simple roads file: one segment along 37.7749,-122.4194 to 37.7849,-122.4094
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_a",
            "start": {"lat": 37.7749, "lng": -122.4194},
            "end": {"lat": 37.7849, "lng": -122.4094},
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)

    # Vehicle location near road but offset by ~20m east
    # Point 37.7799, -122.4144 is near middle of road_a
    run_cli(
        binary,
        db,
        [
            "update",
            "veh1",
            "37.7799",
            "-122.4145",
            "1000000",
            "--accuracy",
            "5",
            "--speed",
            "0",
            "--heading",
            "0",
        ],
        expect_code=0,
    )
    proc = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000005", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(proc.stdout.strip())
    # Should be snapped because near road
    assert "snapped" in data
    assert "road_id" in data
    # If snapped, road_id should be road_a and distance to road before snap <50
    if data["snapped"]:
        assert data["road_id"] == "road_a"
        # snapped location should be close to road line, not exactly original
        # original_* should be preserved
        assert "original_lat" in data
    else:
        # If not snapped, maybe implementation uses stricter threshold, but should at least output correctly
        assert data["road_id"] == ""


def test_road_snapping_far_no_snap(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_a",
            "start": {"lat": 37.7749, "lng": -122.4194},
            "end": {"lat": 37.7849, "lng": -122.4094},
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)

    # Far away location
    run_cli(
        binary,
        db,
        ["update", "veh1", "38.7749", "-123.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    proc = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", roads_path],
        expect_code=0,
    )
    data = json.loads(proc.stdout.strip())
    assert data["snapped"] is False


def test_estimate_without_roads(binary):
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
    proc = run_cli(binary, db, ["estimate", "veh1", "--now", "1000000"], expect_code=0)
    data = json.loads(proc.stdout.strip())
    assert data["vehicle_id"] == "veh1"
    assert "confidence" in data
    assert "age_ms" in data
    assert data["age_ms"] == 0


# ---- Estimation with Prediction ----


def test_estimate_prediction_forward(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # vehicle moving east at 10 m/s, heading 90
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
    # now 10 seconds later
    now = 1000000 + 10000
    proc = run_cli(binary, db, ["estimate", "veh1", "--now", str(now)], expect_code=0)
    data = json.loads(proc.stdout.strip())
    # Predicted should be about 100m east
    assert data["predicted"] is True
    # East means lng increases (in SF) compared to raw stored location -122.4194
    assert data["lng"] > -122.4194
    # Distance moved should be approx speed*dt = 100m
    moved = haversine(37.7749, -122.4194, data["lat"], data["lng"])
    assert 80 < moved < 120, f"moved {moved} not approx 100m"
    # Age 10000
    assert data["age_ms"] == 10000


def test_estimate_confidence_high_medium_low(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # high confidence: accuracy 5, age 5s
    run_cli(
        binary,
        db,
        [
            "update",
            "veh_high",
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
    proc = run_cli(
        binary, db, ["estimate", "veh_high", "--now", "1005000"], expect_code=0
    )
    data = json.loads(proc.stdout.strip())
    assert data["confidence"] == "high"

    # medium: accuracy 20, age 15s
    run_cli(
        binary,
        db,
        ["update", "veh_med", "37.0", "-122.0", "1000000", "--accuracy", "20"],
        expect_code=0,
    )
    proc2 = run_cli(
        binary, db, ["estimate", "veh_med", "--now", "1015000"], expect_code=0
    )
    data2 = json.loads(proc2.stdout.strip())
    assert data2["confidence"] in (
        "medium",
        "high",
    )  # medium or upgraded to high if snapped but without roads should be medium

    # low: accuracy 40, age 40s >30? Actually age 40s >20000 => low
    run_cli(
        binary,
        db,
        ["update", "veh_low", "37.0", "-122.0", "1000000", "--accuracy", "40"],
        expect_code=0,
    )
    proc3 = run_cli(
        binary, db, ["estimate", "veh_low", "--now", "1040000"], expect_code=0
    )
    data3 = json.loads(proc3.stdout.strip())
    assert data3["confidence"] == "low"


# ---- Pickup Validation ----


def test_validate_pickup_valid(binary):
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
    # pickup very close 10m away
    proc = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7750", "-122.4195", "--now", "1005000"],
        expect_code=0,
    )
    data = json.loads(proc.stdout.strip())
    assert data["valid"] is True
    assert data["reason"] == "ok"
    assert data["distance_m"] < 100


def test_validate_pickup_too_far(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    # pickup 500m away
    proc = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7799", "-122.4194", "--now", "1000000"],
        expect_code=1,
    )
    data = json.loads(proc.stdout.strip())
    assert data["valid"] is False
    assert data["reason"] == "too_far"
    assert data["distance_m"] > 100


def test_validate_pickup_stale(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    # now 40s later => stale
    now = 1000000 + 40000
    proc = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", str(now)],
        expect_code=1,
    )
    data = json.loads(proc.stdout.strip())
    assert data["valid"] is False
    assert data["reason"] == "stale"


def test_validate_pickup_low_accuracy(binary):
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
            "40",
            "--speed",
            "0",
        ],
        expect_code=0,
    )
    # set another update with accuracy 60 would be rejected, so we need to use 40 then estimate accuracy degradation?
    # Actually validation checks estimated accuracy >50 => low_accuracy
    # If we store accuracy 40 and age 0, it's not low_accuracy (40<50) -> should be valid if close.
    # So need accuracy 60 but can't store via update (rejects >100 only, not >50). So 60 is storable but should fail pickup validation because 60>50
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "2000000", "--accuracy", "60"],
        expect_code=0,
    )
    proc = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7749", "-122.4194", "--now", "2000000"],
        expect_code=1,
    )
    data = json.loads(proc.stdout.strip())
    assert data["valid"] is False
    assert data["reason"] == "low_accuracy"


def test_validate_pickup_with_roads(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    roads_path = os.path.join(tmp, "roads.json")
    roads = [
        {
            "id": "road_a",
            "start": {"lat": 37.7749, "lng": -122.4194},
            "end": {"lat": 37.7849, "lng": -122.4094},
        }
    ]
    with open(roads_path, "w") as f:
        json.dump(roads, f)

    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7799", "-122.4145", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    proc = run_cli(
        binary,
        db,
        [
            "validate-pickup",
            "veh1",
            "37.7800",
            "-122.4144",
            "--now",
            "1000000",
            "--roads",
            roads_path,
        ],
        expect_code=0,
    )
    data = json.loads(proc.stdout.strip())
    assert data["valid"] is True
    assert "snapped" in data


def test_validate_dropoff_lenient_distance(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    # 120m away: pickup would fail (100 threshold), dropoff should succeed (150 threshold)
    proc_pickup = run_cli(
        binary,
        db,
        ["validate-pickup", "veh1", "37.7760", "-122.4194", "--now", "1000000"],
        expect_code=1,
    )
    data_pickup = json.loads(proc_pickup.stdout.strip())
    assert data_pickup["valid"] is False
    assert data_pickup["reason"] == "too_far"

    proc_drop = run_cli(
        binary,
        db,
        ["validate-dropoff", "veh1", "37.7760", "-122.4194", "--now", "1000000"],
        expect_code=0,
    )
    data_drop = json.loads(proc_drop.stdout.strip())
    assert data_drop["valid"] is True


def test_invalid_road_file(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["update", "veh1", "37.7749", "-122.4194", "1000000", "--accuracy", "5"],
        expect_code=0,
    )
    proc = run_cli(
        binary,
        db,
        ["estimate", "veh1", "--now", "1000000", "--roads", "/nonexistent.json"],
        expect_code=2,
    )


def test_backward_compat_old_db_format(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    # Simulate old Step1 DB format: map vehicle_id -> base location without history
    old_format = {
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
        json.dump(old_format, f)
    # Should still be readable and get should work
    proc = run_cli(binary, db, ["get", "veh_old"], expect_code=0)
    data = json.loads(proc.stdout.strip())
    assert data["vehicle_id"] == "veh_old"
    # estimate should also work auto-migrating
    proc2 = run_cli(
        binary, db, ["estimate", "veh_old", "--now", "1000000"], expect_code=0
    )
    data2 = json.loads(proc2.stdout.strip())
    assert data2["vehicle_id"] == "veh_old"


def test_validate_not_found(binary):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "db.json")
    run_cli(
        binary,
        db,
        ["validate-pickup", "nonexist", "0", "0", "--now", "1000"],
        expect_code=3,
    )
    run_cli(
        binary,
        db,
        ["validate-dropoff", "nonexist", "0", "0", "--now", "1000"],
        expect_code=3,
    )
    run_cli(binary, db, ["estimate", "nonexist"], expect_code=3)
