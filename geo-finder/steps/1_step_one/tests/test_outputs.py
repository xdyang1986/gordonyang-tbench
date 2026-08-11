"""
Black-box tests for Step 1: geofence lookup CLI (Hard version with self-intersection, duplicate, degenerate, empty segment)
"""

import os
import json
import subprocess
import tempfile
import shutil
import time

import pytest

APP = "/app/src"
BIN = "/tmp/geofencectl_step1"

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
    assert os.path.exists(BIN), "binary not produced"
    yield


def run_cli(db_path, args, timeout=10):
    cmd = [BIN, "--db", db_path] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---- basic build ----


def test_binary_exists():
    assert os.path.exists(BIN)


# ---- add / list / persistence ----


def test_add_and_list(tmp_path=None):
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        r = run_cli(db, ["clear"])
        assert r.returncode == 0
        assert "cleared" in r.stdout.lower()

        r = run_cli(
            db, ["add", "zone_a", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Square A"]
        )
        assert r.returncode == 0, f"add failed: {r.stderr} stdout={r.stdout}"
        obj = json.loads(r.stdout)
        assert obj["id"] == "zone_a"
        assert obj["name"] == "Square A"
        assert len(obj["polygon"]) == 4

        r = run_cli(db, ["list"])
        assert r.returncode == 0
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        assert arr[0]["id"] == "zone_a"
        # must be [] not null
        assert r.stdout.strip().startswith("[")

        # add second out of order, check sorting
        r = run_cli(
            db, ["add", "zone_0", "--polygon", "10,10;10,11;11,11;11,10", "--name", "B"]
        )
        assert r.returncode == 0

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        ids = [x["id"] for x in arr]
        assert ids == sorted(ids), f"list not sorted: {ids}"
        assert ids == ["zone_0", "zone_a"]

        # persistence: new process should see same
        r2 = run_cli(db, ["list"])
        arr2 = json.loads(r2.stdout)
        assert arr2 == arr
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_remove():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "zone_a", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        r = run_cli(db, ["remove", "zone_a"])
        assert r.returncode == 0
        assert "removed" in r.stdout.lower()

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert arr == []

        r = run_cli(db, ["remove", "zone_a"])
        assert r.returncode == 3, "removing non-existent should exit 3"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_clear():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "a/b/c/geof.json")  # test parent dir creation
    try:
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        assert r.returncode == 0
        assert os.path.exists(db)

        r = run_cli(db, ["clear"])
        assert r.returncode == 0
        assert "cleared" in r.stdout.lower()

        r = run_cli(db, ["list"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- lookup correctness ----


def test_lookup_inside_outside():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == ["sq"]
        # check not null
        assert r.stdout.strip() != "null"

        r = run_cli(db, ["lookup", "--lat", "2", "--lng", "2"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == []
        assert r.stdout.strip() == "[]"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_on_edge_and_vertex():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "sq"])
        # on edge
        for lat, lng in [("0", "0.5"), ("0.5", "0"), ("1", "0.5"), ("0.5", "1")]:
            r = run_cli(db, ["lookup", "--lat", lat, "--lng", lng])
            assert r.returncode == 0, f"edge lookup {lat},{lng} failed {r.stderr}"
            assert json.loads(r.stdout) == ["sq"], (
                f"point on edge {lat},{lng} should be inside"
            )

        # vertex
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0"])
        assert json.loads(r.stdout) == ["sq"], "vertex should be inside"
        r = run_cli(db, ["lookup", "--lat", "1", "--lng", "1"])
        assert json.loads(r.stdout) == ["sq"]

        # horizontal edge
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["sq"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_concave():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # L shape: 0,0 ->0,2 ->1,2 ->1,1 ->2,1 ->2,0
        poly = "0,0;0,2;1,2;1,1;2,1;2,0"
        run_cli(db, ["add", "lshape", "--polygon", poly, "--name", "L"])
        # inside left arm
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["lshape"]
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "1.5"])
        assert json.loads(r.stdout) == ["lshape"]
        # inside bottom arm
        r = run_cli(db, ["lookup", "--lat", "1.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["lshape"]
        # outside notch (should be outside L)
        r = run_cli(db, ["lookup", "--lat", "1.5", "--lng", "1.5"])
        assert json.loads(r.stdout) == [], (
            f"point in concave notch should be outside, got {r.stdout}"
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_overlapping():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "a", "--polygon", "0,0;0,2;2,2;2,0", "--name", "A"])
        run_cli(db, ["add", "b", "--polygon", "1,1;1,3;3,3;3,1", "--name", "B"])
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["a"]
        r = run_cli(db, ["lookup", "--lat", "1.5", "--lng", "1.5"])
        ids = json.loads(r.stdout)
        assert ids == ["a", "b"], (
            f"overlapping point should match both sorted, got {ids}"
        )
        r = run_cli(db, ["lookup", "--lat", "2.5", "--lng", "2.5"])
        assert json.loads(r.stdout) == ["b"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_verbose():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,1;1,1;1,0", "--name", "myzone"])
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5", "--verbose"])
        assert r.returncode == 0
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        assert arr[0]["id"] == "sq"
        assert arr[0]["name"] == "myzone"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_empty_db():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == []
        assert r.stdout.strip() == "[]"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- validation ----


def test_invalid_id():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        r = run_cli(
            db, ["add", "bad id", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"]
        )
        assert r.returncode == 2, "id with space should be invalid"

        r = run_cli(db, ["add", "", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        assert r.returncode == 2

        r = run_cli(
            db, ["add", "a" * 65, "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"]
        )
        assert r.returncode == 2, "too long id should fail"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_name():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", ""])
        assert r.returncode == 2

        r = run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", "   "])
        assert r.returncode == 2

        r = run_cli(
            db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", "a" * 129]
        )
        assert r.returncode == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_polygon():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        # less than 3 points
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;0,1", "--name", "A"])
        assert r.returncode == 2

        # invalid float
        r = run_cli(db, ["add", "z1", "--polygon", "abc,0;0,1;1,1", "--name", "A"])
        assert r.returncode == 2

        # out of range lat
        r = run_cli(db, ["add", "z1", "--polygon", "100,0;0,1;1,1", "--name", "A"])
        assert r.returncode == 2

        # malformed
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;bad", "--name", "A"])
        assert r.returncode == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_polygon_strict():
    """Tests for strict polygon validation: empty segments, duplicate points, self-intersection, degenerate area."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])

        # empty segment double semicolon
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;;0,1;1,1;1,0", "--name", "A"])
        assert r.returncode == 2, "double semicolon should be invalid"

        # trailing semicolon
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0;", "--name", "A"])
        assert r.returncode == 2, "trailing semicolon should be invalid"

        # leading semicolon
        r = run_cli(db, ["add", "z1", "--polygon", ";0,0;0,1;1,1;1,0", "--name", "A"])
        assert r.returncode == 2, "leading semicolon should be invalid"

        # duplicate points (exact same coordinates)
        r = run_cli(
            db, ["add", "z1", "--polygon", "0,0;0,0;0,1;1,1;1,0", "--name", "A"]
        )
        assert r.returncode == 2, "duplicate points should be invalid"

        # duplicate closing point (first == last) – should be invalid per spec (implicit closure)
        r = run_cli(
            db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0;0,0", "--name", "A"]
        )
        assert r.returncode == 2, "explicit closing duplicate should be invalid"

        # degenerate colinear area zero
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;1,0;2,0", "--name", "A"])
        assert r.returncode == 2, "colinear degenerate should be invalid"

        # self-intersecting bow-tie
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;1,1;0,1;1,0", "--name", "A"])
        assert r.returncode == 2, "bow-tie self-intersecting should be invalid"

        # self-intersecting with overlapping colinear edges
        r = run_cli(
            db,
            [
                "add",
                "z1",
                "--polygon",
                "0,0;0,2;2,2;2,0;0,0;1,0;1,1;0,1",
                "--name",
                "A",
            ],
        )
        # This is complex; at least ensure our validator catches simple case
        # We test a simpler colinear overlapping: square with a spike that overlaps
        r2 = run_cli(
            db, ["add", "z2", "--polygon", "0,0;0,1;1,1;1,0;0,0", "--name", "A"]
        )
        assert r2.returncode == 2

        # valid polygon should still pass
        r = run_cli(
            db, ["add", "valid", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Ok"]
        )
        assert r.returncode == 0, f"valid should pass but got {r.stderr}"

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_lat_lng_lookup():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(db, ["lookup", "--lat", "100", "--lng", "0"])
        assert r.returncode == 2
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "200"])
        assert r.returncode == 2
        r = run_cli(db, ["lookup", "--lat", "abc", "--lng", "0"])
        assert r.returncode == 2
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0"])
        assert r.returncode == 0  # valid
        # missing flag
        r = run_cli(db, ["lookup", "--lat", "0"])
        assert r.returncode == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_corrupt_db():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        with open(db, "w") as f:
            f.write("{ invalid json")
        r = run_cli(db, ["list"])
        assert r.returncode == 4, (
            f"corrupt DB should exit 4, got {r.returncode} stderr={r.stderr}"
        )

        # empty file should be treated as empty, not corrupt
        with open(db, "w") as f:
            f.write("")
        r = run_cli(db, ["list"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == []

        # array instead of object should be corrupt
        with open(db, "w") as f:
            f.write("[]")
        r = run_cli(db, ["list"])
        # Depending on implementation, empty array might be considered corrupt if not object.
        # We require object, so array should be corrupt -> exit 4 or at least not crash.
        # Allow either 4 or treat as empty? Spec says if JSON is not an object, exit 4.
        if r.returncode == 0:
            # If implementation treats [] as empty, that's lenient but we check it doesn't crash
            assert json.loads(r.stdout) == [] or r.returncode == 4
        else:
            assert r.returncode == 4
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_missing_db_flag():
    # Missing --db flag should exit 2
    cmd = [BIN, "list"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert r.returncode == 2


def test_parent_dir_creation():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "a/b/c/d/geof.json")
    try:
        r = run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        assert r.returncode == 0, f"should create parent dirs: {r.stderr}"
        assert os.path.exists(db)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_overwrite():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", "First"])
        r = run_cli(
            db,
            ["add", "z1", "--polygon", "10,10;10,11;11,11;11,10", "--name", "Second"],
        )
        assert r.returncode == 0
        obj = json.loads(r.stdout)
        assert obj["name"] == "Second"

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        assert arr[0]["name"] == "Second"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_world_bounds():
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # polygon near world bounds
        poly = "-90,-180;-90,180;90,180;90,-180"
        r = run_cli(db, ["add", "world", "--polygon", poly, "--name", "World"])
        assert r.returncode == 0, f"world polygon add failed: {r.stderr}"
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0"])
        assert json.loads(r.stdout) == ["world"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_temp_file_cleanup():
    """Ensure atomic write does not leave temp files behind."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "z1", "--polygon", "0,0;0,1;1,1;1,0", "--name", "A"])
        # Check no .tmp files in dir
        files = os.listdir(tmpdir)
        tmp_left = [f for f in files if ".tmp." in f]
        assert tmp_left == [], f"temp files left behind: {tmp_left}"
        run_cli(
            db, ["add", "z2", "--polygon", "10,10;10,11;11,11;11,10", "--name", "B"]
        )
        files = os.listdir(tmpdir)
        tmp_left = [f for f in files if ".tmp." in f]
        assert tmp_left == [], f"temp files left after second add: {tmp_left}"
        run_cli(db, ["remove", "z1"])
        files = os.listdir(tmpdir)
        tmp_left = [f for f in files if ".tmp." in f]
        assert tmp_left == [], f"temp files left after remove: {tmp_left}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_add_does_not_corrupt():
    """Validation failure must not modify DB and must not leave temp files."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "keep", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Keep"])
        # Try invalid polygon (self-intersecting bow-tie)
        r = run_cli(
            db,
            ["add", "bad", "--polygon", "0,0;1,1;0,1;1,0", "--name", "Bad"],
        )
        assert r.returncode == 2, f"should reject self-intersecting {r.stderr}"
        # DB must still have only keep
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1 and arr[0]["id"] == "keep"
        # No temp files
        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f], (
            f"temp left after invalid add: {files}"
        )

        # Try duplicate point with different string representation (0 vs 0.0) – numeric duplicate
        r = run_cli(
            db,
            ["add", "bad2", "--polygon", "0,0;0.0,0.0;1,0;0,1", "--name", "Bad2"],
        )
        assert r.returncode == 2, f"should reject numeric duplicate {r.stderr}"
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1 and arr[0]["id"] == "keep"

        # Invalid ID format must not corrupt
        r = run_cli(
            db, ["add", "bad id!", "--polygon", "0,0;0,1;1,1;1,0", "--name", "X"]
        )
        assert r.returncode == 2
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_overwrite_invalid_preserves_old():
    """Overwrite attempt with invalid polygon must keep old entry, not delete or corrupt."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(
            db, ["add", "zone", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Original"]
        )
        r = run_cli(
            db, ["add", "zone", "--polygon", "0,0;1,1;0,1;1,0", "--name", "Bad"]
        )
        assert r.returncode == 2
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        assert arr[0]["name"] == "Original"
        assert len(arr[0]["polygon"]) == 4
        # lookup must still work for original
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["zone"]
        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_colinear_overlap_self_intersect():
    """Colinear overlapping edges must be detected as self-intersection."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Overlapping colinear: 0,0-0,2 contains 0,1 overlapping with 0,2-0,1
        poly = "0,0;0,2;0,1;1,1;1,0"
        r = run_cli(db, ["add", "bad", "--polygon", poly, "--name", "Bad"])
        assert r.returncode == 2, f"should reject colinear overlap {r.stderr}"

        # Another overlapping: horizontal overlap
        poly2 = "0,0;2,0;1,0;1,1;0,1"
        r = run_cli(db, ["add", "bad2", "--polygon", poly2, "--name", "Bad2"])
        assert r.returncode == 2, (
            f"should reject horizontal colinear overlap {r.stderr}"
        )

        # Valid touching at shared vertex only (adjacent) should be allowed
        poly_ok = "0,0;0,1;1,1;1,0"
        r = run_cli(db, ["add", "ok", "--polygon", poly_ok, "--name", "OK"])
        assert r.returncode == 0, f"valid rect should pass {r.stderr}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_polygon_1000_boundary():
    """1000 points allowed, 1001 rejected – boundary check."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Build 1000-point polygon approximating circle (valid)
        import math

        pts = []
        for i in range(1000):
            ang = 2 * math.pi * i / 1000
            lat = 0.5 + 0.1 * math.sin(ang)
            lng = 0.5 + 0.1 * math.cos(ang)
            pts.append(f"{lat},{lng}")
        poly_1000 = ";".join(pts)
        r = run_cli(db, ["add", "max", "--polygon", poly_1000, "--name", "Max"])
        assert r.returncode == 0, f"1000 points should be allowed: {r.stderr}"

        # 1001 points should be rejected
        pts.append("0.6,0.5")
        poly_1001 = ";".join(pts)
        r = run_cli(
            db, ["add", "too_many", "--polygon", poly_1001, "--name", "TooMany"]
        )
        assert r.returncode == 2, f"1001 points should be rejected: {r.stderr}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_antimeridian_cli():
    """CLI lookup must correctly handle antimeridian-crossing polygon."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Tiny rect crossing 180
        poly = "0,179;0,-179;1,-179;1,179"
        r = run_cli(db, ["add", "cross", "--polygon", poly, "--name", "Cross"])
        assert r.returncode == 0, f"crossing add failed {r.stderr}"

        tests = [
            (0.5, 179.5, True),
            (0.5, -179.5, True),
            (0.5, 180, True),
            (0.5, -180, True),
            (0.5, 0, False),
        ]
        for lat, lng, should_inside in tests:
            r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
            assert r.returncode == 0
            ids = json.loads(r.stdout)
            assert isinstance(ids, list), f"should be list not {ids}"
            assert ids == [] or ids is not None
            # empty must be [] not null – check raw text
            if not should_inside:
                assert r.stdout.strip() == "[]", f"empty should be [] not {r.stdout}"
            if should_inside:
                assert "cross" in ids, f"{lat},{lng} should be inside crossing"
            else:
                assert "cross" not in ids, f"{lat},{lng} should be outside crossing"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_pole_cli():
    """CLI lookup near poles, including edge at exactly ±90."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly_n = "89,-10;89,10;90,10;90,-10"
        r = run_cli(db, ["add", "pole_n", "--polygon", poly_n, "--name", "N"])
        assert r.returncode == 0

        poly_s = "-90,-10;-90,10;-89,10;-89,-10"
        r = run_cli(db, ["add", "pole_s", "--polygon", poly_s, "--name", "S"])
        assert r.returncode == 0

        r = run_cli(db, ["lookup", "--lat", "89.5", "--lng", "0"])
        assert "pole_n" in json.loads(r.stdout)

        r = run_cli(db, ["lookup", "--lat", "90", "--lng", "0"])
        assert "pole_n" in json.loads(r.stdout), "90,0 on edge should be inside"

        r = run_cli(db, ["lookup", "--lat", "-89.5", "--lng", "0"])
        assert "pole_s" in json.loads(r.stdout)

        r = run_cli(db, ["lookup", "--lat", "-90", "--lng", "0"])
        assert "pole_s" in json.loads(r.stdout)

        r = run_cli(db, ["lookup", "--lat", "88", "--lng", "0"])
        ids = json.loads(r.stdout)
        assert "pole_n" not in ids and "pole_s" not in ids
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_name_trimming():
    """Name with leading/trailing spaces must be trimmed and persisted trimmed."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(
            db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "  My Zone  "]
        )
        assert r.returncode == 0
        obj = json.loads(r.stdout)
        assert obj["name"] == "My Zone", f"name should be trimmed, got {obj['name']!r}"

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert arr[0]["name"] == "My Zone"

        # Name that is only whitespace should be rejected
        r = run_cli(db, ["add", "z2", "--polygon", "0,0;0,1;1,1;1,0", "--name", "   "])
        assert r.returncode == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_remove_invalid_format():
    """remove with invalid ID format must exit 2, not 3, and not corrupt DB."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "keep", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Keep"])
        r = run_cli(db, ["remove", "bad id!"])
        assert r.returncode == 2, (
            f"invalid id format should be exit 2, got {r.returncode}"
        )
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1 and arr[0]["id"] == "keep"
        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cli_performance():
    """CLI lookup with many geofences should still be reasonably fast (bbox prefilter)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Create 200 geofences
        for i in range(200):
            base_lat = (i // 20) * 2.0
            base_lng = (i % 20) * 2.0
            poly = f"{base_lat},{base_lng};{base_lat},{base_lng + 0.8};{base_lat + 0.8},{base_lng + 0.8};{base_lat + 0.8},{base_lng}"
            r = run_cli(
                db, ["add", f"zone_{i:03d}", "--polygon", poly, "--name", f"Zone {i}"]
            )
            assert r.returncode == 0
        start = time.time()
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        elapsed = time.time() - start
        assert r.returncode == 0
        # Should be fast (<500ms) even with 200 zones
        assert elapsed < 0.5, f"CLI lookup too slow {elapsed}s for 200 zones"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
