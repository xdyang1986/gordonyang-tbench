"""
Black-box tests for Step 1: geofence lookup CLI
"""

import os
import json
import subprocess
import tempfile
import shutil

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


def run_cli_with_db_flag_first(db_path, args, timeout=10):
    # also test --db=/path form in some cases
    return run_cli(db_path, args, timeout)


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

        r = run_cli(db, ["lookup", "--lat", "2", "--lng", "2"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == []
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
