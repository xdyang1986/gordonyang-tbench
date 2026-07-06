"""
Grader for the persistent ordered key-value CLI.

Strategy:
  - build the agent's source from /app/src with `go build ./...` (a broken build
    fails the whole task)
  - enforce the standard-library-only constraint by scanning imports + go.mod
  - drive the resulting binary over its documented CLI contract with randomized
    data, so hard-coded outputs cannot pass

Each test uses a fresh --db path so cases are isolated, and because every command
is a separate process, correctness across invocations also proves persistence.
"""

import os
import random
import re
import shutil
import string
import subprocess
import tempfile

import pytest

SRC_DIR = "/app/src"


# --------------------------------------------------------------------------- #
# Build the agent's binary once for the whole session.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def dbctl():
    assert os.path.isdir(SRC_DIR), f"{SRC_DIR} does not exist"
    go_files = [f for f in _walk_go(SRC_DIR)]
    assert go_files, f"no .go source files found under {SRC_DIR}"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "missing /app/src/go.mod"

    go = shutil.which("go")
    assert go, "the go toolchain is not available in the verifier environment"

    out_dir = tempfile.mkdtemp(prefix="dbctl_build_")
    binary = os.path.join(out_dir, "dbctl")
    proc = subprocess.run(
        [go, "build", "-o", binary, "./..."],
        cwd=SRC_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"`go build ./...` failed in {SRC_DIR}:\n{proc.stdout}\n{proc.stderr}"
    )
    assert os.path.isfile(binary), "go build did not produce a binary"
    return binary


def _walk_go(root):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".go"):
                yield os.path.join(dirpath, f)


# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #
def run(dbctl, db_path, *args, expect=None):
    proc = subprocess.run(
        [dbctl, "--db", db_path, *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"args={args} expected exit {expect}, got {proc.returncode}; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return proc


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "store.db")


def rand_str(rng, n_min=1, n_max=12):
    n = rng.randint(n_min, n_max)
    alphabet = string.ascii_letters + string.digits
    return "".join(rng.choice(alphabet) for _ in range(n))


# --------------------------------------------------------------------------- #
# Constraint: standard library only
# --------------------------------------------------------------------------- #
def test_go_mod_has_no_external_requires():
    with open(os.path.join(SRC_DIR, "go.mod")) as fh:
        contents = fh.read()
    # any require line pointing at an external module (path with a dot) is a violation
    for line in contents.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            path = m.group(2)
            first = path.split("/")[0]
            assert "." not in first, f"external dependency in go.mod: {path}"


def test_imports_are_stdlib_only():
    import_re = re.compile(r'"([^"]+)"')
    for path in _walk_go(SRC_DIR):
        with open(path) as fh:
            text = fh.read()
        # collect import blocks: import ( ... ) — catches aliased/underscore imports too
        for block in re.findall(r"import\s*\((.*?)\)", text, flags=re.S):
            for imp in import_re.findall(block):
                _assert_stdlib(imp, path)
        # single-line imports, including aliased/underscore/dot forms:
        #   import "x"   import a "x"   import _ "x"   import . "x"
        for imp in re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', text):
            _assert_stdlib(imp, path)


def _assert_stdlib(import_path, src_file):
    first = import_path.split("/")[0]
    assert "." not in first, (
        f"non-stdlib import {import_path!r} in {src_file}"
    )


# --------------------------------------------------------------------------- #
# Core behavior
# --------------------------------------------------------------------------- #
def test_put_then_get(dbctl, db):
    run(dbctl, db, "put", "apple", "red", expect=0)
    proc = run(dbctl, db, "get", "apple", expect=0)
    assert proc.stdout == "red\n"


def test_get_missing_key_exits_3(dbctl, db):
    proc = run(dbctl, db, "get", "nope", expect=3)
    assert proc.stdout == ""


def test_put_overwrites(dbctl, db):
    run(dbctl, db, "put", "k", "v1", expect=0)
    run(dbctl, db, "put", "k", "v2", expect=0)
    proc = run(dbctl, db, "get", "k", expect=0)
    assert proc.stdout == "v2\n"


def test_delete_then_get_missing(dbctl, db):
    run(dbctl, db, "put", "k", "v", expect=0)
    run(dbctl, db, "delete", "k", expect=0)
    run(dbctl, db, "get", "k", expect=3)


def test_delete_missing_is_idempotent(dbctl, db):
    # deleting a key that never existed is not an error
    run(dbctl, db, "delete", "ghost", expect=0)


def test_persistence_across_processes(dbctl, db):
    # each command is its own process; writes must survive to later invocations
    run(dbctl, db, "put", "persist", "yes", expect=0)
    proc = run(dbctl, db, "get", "persist", expect=0)
    assert proc.stdout == "yes\n"


def test_new_db_scan_is_empty(dbctl, db):
    proc = run(dbctl, db, "scan", expect=0)
    assert proc.stdout == ""


def test_default_db_path(dbctl):
    # With no --db flag, the store must default to /app/data/store.db.
    default_path = "/app/data/store.db"
    try:
        os.remove(default_path)
    except FileNotFoundError:
        pass
    p1 = subprocess.run(
        [dbctl, "put", "dk", "dv"], capture_output=True, text=True, timeout=60
    )
    assert p1.returncode == 0, f"put failed: {p1.stderr!r}"
    assert os.path.isfile(default_path), "default db file was not created at /app/data/store.db"
    p2 = subprocess.run(
        [dbctl, "get", "dk"], capture_output=True, text=True, timeout=60
    )
    assert p2.returncode == 0 and p2.stdout == "dv\n"
    os.remove(default_path)


# --------------------------------------------------------------------------- #
# Scan ordering & ranges
# --------------------------------------------------------------------------- #
def test_scan_is_sorted(dbctl, db):
    pairs = {"banana": "yellow", "apple": "red", "cherry": "dark", "date": "brown"}
    for k, v in pairs.items():
        run(dbctl, db, "put", k, v, expect=0)
    proc = run(dbctl, db, "scan", expect=0)
    lines = proc.stdout.splitlines()
    assert lines == [f"{k}\t{pairs[k]}" for k in sorted(pairs)]


def test_scan_range_inclusive_start_exclusive_end(dbctl, db):
    for k in ["a", "b", "c", "d", "e"]:
        run(dbctl, db, "put", k, k.upper(), expect=0)
    proc = run(dbctl, db, "scan", "b", "d", expect=0)
    assert proc.stdout.splitlines() == ["b\tB", "c\tC"]


def test_scan_start_only(dbctl, db):
    for k in ["a", "b", "c"]:
        run(dbctl, db, "put", k, k.upper(), expect=0)
    proc = run(dbctl, db, "scan", "b", expect=0)
    assert proc.stdout.splitlines() == ["b\tB", "c\tC"]


def test_scan_empty_range(dbctl, db):
    for k in ["a", "z"]:
        run(dbctl, db, "put", k, "x", expect=0)
    proc = run(dbctl, db, "scan", "m", "n", expect=0)
    assert proc.stdout == ""


def test_get_after_node_split(dbctl, db):
    # Insert enough keys to force a node split, then every inserted key must
    # still be retrievable — including keys that become internal separators.
    pairs = [("k01", "a"), ("k02", "b"), ("k03", "c"), ("k04", "d"), ("k05", "e")]
    for k, v in pairs:
        run(dbctl, db, "put", k, v, expect=0)
    for k, v in pairs:
        proc = run(dbctl, db, "get", k, expect=0)
        assert proc.stdout == v + "\n", f"get {k} returned {proc.stdout!r}, expected {v!r}"


def test_get_after_delete_rebalance(dbctl, db):
    # Deletions that trigger node rebalancing must not strand live keys: every
    # key still present must be retrievable and consistent with scan.
    for k, v in [("c", "3"), ("d", "4"), ("e", "5"), ("f", "6"), ("g", "7"), ("a", "1")]:
        run(dbctl, db, "put", k, v, expect=0)
    run(dbctl, db, "delete", "f", expect=0)
    run(dbctl, db, "delete", "g", expect=0)
    # remaining keys: a,c,d,e
    for k, v in [("a", "1"), ("c", "3"), ("d", "4"), ("e", "5")]:
        proc = run(dbctl, db, "get", k, expect=0)
        assert proc.stdout == v + "\n", f"get {k} returned {proc.stdout!r}, expected {v!r}"
    # scan and get must agree
    proc = run(dbctl, db, "scan", expect=0)
    assert proc.stdout.splitlines() == ["a\t1", "c\t3", "d\t4", "e\t5"]


def test_get_after_deep_tree_merges(dbctl, db):
    # Build a multi-level tree, then delete a contiguous run to force node
    # merges that propagate up into internal nodes. Every surviving key must
    # remain retrievable and consistent with scan.
    keys = [f"k{n:02d}" for n in range(1, 25)]  # k01..k24
    for k in keys:
        run(dbctl, db, "put", k, "v" + k[1:], expect=0)
    deleted = [f"k{n:02d}" for n in range(5, 13)]  # k05..k12
    for k in deleted:
        run(dbctl, db, "delete", k, expect=0)
    survivors = [k for k in keys if k not in deleted]
    for k in survivors:
        proc = run(dbctl, db, "get", k, expect=0)
        assert proc.stdout == "v" + k[1:] + "\n", f"get {k} returned {proc.stdout!r}"
    for k in deleted:
        run(dbctl, db, "get", k, expect=3)
    proc = run(dbctl, db, "scan", expect=0)
    assert proc.stdout.splitlines() == [f"{k}\tv{k[1:]}" for k in survivors]


# --------------------------------------------------------------------------- #
# Randomized end-to-end model check
# --------------------------------------------------------------------------- #
def test_randomized_model(dbctl, db):
    rng = random.Random(1234)
    model = {}
    keys_pool = [rand_str(rng) for _ in range(60)]

    for _ in range(500):
        op = rng.random()
        key = rng.choice(keys_pool)
        if op < 0.6:  # put
            val = rand_str(rng, 1, 20)
            run(dbctl, db, "put", key, val, expect=0)
            model[key] = val
        elif op < 0.8:  # delete
            run(dbctl, db, "delete", key, expect=0)
            model.pop(key, None)
        else:  # get, checked against the model
            proc = run(dbctl, db, "get", key)
            if key in model:
                assert proc.returncode == 0 and proc.stdout == model[key] + "\n"
            else:
                assert proc.returncode == 3 and proc.stdout == ""

    # final full scan must equal the sorted model
    proc = run(dbctl, db, "scan", expect=0)
    expected = [f"{k}\t{model[k]}" for k in sorted(model)]
    assert proc.stdout.splitlines() == expected


def test_scan_range_matches_model(dbctl, db):
    rng = random.Random(99)
    model = {}
    for _ in range(200):
        k = rand_str(rng)
        v = rand_str(rng)
        run(dbctl, db, "put", k, v, expect=0)
        model[k] = v

    lo, hi = sorted([rand_str(rng), rand_str(rng)])
    proc = run(dbctl, db, "scan", lo, hi, expect=0)
    expected = [f"{k}\t{model[k]}" for k in sorted(model) if lo <= k < hi]
    assert proc.stdout.splitlines() == expected


# --------------------------------------------------------------------------- #
# Usage errors
# --------------------------------------------------------------------------- #
def test_unknown_command_is_error_not_3(dbctl, db):
    proc = run(dbctl, db, "frobnicate")
    assert proc.returncode not in (0, 3)


def test_wrong_arg_count_is_error(dbctl, db):
    proc = run(dbctl, db, "put", "onlykey")
    assert proc.returncode not in (0, 3)
