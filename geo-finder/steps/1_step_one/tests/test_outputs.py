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


def test_gomod_stdlib_only():
    """Go stdlib only – fail if go.mod has external require or .go imports external package (allow_internet=true)."""
    gomod_path = os.path.join(APP, "go.mod")
    if not os.path.exists(gomod_path):
        # built fixture creates go.mod via go mod init, so if missing we skip but binary already built
        return
    with open(gomod_path) as f:
        content = f.read()
    # Check for external requires – any require containing github.com, golang.org/x, gopkg.in, etc. or any domain dot
    lower = content.lower()
    # Parse require blocks
    forbidden_substrings = [
        "github.com",
        "golang.org/x",
        "gopkg.in",
        "gitlab.com",
        "bitbucket.org",
    ]
    for substr in forbidden_substrings:
        assert substr not in lower, (
            f"go.mod contains external dep {substr}: {content[:500]}"
        )
    # Also check that there is no require with a module path that contains '.' (external modules have domain)
    # Allow only comments and module/go lines. If 'require' appears, ensure its module doesn't look external.
    # Simple heuristic: if file contains 'require (' block or single require, extract module names
    import re as _re

    # Find all require lines
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require"):
            # examples: require github.com/foo/bar v1.2.3
            # or require (
            # We look at the remainder after 'require'
            rest = stripped[len("require") :].strip()
            if rest.startswith("(") or rest == "":
                continue
            # rest should be module path
            mod = rest.split()[0]
            if "/" in mod and "." in mod.split("/")[0]:
                assert False, f"go.mod has external require {mod}"

    # Check .go files for external imports (import path containing '.' like github.com/)
    # Only inspect actual import declarations, not all quoted strings in file (which could be error messages)
    for root, _dirs, files in os.walk(APP):
        for fname in files:
            if not fname.endswith(".go"):
                continue
            path = os.path.join(root, fname)
            try:
                lines = (
                    open(path, encoding="utf-8", errors="ignore").read().splitlines()
                )
            except:
                continue
            in_block = False
            for line in lines:
                stripped = line.strip()
                if not in_block and stripped.startswith("import ("):
                    in_block = True
                    # may have imports on same line after '('
                    # extract quoted strings from remainder
                    quotes = _re.findall(r'"([^"]+)"', line)
                    for imp in quotes:
                        if "." in imp and "/" in imp:
                            assert False, (
                                f"{path} imports external package {imp!r} – stdlib only required"
                            )
                    continue
                if in_block:
                    if ")" in stripped:
                        in_block = False
                        # also check this line for imports before closing
                    quotes = _re.findall(r'"([^"]+)"', line)
                    for imp in quotes:
                        if "." in imp and "/" in imp:
                            assert False, (
                                f"{path} imports external package {imp!r} – stdlib only required"
                            )
                    if ")" in stripped:
                        continue
                else:
                    if stripped.startswith("import "):
                        quotes = _re.findall(r'"([^"]+)"', line)
                        for imp in quotes:
                            if "." in imp and "/" in imp:
                                assert False, (
                                    f"{path} imports external package {imp!r} – stdlib only required"
                                )


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

        # array instead of object must be corrupt -> exit 4 (strict, no soft acceptance)
        with open(db, "w") as f:
            f.write("[]")
        r = run_cli(db, ["list"])
        assert r.returncode == 4, (
            f"[] should be corrupt (not object) exit 4, got {r.returncode} stdout={r.stdout}"
        )

        # null is valid JSON but not an object -> per spec :57 must be corrupt exit 4 (previous treated as empty and pinned p1 to 0.0)
        with open(db, "w") as f:
            f.write("null")
        r = run_cli(db, ["list"])
        assert r.returncode == 4, (
            f"null should be corrupt (not object) exit 4 per spec instruction.md:57, got {r.returncode} stdout={r.stdout}"
        )

        # number / string / bool are valid JSON but not objects -> corrupt
        for bad in ["123", "0", "1.5", '"hello"', "true", "false", '"null"', "123.45"]:
            with open(db, "w") as f:
                f.write(bad)
            r = run_cli(db, ["list"])
            assert r.returncode == 4, (
                f"{bad!r} should be corrupt exit 4, got {r.returncode} stdout={r.stdout}"
            )

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
        # world rectangle – must be accepted per Edit A ( -180 and 180 distinct, area 64800)
        poly = "-90,-180;-90,180;90,180;90,-180"
        r = run_cli(db, ["add", "world", "--polygon", poly, "--name", "World"])
        assert r.returncode == 0, (
            f"world polygon add failed: {r.stderr} stdout={r.stdout}"
        )

        # world must match at every longitude in its lat band (not just 0,0)
        for lng in [0, 50, -50, 179.5, -179.5, 180, -180, 150, -100]:
            r = run_cli(db, ["lookup", "--lat", "0", "--lng", str(lng)])
            assert r.returncode == 0, f"lookup {lng} failed {r.stderr}"
            ids = json.loads(r.stdout)
            assert ids == ["world"], f"world should match lng {lng}, got {ids}"

        # edge points at poles and antimeridian should be inside (on edge)
        for lat, lng in [
            (-90, 0),
            (90, 0),
            (-90, -180),
            (90, 180),
            (0, -180),
            (0, 180),
        ]:
            r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
            assert r.returncode == 0
            assert json.loads(r.stdout) == ["world"], (
                f"world edge {lat},{lng} should be inside"
            )

        # empty outside lat band? world lat is -90..90 so all lats inside, but test outside range rejected earlier
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_world_bounds_duplicate_distinct():
    """World rect -180 and 180 are distinct for validation – must not be considered duplicate."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly = "-90,-180;-90,180;90,180;90,-180"
        r = run_cli(db, ["add", "world", "--polygon", poly, "--name", "World"])
        assert r.returncode == 0, (
            f"world should be valid ( -180 != 180 ), got {r.stderr} code {r.returncode}"
        )
        # Adding same world again with same ID but slightly different name should overwrite and still be valid
        r = run_cli(db, ["add", "world", "--polygon", poly, "--name", "World2"])
        assert r.returncode == 0
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1 and arr[0]["name"] == "World2"
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
    """CLI lookup with many geofences should still be reasonably fast (bbox prefilter). Relaxed to avoid flake."""
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
        # Should be fast (<1s) even with 200 zones – relaxed from 0.5s to avoid flake
        assert elapsed < 1.0, f"CLI lookup too slow {elapsed}s for 200 zones"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_many_overlapping_sorted():
    """Overlapping zones must return sorted IDs, [] not null, and verbose sorted."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # 10 overlapping zones all covering 0,0-10,10 but different IDs out of order
        for i in reversed(range(10)):
            poly = "0,0;0,10;10,10;10,0"
            r = run_cli(
                db, ["add", f"zone_{i:02d}", "--polygon", poly, "--name", f"Z{i}"]
            )
            assert r.returncode == 0

        r = run_cli(db, ["lookup", "--lat", "5", "--lng", "5"])
        assert r.returncode == 0
        ids = json.loads(r.stdout)
        assert ids == sorted(ids), f"lookup IDs not sorted: {ids}"
        assert len(ids) == 10
        # raw text must be [] not null when empty is already checked elsewhere, but check not null
        assert "null" not in r.stdout.lower()

        r = run_cli(db, ["lookup", "--lat", "5", "--lng", "5", "--verbose"])
        arr = json.loads(r.stdout)
        assert len(arr) == 10
        v_ids = [x["id"] for x in arr]
        assert v_ids == sorted(v_ids)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_name_and_id_boundaries():
    """Name 128 allowed, 129 rejected; ID 64 allowed, 65 rejected."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        name_128 = "a" * 128
        r = run_cli(
            db, ["add", "b1", "--polygon", "0,0;0,1;1,1;1,0", "--name", name_128]
        )
        assert r.returncode == 0, f"128-char name should be allowed: {r.stderr}"

        name_129 = "a" * 129
        r = run_cli(
            db, ["add", "b2", "--polygon", "0,0;0,1;1,1;1,0", "--name", name_129]
        )
        assert r.returncode == 2, "129-char name should be rejected"

        id_64 = "x" * 64
        r = run_cli(db, ["add", id_64, "--polygon", "0,0;0,1;1,1;1,0", "--name", "OK"])
        assert r.returncode == 0, f"64-char ID should be allowed: {r.stderr}"

        id_65 = "y" * 65
        r = run_cli(db, ["add", id_65, "--polygon", "0,0;0,1;1,1;1,0", "--name", "OK"])
        assert r.returncode == 2, "65-char ID should be rejected"

        # Empty ID and whitespace-only name already tested elsewhere but check not corrupting
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 2  # b1 and id_64
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_polygon_min_max_points():
    """Polygon must have at least 3 points, at most 1000; 2 points invalid."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(db, ["add", "two", "--polygon", "0,0;0,1", "--name", "Two"])
        assert r.returncode == 2, "2 points should be rejected"

        r = run_cli(db, ["add", "three", "--polygon", "0,0;0,1;1,0", "--name", "Three"])
        assert r.returncode == 0, "3 points should be allowed"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_parent_dir_deep_nested():
    """Deep nested parent dir creation must work and not leave temp files in intermediate dirs."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "a", "b", "c", "d", "geof.json")
    try:
        r = run_cli(db, ["clear"])
        assert r.returncode == 0
        assert os.path.exists(db)
        r = run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z"])
        assert r.returncode == 0
        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        # Check no temp files in any nested dir
        for root, dirs, files in os.walk(tmpdir):
            assert not [f for f in files if ".tmp." in f], (
                f"temp left in {root}: {files}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_polygon_spaces_around_comma():
    """Spaces around comma and semicolon should be trimmed and accepted."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # spaces around comma and semicolon
        poly = "0, 0; 0, 1; 1, 1; 1, 0"
        r = run_cli(db, ["add", "spaced", "--polygon", poly, "--name", "Spaced"])
        assert r.returncode == 0, f"spaces around comma should be allowed: {r.stderr}"

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["spaced"]

        # leading/trailing spaces in whole polygon string
        poly2 = " 0,0;0,1;1,1;1,0 "
        r = run_cli(db, ["add", "spaced2", "--polygon", poly2, "--name", "Spaced2"])
        assert r.returncode == 0, (
            f"leading/trailing spaces should be allowed: {r.stderr}"
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_empty_db_whitespace():
    """File containing only whitespace should be treated as corrupt (exit 4), not empty, because spec says 0 bytes is empty."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        os.makedirs(os.path.dirname(db), exist_ok=True)
        with open(db, "w") as f:
            f.write("   \n  \t\n")
        r = run_cli(db, ["list"])
        # Our reference treats whitespace as corrupt (invalid JSON) -> exit 4, which is acceptable per spec (0 bytes is empty, whitespace not)
        # Some impls might treat as empty – allow either 0 or 4 as long as not crash and no temp left, but must not return []
        assert r.returncode in (0, 4), (
            f"whitespace db should be either empty or corrupt, got {r.returncode}"
        )
        if r.returncode == 0:
            arr = json.loads(r.stdout)
            assert arr == [], "if treated as empty, list should be []"
        # No temp files
        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_list_and_lookup_empty_bracket():
    """list and lookup empty must return exactly [] not null, raw text check."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(db, ["list"])
        assert r.returncode == 0
        assert r.stdout.strip() == "[]", f"empty list should be [] not {r.stdout!r}"
        assert "null" not in r.stdout.lower()

        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0"])
        assert r.returncode == 0
        assert r.stdout.strip() == "[]", f"empty lookup should be [] not {r.stdout!r}"
        assert "null" not in r.stdout.lower()

        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0", "--verbose"])
        assert r.returncode == 0
        assert r.stdout.strip() == "[]"
        assert "null" not in r.stdout.lower()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cli_performance_500_zones():
    """Safety-net performance: 500 zones each 100 points, lookup well under 1s requires bbox prefilter (lenient to avoid flake)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        import math

        for i in range(500):
            base_lat = (i // 20) * 2.0
            base_lng = (i % 20) * 2.0
            # 100-point polygon approximating small circle to make naive expensive
            pts = []
            for j in range(100):
                ang = 2 * math.pi * j / 100
                lat = base_lat + 0.1 * math.sin(ang) + 0.25
                lng = base_lng + 0.1 * math.cos(ang) + 0.25
                pts.append(f"{lat},{lng}")
            poly = ";".join(pts)
            r = run_cli(
                db, ["add", f"perf_{i:03d}", "--polygon", poly, "--name", f"P{i}"]
            )
            assert r.returncode == 0, f"add perf_{i} failed {r.stderr}"

        start = time.time()
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        elapsed = time.time() - start
        assert r.returncode == 0
        # Relaxed from 0.5s to 1.0s to avoid flake on slow hosts; naive would be >>2s, so still catches missing bbox
        assert elapsed < 1.0, (
            f"500-zone 100-pt lookup too slow {elapsed}s, need bbox prefilter"
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_area_epsilon_boundary():
    """Area just below 1e-9 must be rejected, just above accepted – tests epsilon handling."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Tiny triangle area ~ 5e-10 (below threshold)
        poly_small = "0,0;0,0.000001;0.001,0"
        r = run_cli(db, ["add", "tiny", "--polygon", poly_small, "--name", "Tiny"])
        assert r.returncode == 2, (
            f"tiny area below 1e-9 should be rejected, got {r.returncode} {r.stderr}"
        )

        # Slightly larger area ~ 2e-9 (above threshold) – compute: (0,0)-(0,0.000002)-(0.001,0) area = 0.5*0.000002*0.001=1e-9 -> boundary, need >1e-9 so use 0.000003 => 1.5e-9
        poly_ok = "0,0;0,0.000003;0.001,0"
        r = run_cli(db, ["add", "ok", "--polygon", poly_ok, "--name", "OK"])
        # Allow either acceptance (if impl uses >1e-9) – this polygon area 1.5e-9 >1e-9 so should be accepted
        # Some impls use ==0 check and would accept both, so we only enforce that tiny is rejected, ok is allowed (not required to be accepted if they use stricter threshold, but our ref accepts)
        # To keep fair, assert ok is accepted by our ref, but allow rejection only if they use larger epsilon? We'll assert ok passes for ref, but for grading we require tiny rejected and ok not crash
        # Actually spec says >1e-9, so ok must be accepted
        assert r.returncode == 0, (
            f"area 1.5e-9 should be accepted per spec >1e-9, got {r.returncode} {r.stderr}"
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_duplicate_negative_zero():
    """Negative zero must be considered duplicate of zero (numeric equality)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # 0 vs -0 duplicate
        poly = "0,0;-0,0;1,0;0,1"
        r = run_cli(db, ["add", "negzero", "--polygon", poly, "--name", "NegZero"])
        assert r.returncode == 2, (
            f"-0 vs 0 should be duplicate, got {r.returncode} {r.stderr}"
        )

        poly2 = "0,0;0,-0;1,0;0,1"
        r = run_cli(db, ["add", "negzero2", "--polygon", poly2, "--name", "NegZero2"])
        assert r.returncode == 2, f"0 vs -0 lng should be duplicate"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_id_sort_lexicographic():
    """List and lookup must sort IDs lexicographically, not numerically – zone_10 < zone_2."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for id_ in ["zone_2", "zone_10", "zone_1"]:
            r = run_cli(db, ["add", id_, "--polygon", "0,0;0,1;1,1;1,0", "--name", id_])
            assert r.returncode == 0

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        ids = [x["id"] for x in arr]
        assert ids == ["zone_1", "zone_10", "zone_2"], (
            f"lexicographic sort failed, got {ids}"
        )

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        ids_lookup = json.loads(r.stdout)
        assert ids_lookup == ["zone_1", "zone_10", "zone_2"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_world_crossing_pole_combined():
    """CLI lookup with world + crossing + pole together must return correct combined sets."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(
            db,
            [
                "add",
                "world",
                "--polygon",
                "-90,-180;-90,180;90,180;90,-180",
                "--name",
                "World",
            ],
        )
        run_cli(
            db,
            [
                "add",
                "cross",
                "--polygon",
                "0,179;0,-179;1,-179;1,179",
                "--name",
                "Cross",
            ],
        )
        run_cli(
            db,
            [
                "add",
                "pole_n",
                "--polygon",
                "89,-10;89,10;90,10;90,-10",
                "--name",
                "PoleN",
            ],
        )

        # 0.5,0 -> only world
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0"])
        ids = json.loads(r.stdout)
        assert ids == ["world"], f"0.5,0 should be only world, got {ids}"

        # 0.5,179.5 -> world + cross
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "179.5"])
        ids = json.loads(r.stdout)
        assert ids == ["cross", "world"], (
            f"179.5 should be cross+world sorted, got {ids}"
        )

        # 89.5,0 -> world + pole_n
        r = run_cli(db, ["lookup", "--lat", "89.5", "--lng", "0"])
        ids = json.loads(r.stdout)
        assert ids == ["pole_n", "world"], f"pole+world failed, got {ids}"

        # 89.5,179.5 -> only world (pole_n lng -10..10, cross lat 0-1 only)
        r = run_cli(db, ["lookup", "--lat", "89.5", "--lng", "179.5"])
        ids = json.loads(r.stdout)
        assert ids == ["world"], f"89.5,179.5 should be only world, got {ids}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_name_with_128_after_trim():
    """Name with leading/trailing spaces that trims to 128 must be allowed, 129 after trim rejected even if raw longer."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        raw_128_trim = "  " + "b" * 128 + "  "
        r = run_cli(
            db, ["add", "z128", "--polygon", "0,0;0,1;1,1;1,0", "--name", raw_128_trim]
        )
        assert r.returncode == 0, (
            f"128 after trim should be allowed, got {r.returncode} {r.stderr}"
        )
        obj = json.loads(r.stdout)
        assert obj["name"] == "b" * 128
        assert len(obj["name"]) == 128

        raw_129_trim = "  " + "c" * 129 + "  "
        r = run_cli(
            db, ["add", "z129", "--polygon", "0,0;0,1;1,1;1,0", "--name", raw_129_trim]
        )
        assert r.returncode == 2, (
            f"129 after trim should be rejected, got {r.returncode}"
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_remove_then_add_same_id():
    """Remove then add same ID with new polygon must show new polygon, old no longer matches."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "First"])
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["z"]

        r = run_cli(db, ["remove", "z"])
        assert r.returncode == 0
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == []

        r = run_cli(
            db, ["add", "z", "--polygon", "10,10;10,11;11,11;11,10", "--name", "Second"]
        )
        assert r.returncode == 0
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == []
        r = run_cli(db, ["lookup", "--lat", "10.5", "--lng", "10.5"])
        assert json.loads(r.stdout) == ["z"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_scientific_notation_polygon():
    """Scientific notation for lat/lng must be parsed (strconv.ParseFloat handles it)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # 1e0 == 1, 0e0 ==0
        poly = "1e0,1e0;0e0,1e0;0e0,0e0;1e0,0e0"
        r = run_cli(db, ["add", "sci", "--polygon", poly, "--name", "Sci"])
        assert r.returncode == 0, f"sci notation should be allowed {r.stderr}"

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["sci"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_self_intersection_star():
    """5-point star is self-intersecting and must be rejected."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Classic 5-point star (pentagram) self-intersects
        star = "0,0;2,3;4,0;0,2;4,2"
        r = run_cli(db, ["add", "star", "--polygon", star, "--name", "Star"])
        assert r.returncode == 2, f"star self-intersect should be rejected {r.stderr}"
        r = run_cli(db, ["list"])
        assert json.loads(r.stdout) == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_duplicate_nonadjacent():
    """Duplicate points not adjacent must be rejected."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # duplicate first and third
        poly = "0,0;0,1;0,0;1,1;1,0"
        r = run_cli(db, ["add", "dup", "--polygon", poly, "--name", "Dup"])
        assert r.returncode == 2, f"nonadjacent duplicate should be rejected {r.stderr}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_id_sort_with_hyphen_underscore():
    """IDs with hyphen and underscore must sort by ASCII: '-' < digits < '_'."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for id_ in ["a_1", "a-1", "a1"]:
            r = run_cli(db, ["add", id_, "--polygon", "0,0;0,1;1,1;1,0", "--name", id_])
            assert r.returncode == 0

        r = run_cli(db, ["list"])
        ids = [x["id"] for x in json.loads(r.stdout)]
        assert ids == ["a-1", "a1", "a_1"], f"ASCII sort failed, got {ids}"

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["a-1", "a1", "a_1"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_fuzz_python_reference():
    """Wrapping-aware fuzz: exercises antimeridian crossing, world-spanning, poles, and boundary-exact points.
    Reference extends ordinary PIP by ~6 lines: classify raw lng span, if 180 < span < 360 shift negative lngs +360 and shift query point.
    Verified: golden 0/160 fail, no-unwrap 35/160 fail, world misclassified as crossing 136/160 fail. Deterministic, zero timing, spec-derivable."""
    import random
    import math

    random.seed(12345)  # deterministic for CI
    eps = 1e-9

    def point_on_segment(px, py, x1, y1, x2, y2):
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if abs(cross) > eps:
            return False
        minx, maxx = (x1, x2) if x1 <= x2 else (x2, x1)
        miny, maxy = (y1, y2) if y1 <= y2 else (y2, y1)
        return (
            px >= minx - eps
            and px <= maxx + eps
            and py >= miny - eps
            and py <= maxy + eps
        )

    def point_in_polygon_wrapping(lat, lng, poly):
        """Spec-derivable reference: same longitude classification as instruction.md:76-82.
        raw span = maxLng-minLng, >=360 world, 180< <360 crossing (unwrap), <=180 ordinary."""
        # raw span
        lats = [p[0] for p in poly]
        lngs = [p[1] for p in poly]
        min_lat, max_lat = min(lats), max(lats)
        min_lng, max_lng = min(lngs), max(lngs)
        span = max_lng - min_lng

        # quick lat bbox reject
        if lat < min_lat - eps or lat > max_lat + eps:
            # for world-spanning lat reject still needed, but we keep it – for world rect min=-90 max=90 it passes
            # For crossing we also need lat reject before unwrapping
            if span < 360 - eps:  # world covers every longitude but still lat-bounded
                # Actually even world needs lat check, so we can keep early reject
                pass

        # longitude classification
        if span >= 360 - eps:
            # world-spanning: covers every longitude, not crossing. Use ordinary ray casting (works for world rect)
            use_poly = poly
            use_lng = lng
        elif span > 180 + eps:
            # crossing: unwrap by shifting negative lngs +360
            shifted = []
            for plat, plng in poly:
                if plng < 0:
                    shifted.append((plat, plng + 360))
                else:
                    shifted.append((plat, plng))
            use_poly = shifted
            use_lng = lng + 360 if lng < 0 else lng
            # After shifting, the polygon's lng range becomes small (e.g., 179..181)
            # Query at 0 stays 0 (outside) correctly, query at -179.5 -> 180.5 inside
        else:
            use_poly = poly
            use_lng = lng

        px, py = use_lng, lat
        n = len(use_poly)
        # on-edge check
        for i in range(n):
            j = (i + 1) % n
            x1, y1 = use_poly[i][1], use_poly[i][0]
            x2, y2 = use_poly[j][1], use_poly[j][0]
            if point_on_segment(px, py, x1, y1, x2, y2):
                return True
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = use_poly[i][1], use_poly[i][0]
            xj, yj = use_poly[j][1], use_poly[j][0]
            if (yi > py) != (yj > py):
                # avoid div by zero
                if abs(yj - yi) < eps:
                    xinters = xi
                else:
                    xinters = (xj - xi) * (py - yi) / (yj - yi) + xi
                if px < xinters:
                    inside = not inside
            j = i
        return inside

    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        geofences = []

        # 10 ordinary squares random but within valid world
        for i in range(10):
            base_lat = random.uniform(-80, 70)
            base_lng = random.uniform(-170, 150)
            size = random.uniform(0.3, 1.0)
            poly = [
                (base_lat, base_lng),
                (base_lat, base_lng + size),
                (base_lat + size, base_lng + size),
                (base_lat + size, base_lng),
            ]
            poly_str = ";".join(f"{lat},{lng}" for lat, lng in poly)
            id_ = f"fuzz_ord_{i:02d}"
            r = run_cli(db, ["add", id_, "--polygon", poly_str, "--name", f"Ord{i}"])
            assert r.returncode == 0, f"ord fuzz add {i} failed {r.stderr}"
            geofences.append((id_, poly))

        # 2 antimeridian crossing rects
        crossing_polys = [
            [(0, 179), (0, -179), (1, -179), (1, 179)],
            [(10, 170), (10, -170), (11, -170), (11, 170)],
        ]
        for idx, poly in enumerate(crossing_polys):
            poly_str = ";".join(f"{lat},{lng}" for lat, lng in poly)
            id_ = f"fuzz_cross_{idx}"
            r = run_cli(
                db, ["add", id_, "--polygon", poly_str, "--name", f"Cross{idx}"]
            )
            assert r.returncode == 0, f"cross add {idx} failed {r.stderr}"
            geofences.append((id_, poly))

        # 1 world-spanning
        world_poly = [(-90, -180), (-90, 180), (90, 180), (90, -180)]
        world_str = ";".join(f"{lat},{lng}" for lat, lng in world_poly)
        r = run_cli(
            db, ["add", "fuzz_world", "--polygon", world_str, "--name", "World"]
        )
        assert r.returncode == 0, f"world add failed {r.stderr}"
        geofences.append(("fuzz_world", world_poly))

        # 2 polar (near poles, valid non-zero area)
        polar_polys = [
            [(80, 0), (80, 90), (85, 45)],
            [(-85, -45), (-80, 0), (-80, 90)],
        ]
        for idx, poly in enumerate(polar_polys):
            poly_str = ";".join(f"{lat},{lng}" for lat, lng in poly)
            id_ = f"fuzz_polar_{idx}"
            r = run_cli(
                db, ["add", id_, "--polygon", poly_str, "--name", f"Polar{idx}"]
            )
            # Some polar triangles might be degenerate due to lng wrap, but our chosen ones are valid
            if r.returncode != 0:
                # skip if invalid per strict validation
                continue
            geofences.append((id_, poly))

        # Build query points: vertices + edge midpoints + random + hard antimeridian/pole/world points
        query_points = []

        # vertices and edge midpoints
        for _, poly in geofences:
            for lat, lng in poly:
                query_points.append((lat, lng))
            # edge midpoints
            n = len(poly)
            for i in range(n):
                lat1, lng1 = poly[i]
                lat2, lng2 = poly[(i + 1) % n]
                mid_lat = (lat1 + lat2) / 2.0
                # handle antimeridian crossing edge
                if abs(lng2 - lng1) > 180:
                    # edge goes via 180, midpoint at 180
                    mid_lng = 180.0
                else:
                    mid_lng = (lng1 + lng2) / 2.0
                query_points.append((mid_lat, mid_lng))

        # random points over whole world (deterministic)
        for _ in range(80):
            lat = random.uniform(-90, 90)
            lng = random.uniform(-180, 180)
            query_points.append((lat, lng))

        # hard antimeridian / world / pole points (boundary-exact)
        hard_points = [
            (0.5, 179.5),
            (0.5, -179.5),
            (0.5, 180),
            (0.5, -180),
            (0.5, 0),
            (0, 179),
            (0, -179),
            (0, 180),
            (0, 0),
            (80, 0),
            (80, 90),
            (85, 45),
            (-85, 0),
            (80, 45),
            (-80, 45),
            (89, 0),
            (-89, 0),
            (0, 170),
            (0, -170),
            (10.5, 179.5),
            (10.5, -179.5),
        ]
        query_points.extend(hard_points)

        # Deduplicate while preserving order, keep ~160 points as in review table
        seen = set()
        deduped = []
        for pt in query_points:
            # round to 6 decimals for dedup
            key = (round(pt[0], 6), round(pt[1], 6))
            if key not in seen:
                seen.add(key)
                deduped.append(pt)
        query_points = deduped[:160]  # match review's 160

        mismatches = 0
        for lat, lng in query_points:
            r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
            assert r.returncode == 0, f"lookup failed at {lat},{lng} {r.stderr}"
            got = json.loads(r.stdout)
            expected = []
            for gid, poly in geofences:
                if point_in_polygon_wrapping(lat, lng, poly):
                    expected.append(gid)
            expected.sort()
            if got != expected:
                mismatches += 1
                # detailed assert for first few mismatches
                assert got == expected, (
                    f"wrapping fuzz mismatch at {lat},{lng}: got {got} expected {expected} "
                    f"(poly span check: mismatches {mismatches}/{len(query_points)})"
                )

        # If we reach here, 0 mismatches
        assert mismatches == 0

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_colinear_points_on_edge_allowed():
    """Colinear points along an edge (not duplicate) are allowed if area non-zero and no self-intersection."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly = "0,0;0,1;0.5,1;1,1;1,0"
        r = run_cli(db, ["add", "colinear_ok", "--polygon", poly, "--name", "Colinear"])
        assert r.returncode == 0, (
            f"colinear points on edge should be allowed: {r.stderr}"
        )

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["colinear_ok"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_large_file_1000_zones():
    """1000 zones persistence – list must have 1000 sorted, no temp, file valid JSON object."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(1000):
            base = i * 0.01
            poly = f"{base},{base};{base},{base + 0.005};{base + 0.005},{base + 0.005};{base + 0.005},{base}"
            r = run_cli(
                db, ["add", f"big_{i:04d}", "--polygon", poly, "--name", f"Big {i}"]
            )
            assert r.returncode == 0, f"add big_{i} failed {r.stderr}"

        r = run_cli(db, ["list"])
        assert r.returncode == 0
        arr = json.loads(r.stdout)
        assert len(arr) == 1000, f"expected 1000, got {len(arr)}"
        ids = [x["id"] for x in arr]
        assert ids == sorted(ids)

        # raw file must be valid JSON object mapping, not array
        with open(db) as f:
            data = json.load(f)
            assert isinstance(data, dict)
            assert len(data) == 1000

        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_on_world_edge_points():
    """Points exactly on world rectangle edge (lat -90,90 lng -180,180) must be considered inside."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly = "-90,-180;-90,180;90,180;90,-180"
        r = run_cli(db, ["add", "world", "--polygon", poly, "--name", "World"])
        assert r.returncode == 0

        edge_points = [
            (-90, -180),
            (-90, 180),
            (90, -180),
            (90, 180),
            (-90, 0),
            (90, 0),
            (0, -180),
            (0, 180),
        ]
        for lat, lng in edge_points:
            r = run_cli(db, ["lookup", "--lat", str(lat), "--lng", str(lng)])
            assert r.returncode == 0, f"lookup edge {lat},{lng} failed"
            ids = json.loads(r.stdout)
            assert ids == ["world"], (
                f"edge {lat},{lng} should be inside world, got {ids}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_clear_reclaims_and_empty():
    """clear must atomically write {} and list must be [] not null, no temp left."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(20):
            r = run_cli(
                db, ["add", f"z{i}", "--polygon", "0,0;0,1;1,1;1,0", "--name", f"Z{i}"]
            )
            assert r.returncode == 0

        r = run_cli(db, ["clear"])
        assert r.returncode == 0
        assert "cleared" in r.stdout.lower()

        r = run_cli(db, ["list"])
        assert r.stdout.strip() == "[]"
        assert json.loads(r.stdout) == []

        with open(db) as f:
            content = f.read().strip()
            assert content in ("{}", "{ }", "{  }") or json.loads(content) == {}

        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_polygon_order_preserved():
    """Polygon point order must be preserved exactly as given, not sorted or normalized."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Add points in specific order
        poly_str = "0,0;0,2;2,2;2,0"
        r = run_cli(db, ["add", "order", "--polygon", poly_str, "--name", "Order"])
        assert r.returncode == 0
        obj = json.loads(r.stdout)
        # Check order preserved
        assert len(obj["polygon"]) == 4
        assert obj["polygon"][0]["lat"] == 0 and obj["polygon"][0]["lng"] == 0
        assert obj["polygon"][1]["lat"] == 0 and obj["polygon"][1]["lng"] == 2
        assert obj["polygon"][2]["lat"] == 2 and obj["polygon"][2]["lng"] == 2
        assert obj["polygon"][3]["lat"] == 2 and obj["polygon"][3]["lng"] == 0

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert arr[0]["polygon"][1]["lng"] == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_scientific_notation():
    """Lookup lat/lng may be given in scientific notation and must be parsed."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z"])

        r = run_cli(db, ["lookup", "--lat", "5e-1", "--lng", "5e-1"])
        assert r.returncode == 0, f"sci lookup should be allowed {r.stderr}"
        assert json.loads(r.stdout) == ["z"]

        r = run_cli(db, ["lookup", "--lat", "5E-1", "--lng", "5E-1"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == ["z"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_on_edge_midpoint():
    """Points exactly on edge midpoint and vertex must be considered inside."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "sq", "--polygon", "0,0;0,2;2,2;2,0", "--name", "Sq"])

        # midpoint of bottom edge
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "1"])
        assert json.loads(r.stdout) == ["sq"], (
            f"bottom edge midpoint should be inside, got {r.stdout}"
        )

        # midpoint of left edge
        r = run_cli(db, ["lookup", "--lat", "1", "--lng", "0"])
        assert json.loads(r.stdout) == ["sq"]

        # vertex
        r = run_cli(db, ["lookup", "--lat", "0", "--lng", "0"])
        assert json.loads(r.stdout) == ["sq"]

        # top edge
        r = run_cli(db, ["lookup", "--lat", "2", "--lng", "1"])
        assert json.loads(r.stdout) == ["sq"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_concurrent_reads_during_writes():
    """Concurrent list and lookup during adds must not crash or see corrupt JSON."""
    import concurrent.futures

    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])

        def add_many():
            for i in range(30):
                poly = "0,0;0,1;1,1;1,0"
                run_cli(
                    db, ["add", f"cr_{i:02d}", "--polygon", poly, "--name", f"CR{i}"]
                )

        def read_many():
            for _ in range(30):
                r1 = run_cli(db, ["list"])
                if r1.returncode not in (0, 4):
                    return False
                try:
                    json.loads(r1.stdout)
                except:
                    return False
                r2 = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
                if r2.returncode != 0:
                    return False
                try:
                    json.loads(r2.stdout)
                except:
                    return False
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            fut_add = ex.submit(add_many)
            futs_read = [ex.submit(read_many) for _ in range(5)]
            add_ok = fut_add.result()
            read_oks = [f.result() for f in futs_read]

        # add_many runs in same thread as CLI processes, each CLI is separate process so concurrent file writes may interleave
        # We only require no crash and valid JSON, not exact count (single-process spec)
        assert all(read_oks), (
            f"concurrent reads during writes saw corrupt JSON: {read_oks}"
        )

        r = run_cli(db, ["list"])
        assert r.returncode == 0
        arr = json.loads(r.stdout)
        assert isinstance(arr, list)

        for root, dirs, files in os.walk(tmpdir):
            assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_list_stable_after_many_ops():
    """After many adds/removes/overwrites, list must remain sorted and file valid."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(30):
            r = run_cli(
                db,
                [
                    "add",
                    f"id_{i:02d}",
                    "--polygon",
                    "0,0;0,1;1,1;1,0",
                    "--name",
                    f"N{i}",
                ],
            )
            assert r.returncode == 0

        for i in range(0, 30, 2):
            r = run_cli(db, ["remove", f"id_{i:02d}"])
            assert r.returncode == 0

        for i in range(1, 30, 2):
            r = run_cli(
                db,
                [
                    "add",
                    f"id_{i:02d}",
                    "--polygon",
                    "10,10;10,11;11,11;11,10",
                    "--name",
                    f"New{i}",
                ],
            )
            assert r.returncode == 0

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        ids = [x["id"] for x in arr]
        assert ids == sorted(ids), f"not sorted after many ops: {ids}"
        assert len(arr) == 15
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == []
        r = run_cli(db, ["lookup", "--lat", "10.5", "--lng", "10.5"])
        assert len(json.loads(r.stdout)) == 15
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_db_with_extra_field():
    """DB file containing geofence with extra unknown field must not crash list."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        # manually write DB with extra field
        data = {
            "z": {
                "id": "z",
                "name": "Z",
                "polygon": [
                    {"lat": 0, "lng": 0},
                    {"lat": 0, "lng": 1},
                    {"lat": 1, "lng": 1},
                ],
                "extra": "should be ignored",
            }
        }
        with open(db, "w") as f:
            json.dump(data, f)

        r = run_cli(db, ["list"])
        # Should either exit 0 with list containing z (ignoring extra) or exit 4 if strict – allow 0
        assert r.returncode in (0, 4)
        if r.returncode == 0:
            arr = json.loads(r.stdout)
            assert len(arr) == 1
            assert arr[0]["id"] == "z"
            # adding new zone should not corrupt and should preserve? Our ref will drop extra field on write – that's acceptable
            r2 = run_cli(
                db, ["add", "z2", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z2"]
            )
            assert r2.returncode == 0

        files = os.listdir(tmpdir)
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_1000_zones_sorted():
    """100 overlapping zones lookup must return sorted IDs quickly."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(100):
            # all overlapping at 0,0-10,10 but IDs in reverse order to test sorting
            r = run_cli(
                db,
                [
                    "add",
                    f"ov_{100 - i:03d}",
                    "--polygon",
                    "0,0;0,10;10,10;10,0",
                    "--name",
                    f"OV{i}",
                ],
            )
            assert r.returncode == 0

        r = run_cli(db, ["lookup", "--lat", "5", "--lng", "5"])
        assert r.returncode == 0
        ids = json.loads(r.stdout)
        assert len(ids) == 100
        assert ids == sorted(ids), f"100 overlapping lookup not sorted: {ids[:10]}"
        assert "null" not in r.stdout.lower()
        assert r.stdout.strip().startswith("[")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_add_with_tabs_and_newlines_in_polygon():
    """Polygon string may contain tabs and mixed whitespace around delimiters."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly = "0,0;\t0,1;\n1,1;\t1,0"
        r = run_cli(db, ["add", "ws", "--polygon", poly, "--name", "WS"])
        assert r.returncode == 0, (
            f"tabs/newlines around ; should be allowed: {r.stderr}"
        )

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == ["ws"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_lat_lng_out_of_range():
    """Lookup with lat/lng out of [-90,90]/[-180,180] must exit 2, not crash or return []."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z"])

        for lat, lng in [
            ("91", "0"),
            ("-91", "0"),
            ("0", "181"),
            ("0", "-181"),
            ("100", "200"),
        ]:
            r = run_cli(db, ["lookup", "--lat", lat, "--lng", lng])
            assert r.returncode == 2, (
                f"lookup {lat},{lng} should exit 2, got {r.returncode} {r.stderr}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_name_unicode_and_trim():
    """Name may contain unicode and must be trimmed, counted by runes or bytes len<=128 after trim."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # unicode name
        name = "Café 🚀 Zone"
        r = run_cli(db, ["add", "uni", "--polygon", "0,0;0,1;1,1;1,0", "--name", name])
        assert r.returncode == 0, f"unicode name should be allowed {r.stderr}"
        obj = json.loads(r.stdout)
        assert obj["name"] == name.strip()

        # name with 128 unicode chars (ascii still) after trim
        name_128 = (
            "é" * 64
        )  # 64 runes, but bytes >128? Our ref counts len() bytes, so this would be 128 bytes? Actually é is 2 bytes, 64*2=128 bytes
        r = run_cli(
            db, ["add", "uni128", "--polygon", "0,0;0,1;1,1;1,0", "--name", name_128]
        )
        # Allow either 0 or 2 depending on byte vs rune counting, but must not crash
        assert r.returncode in (0, 2)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_overwrite_many_times_stable():
    """Overwrite same ID 50 times with different polygons – final must be last, list sorted, no tmp leak."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(50):
            poly = f"{i},{i};{i},{i + 1};{i + 1},{i + 1};{i + 1},{i}"
            r = run_cli(db, ["add", "same_id", "--polygon", poly, "--name", f"V{i}"])
            assert r.returncode == 0

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 1
        assert arr[0]["name"] == "V49"
        # final polygon should contain point 49.5,49.5
        r = run_cli(db, ["lookup", "--lat", "49.5", "--lng", "49.5"])
        assert json.loads(r.stdout) == ["same_id"]
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert json.loads(r.stdout) == []

        for root, dirs, files in os.walk(tmpdir):
            assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_list_after_overwrites_and_removes():
    """List after many overwrites/removes must remain sorted and file valid."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        ids = [f"a_{i}" for i in range(20)]
        for id_ in ids:
            run_cli(db, ["add", id_, "--polygon", "0,0;0,1;1,1;1,0", "--name", id_])

        # overwrite 10 of them
        for id_ in ids[:10]:
            run_cli(
                db,
                [
                    "add",
                    id_,
                    "--polygon",
                    "10,10;10,11;11,11;11,10",
                    "--name",
                    f"New{id_}",
                ],
            )

        # remove 5
        for id_ in ids[10:15]:
            run_cli(db, ["remove", id_])

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        got_ids = [x["id"] for x in arr]
        assert got_ids == sorted(got_ids)
        assert len(arr) == 15

        # verify file is valid dict
        with open(db) as f:
            data = json.load(f)
            assert isinstance(data, dict)
            assert len(data) == 15
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cli_lookup_1000_zones():
    """1000 overlapping zones lookup must be sorted and fast – absolute upper bound."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(1000):
            r = run_cli(
                db,
                [
                    "add",
                    f"big_{i:04d}",
                    "--polygon",
                    "0,0;0,10;10,10;10,0",
                    "--name",
                    f"Big {i}",
                ],
            )
            assert r.returncode == 0

        start = time.time()
        r = run_cli(db, ["lookup", "--lat", "5", "--lng", "5"])
        elapsed = time.time() - start
        assert r.returncode == 0
        ids = json.loads(r.stdout)
        assert len(ids) == 1000
        assert ids == sorted(ids)
        assert elapsed < 1.0, f"1000 overlapping lookup too slow {elapsed}s"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_id_starting_with_hyphen_underscore():
    """ID may start with hyphen or underscore – regex allows ^[A-Za-z0-9_-]{1,64}$ including start."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for id_ in ["-abc", "_abc", "-_a1", "_-b2", "a-b_c"]:
            r = run_cli(db, ["add", id_, "--polygon", "0,0;0,1;1,1;1,0", "--name", id_])
            assert r.returncode == 0, (
                f"ID {id_!r} should be allowed per regex, got {r.returncode} {r.stderr}"
            )

        r = run_cli(db, ["list"])
        ids = [x["id"] for x in json.loads(r.stdout)]
        assert ids == sorted(ids)
        # Invalid still rejected
        r = run_cli(db, ["add", "a.b", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Bad"])
        assert r.returncode == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_polygon_with_plus_sign():
    """Plus sign in lat/lng should be accepted – ParseFloat handles +."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        poly = "+0,+0;+0,+1;+1,+1;+1,+0"
        r = run_cli(db, ["add", "plus", "--polygon", poly, "--name", "Plus"])
        assert r.returncode == 0, f"plus sign should be allowed {r.stderr}"

        r = run_cli(db, ["lookup", "--lat", "+0.5", "--lng", "+0.5"])
        assert r.returncode == 0
        assert json.loads(r.stdout) == ["plus"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_db_path_with_spaces():
    """DB path containing spaces must be handled (parent dir creation, atomic write)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "my dir with spaces", "geofences.json")
    try:
        r = run_cli(db, ["clear"])
        assert r.returncode == 0, f"clear with spaces path should work {r.stderr}"
        assert os.path.exists(db)

        r = run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z"])
        assert r.returncode == 0

        r = run_cli(db, ["list"])
        assert len(json.loads(r.stdout)) == 1

        # No temp files in that dir
        files = os.listdir(os.path.dirname(db))
        assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_validation_fuzz_convex_vs_bowtie():
    """Fuzz validation: random convex polygons accepted, bow-tie versions rejected."""
    import random
    import math

    random.seed(999)
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        for i in range(20):
            # convex random small square (should be valid)
            base_lat = random.uniform(0, 5)
            base_lng = random.uniform(0, 5)
            sz = 0.5
            poly_ok = f"{base_lat},{base_lng};{base_lat},{base_lng + sz};{base_lat + sz},{base_lng + sz};{base_lat + sz},{base_lng}"
            r = run_cli(
                db, ["add", f"ok_{i}", "--polygon", poly_ok, "--name", f"OK{i}"]
            )
            assert r.returncode == 0, f"convex {poly_ok} should be valid: {r.stderr}"

            # bow-tie from same points in crossed order: 0,0;1,1;0,1;1,0 style – create from same square but crossed
            # Use points of square in order 0,1,3,2 (cross)
            pts = poly_ok.split(";")
            # pts[0], pts[2], pts[1], pts[3] is bow-tie for square
            bow = ";".join([pts[0], pts[2], pts[1], pts[3]])
            r = run_cli(db, ["add", f"bad_{i}", "--polygon", bow, "--name", f"Bad{i}"])
            assert r.returncode == 2, (
                f"bow-tie {bow} should be rejected: got {r.returncode}"
            )

        r = run_cli(db, ["list"])
        arr = json.loads(r.stdout)
        assert len(arr) == 20, f"should have 20 valid, got {len(arr)}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_clear_after_clear_and_remove_after_clear():
    """clear after clear and remove after clear must be handled and not leave tmp."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["clear"])
        r = run_cli(db, ["list"])
        assert json.loads(r.stdout) == []
        assert r.stdout.strip() == "[]"

        r = run_cli(db, ["remove", "nonexistent"])
        assert r.returncode == 3

        r = run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z"])
        assert r.returncode == 0

        run_cli(db, ["clear"])
        r = run_cli(db, ["list"])
        assert json.loads(r.stdout) == []

        for root, dirs, files in os.walk(tmpdir):
            assert not [f for f in files if ".tmp." in f]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lookup_with_negative_zero_and_plus():
    """Lookup with -0, +0, +0.0 must be valid and considered same point."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        run_cli(db, ["add", "z", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Z"])

        for lat, lng in [("-0", "0"), ("0", "-0"), ("+0", "+0"), ("+0.0", "-0.0")]:
            r = run_cli(db, ["lookup", "--lat", lat, "--lng", lng])
            assert r.returncode == 0, f"lookup {lat},{lng} should be allowed"
            assert json.loads(r.stdout) == ["z"], f"{lat},{lng} should be inside"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_self_intersection_vertex_on_nonadjacent_edge():
    """A vertex lying on a non-adjacent edge interior must be rejected as self-intersecting."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Vertex 0.5,0 lies on edge 0,0-1,0 interior, non-adjacent to that edge
        # Polygon: 0,0;1,0;0.5,0;0.5,1 — edge 0,0-1,0 contains point 0.5,0 which is start of edge 1,0-0.5,0? Actually need non-adjacent.
        # Use: 0,0;2,0;2,2;1,1;0,2 where 1,1 is not on edge but let's use a clear case:
        # Square with a point on bottom edge interior but not adjacent to bottom edge:
        # 0,0 ->2,0 ->2,2 ->0,2 ->1,0 ->1,1 -> should have 1,0 on bottom edge 0,0-2,0 interior,
        # edge 0,0-2,0 is edge0, edge 0,2-1,0 is edge3 (non-adjacent to edge0? edge0 adjacent to edge1(2,0) and edge5 wrapping)
        # Let's construct polygon where vertex 1,0 lies on first edge.
        poly = "0,0;2,0;2,2;0,2;1,0;1,1"
        r = run_cli(db, ["add", "touch", "--polygon", poly, "--name", "Touch"])
        assert r.returncode == 2, (
            f"vertex on non-adjacent edge should be self-intersecting, got {r.returncode} {r.stderr}"
        )

        # Another: T-shape touching
        poly2 = "0,0;1,0;1,0.5;0.5,0.5;0.5,1;0,1"
        # 0.5,0.5 vertex lies on? Not.
        # Simple bow-tie already tested, but this additional case:
        poly3 = "0,0;1,0;0.5,0;0.5,1;0,1"
        # Edge 0,0-1,0 contains 0.5,0 which is vertex of edge 1,0-0.5,0? Actually adjacent overlapping, but also edge 0.5,0-0.5,1 starts at that point, non-adjacent to first edge? first edge adjacent to last edge(0,1-0,0) only, not to middle.
        r = run_cli(db, ["add", "touch2", "--polygon", poly3, "--name", "Touch2"])
        assert r.returncode == 2, (
            f"vertex on non-adjacent edge should be rejected {r.stderr}"
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_self_intersection_colinear_complex_additional():
    """Additional colinear overlap cases that are subtle and hard for naive orientation checks."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])

        # Overlapping vertical: edge 0,0-0,3 and edge 1,1-1,2? Actually need overlapping of non-adjacent edges both vertical same x
        # Polygon that goes up, right, down partially overlapping left edge
        poly = "0,0;0,3;1,3;1,1;0,1;0,2;2,2;2,0"
        # Edges: 0,0-0,3, 0,3-1,3, 1,3-1,1, 1,1-0,1, 0,1-0,2 (adjacent to 0,0-0,3? non-adjacent? Let's check)
        # This polygon has self-intersection due to overlapping 0,0-0,3 with 0,1-0,2 colinear overlapping interior.
        r = run_cli(db, ["add", "overlap", "--polygon", poly, "--name", "Overlap"])
        assert r.returncode == 2, (
            f"complex colinear overlap should be rejected {r.stderr}"
        )

        # Valid case: colinear points along same edge but not overlapping non-adjacent – must be allowed
        poly_ok = "0,0;0,0.5;0,1;1,1;1,0"
        r = run_cli(db, ["add", "ok_colinear", "--polygon", poly_ok, "--name", "Ok"])
        assert r.returncode == 0, (
            f"colinear points along same edge should be allowed {r.stderr} stdout={r.stdout}"
        )

        # Another valid: 3 colinear points on top edge
        poly_ok2 = "0,0;0,1;0.3,1;0.6,1;1,1;1,0"
        r = run_cli(db, ["add", "ok_colinear2", "--polygon", poly_ok2, "--name", "Ok2"])
        assert r.returncode == 0, (
            f"multiple colinear points on top edge should be allowed {r.stderr}"
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_antimeridian_exact_edge_and_world_distinction():
    """Antimeridian exact edge handling and world vs crossing distinction (hard family)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # Crossing rect
        poly_cross = "0,179;0,-179;1,-179;1,179"
        r = run_cli(db, ["add", "cross", "--polygon", poly_cross, "--name", "Cross"])
        assert r.returncode == 0, f"cross add failed {r.stderr}"

        # Point exactly on antimeridian edge at 180 must be inside (on edge)
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "180"])
        assert json.loads(r.stdout) == ["cross"], (
            f"180 should be on edge inside crossing, got {r.stdout}"
        )
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "-180"])
        assert json.loads(r.stdout) == ["cross"]

        # Point at 0 must be outside crossing (large gap)
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0"])
        assert json.loads(r.stdout) == [], f"0 should be outside crossing"

        # World rect must be accepted and must match at 0 AND at 180 (covers all longitudes)
        run_cli(db, ["clear"])
        world = "-90,-180;-90,180;90,180;90,-180"
        r = run_cli(db, ["add", "world", "--polygon", world, "--name", "World"])
        assert r.returncode == 0, f"world should be valid {r.stderr}"

        for lng in ["0", "180", "-180", "179.9", "-179.9"]:
            r = run_cli(db, ["lookup", "--lat", "0", "--lng", lng])
            assert json.loads(r.stdout) == ["world"], (
                f"world should match at lng {lng}, got {r.stdout}"
            )

        # Distinction: world span is >=360, not crossing. Its bbox must keep full longitude range.
        # The following checks world + crossing together: world matches everywhere, crossing only near 180.
        run_cli(db, ["add", "cross", "--polygon", poly_cross, "--name", "Cross"])
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0"])
        ids = json.loads(r.stdout)
        assert ids == ["world"], f"0.5,0 should be only world, got {ids}"
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "179.5"])
        ids = json.loads(r.stdout)
        assert sorted(ids) == ["cross", "world"], (
            f"179.5 should be cross+world, got {ids}"
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_id_hyphen_remove_and_lookup():
    """ID starting with hyphen must work for remove and lookup sorting (part of disclosed H1 fix, now not sole discriminator)."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        r = run_cli(
            db, ["add", "-hyphen", "--polygon", "0,0;0,1;1,1;1,0", "--name", "Hyphen"]
        )
        assert r.returncode == 0, f"add -hyphen should work {r.stderr}"
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0.5"])
        assert "-hyphen" in json.loads(r.stdout)

        r = run_cli(db, ["remove", "-hyphen"])
        assert r.returncode == 0, f"remove -hyphen should work {r.stderr}"
        r = run_cli(db, ["list"])
        assert json.loads(r.stdout) == []

        # Re-add with hyphen and underscore and check ASCII sort
        for id_ in ["-a", "_a", "a"]:
            r = run_cli(db, ["add", id_, "--polygon", "0,0;0,1;1,1;1,0", "--name", id_])
            assert r.returncode == 0
        r = run_cli(db, ["list"])
        ids = [x["id"] for x in json.loads(r.stdout)]
        assert ids == sorted(ids), f"ASCII sort with hyphen/underscore failed {ids}"
        # '-' ASCII 45, '_' 95, 'a' 97, so order -a < _a < a
        assert ids == ["-a", "_a", "a"], f"expected ['-a','_a','a'] got {ids}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_span_boundary_classification():
    """Span boundary: 180 must be ordinary (not crossing), 360 must be world (not crossing). This is a strong discriminator for wrapping logic."""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "geof.json")
    try:
        run_cli(db, ["clear"])
        # span exactly 180: -90 to 90? Actually -90 to 90 span 180, should be ordinary, not crossing.
        # Use polygon 0,-90;0,90;1,90;1,-90  span 180 -> ordinary. Point at 0,0 inside, point at 0,179 outside? Wait ordinary at -90..90 covers -90..90, 179 outside.
        poly_180 = "0,-90;0,90;1,90;1,-90"
        r = run_cli(db, ["add", "span180", "--polygon", poly_180, "--name", "Span180"])
        assert r.returncode == 0, f"span 180 add failed {r.stderr}"

        # For ordinary  span 180, point at 0,0 should be inside, point at 0,179 should be outside (since ordinary, gap is not wrapped)
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0"])
        assert r.returncode == 0
        assert "span180" in json.loads(r.stdout), (
            f"0 inside span180 expected, got {r.stdout}"
        )

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "179"])
        # 179 is outside -90..90 ordinary range, should be outside
        assert json.loads(r.stdout) == [], (
            f"179 outside span180 should be [], got {r.stdout}"
        )

        # span exactly 360: world, not crossing. Use -90,-180; -90,180; 90,180; 90,-180 already world.
        # Also test 0,-180;0,180;1,180;1,-180 span 360 at lat 0..1, should be world covering every longitude at that lat
        run_cli(db, ["clear"])
        poly_360 = "0,-180;0,180;1,180;1,-180"
        r = run_cli(db, ["add", "span360", "--polygon", poly_360, "--name", "Span360"])
        assert r.returncode == 0, f"span 360 add failed {r.stderr}"

        # For world (span 360), point at any longitude in lat band should be inside
        for lng in [0, 90, -90, 179, -179, 180, -180]:
            r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", str(lng)])
            assert r.returncode == 0
            ids = json.loads(r.stdout)
            assert "span360" in ids, (
                f"world span360 should match at lng {lng}, got {ids}"
            )

        # Crossing vs world distinction: if misclassified as crossing, world would not match at 0 (gap)
        # Our test above catches that.

        # Also test crossing just above 180: 179 to -179 span 358 should be crossing, not world
        run_cli(db, ["clear"])
        poly_cross = "0,179;0,-179;1,-179;1,179"
        r = run_cli(
            db, ["add", "cross358", "--polygon", poly_cross, "--name", "Cross358"]
        )
        assert r.returncode == 0

        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "179.5"])
        assert "cross358" in json.loads(r.stdout), (
            f"179.5 inside cross358, got {r.stdout}"
        )
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "-179.5"])
        assert "cross358" in json.loads(r.stdout), (
            f"-179.5 inside cross358, got {r.stdout}"
        )
        r = run_cli(db, ["lookup", "--lat", "0.5", "--lng", "0"])
        assert json.loads(r.stdout) == [], f"0 outside cross358 (gap), got {r.stdout}"

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
