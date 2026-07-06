"""
Grader for the persistent ordered key-value CLI (`dbctl`).

Strategy:
  - build the agent's source from /app/src with `go build ./...` (a broken build
    fails the whole task)
  - enforce the standard-library-only constraint by scanning imports + go.mod
  - drive the resulting binary over its CLI contract with fixed and randomized
    data, so hard-coded outputs cannot pass

Each test uses a fresh --db path, and because every command is a separate
process, correctness across invocations also proves persistence.
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
    assert list(_walk_go(SRC_DIR)), f"no .go source files found under {SRC_DIR}"
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
def run(dbctl, db_path, *args, stdin=None, expect=None):
    proc = subprocess.run(
        [dbctl, "--db", db_path, *args],
        input=stdin,
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
    for line in contents.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            first = m.group(2).split("/")[0]
            assert "." not in first, f"external dependency in go.mod: {m.group(2)}"


def test_imports_are_stdlib_only():
    import_re = re.compile(r'"([^"]+)"')
    for path in _walk_go(SRC_DIR):
        with open(path) as fh:
            text = fh.read()
        for block in re.findall(r"import\s*\((.*?)\)", text, flags=re.S):
            for imp in import_re.findall(block):
                _assert_stdlib(imp, path)
        for imp in re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', text):
            _assert_stdlib(imp, path)


def _assert_stdlib(import_path, src_file):
    first = import_path.split("/")[0]
    assert "." not in first, f"non-stdlib import {import_path!r} in {src_file}"


# --------------------------------------------------------------------------- #
# Core behavior
# --------------------------------------------------------------------------- #
def test_put_then_get(dbctl, db):
    run(dbctl, db, "put", "apple", "red", expect=0)
    assert run(dbctl, db, "get", "apple", expect=0).stdout == "red\n"


def test_get_missing_key_exits_3(dbctl, db):
    proc = run(dbctl, db, "get", "nope", expect=3)
    assert proc.stdout == ""


def test_put_overwrites(dbctl, db):
    run(dbctl, db, "put", "k", "v1", expect=0)
    run(dbctl, db, "put", "k", "v2", expect=0)
    assert run(dbctl, db, "get", "k", expect=0).stdout == "v2\n"


def test_delete_then_get_missing(dbctl, db):
    run(dbctl, db, "put", "k", "v", expect=0)
    run(dbctl, db, "delete", "k", expect=0)
    run(dbctl, db, "get", "k", expect=3)


def test_delete_missing_is_idempotent(dbctl, db):
    run(dbctl, db, "delete", "ghost", expect=0)


def test_persistence_across_processes(dbctl, db):
    run(dbctl, db, "put", "persist", "yes", expect=0)
    assert run(dbctl, db, "get", "persist", expect=0).stdout == "yes\n"


def test_new_db_scan_is_empty(dbctl, db):
    assert run(dbctl, db, "scan", expect=0).stdout == ""


def test_creates_missing_parent_directories(dbctl, tmp_path):
    # The db path points into directories that do not exist yet; the first write
    # must create them.
    nested = str(tmp_path / "a" / "b" / "c" / "store.db")
    run(dbctl, nested, "put", "k", "v", expect=0)
    assert os.path.isfile(nested), "db file (and parent dirs) not created"
    assert run(dbctl, nested, "get", "k", expect=0).stdout == "v\n"


# --------------------------------------------------------------------------- #
# Ordering (implicit: bytewise over raw key bytes)
# --------------------------------------------------------------------------- #
def test_scan_is_bytewise_sorted(dbctl, db):
    # Uppercase precedes lowercase; digit strings sort lexicographically.
    for k in ["banana", "Apple", "apple", "10", "9", "Zed"]:
        run(dbctl, db, "put", k, "x", expect=0)
    got = [ln.split("\t")[0] for ln in run(dbctl, db, "scan", expect=0).stdout.splitlines()]
    assert got == ["10", "9", "Apple", "Zed", "apple", "banana"], got


# --------------------------------------------------------------------------- #
# Scan ranges (implicit: START inclusive, END exclusive; arg forms)
# --------------------------------------------------------------------------- #
def test_scan_range_half_open(dbctl, db):
    for k in ["a", "b", "c", "d", "e"]:
        run(dbctl, db, "put", k, k.upper(), expect=0)
    assert run(dbctl, db, "scan", "b", "d", expect=0).stdout.splitlines() == ["b\tB", "c\tC"]


def test_scan_start_only_is_from_start_to_end(dbctl, db):
    for k in ["a", "b", "c"]:
        run(dbctl, db, "put", k, k.upper(), expect=0)
    assert run(dbctl, db, "scan", "b", expect=0).stdout.splitlines() == ["b\tB", "c\tC"]


def test_scan_no_args_is_all(dbctl, db):
    for k in ["a", "b", "c"]:
        run(dbctl, db, "put", k, k.upper(), expect=0)
    assert run(dbctl, db, "scan", expect=0).stdout.splitlines() == ["a\tA", "b\tB", "c\tC"]


def test_scan_empty_range(dbctl, db):
    for k in ["a", "z"]:
        run(dbctl, db, "put", k, "x", expect=0)
    assert run(dbctl, db, "scan", "m", "n", expect=0).stdout == ""


# --------------------------------------------------------------------------- #
# Values (implicit: empty value is a real value; spaces preserved)
# --------------------------------------------------------------------------- #
def test_empty_value_is_distinct_from_missing(dbctl, db):
    run(dbctl, db, "put", "e", "", expect=0)
    proc = run(dbctl, db, "get", "e", expect=0)  # present -> exit 0
    assert proc.stdout == "\n", f"empty value should print a blank line, got {proc.stdout!r}"
    run(dbctl, db, "get", "absent", expect=3)  # missing -> exit 3


def test_value_with_spaces_preserved(dbctl, db):
    run(dbctl, db, "put", "greeting", "hello world  two", expect=0)
    assert run(dbctl, db, "get", "greeting", expect=0).stdout == "hello world  two\n"


# --------------------------------------------------------------------------- #
# batch (implicit: all-or-nothing — a malformed line aborts the whole batch)
# --------------------------------------------------------------------------- #
def test_batch_applies_all(dbctl, db):
    script = "put a 1\nput b 2\ndelete a\nput c hello world\n"
    run(dbctl, db, "batch", stdin=script, expect=0)
    assert run(dbctl, db, "scan", expect=0).stdout.splitlines() == ["b\t2", "c\thello world"]


def test_batch_last_write_wins_within_batch(dbctl, db):
    run(dbctl, db, "batch", stdin="put k 1\nput k 2\nput k 3\n", expect=0)
    assert run(dbctl, db, "get", "k", expect=0).stdout == "3\n"


def test_batch_blank_lines_ignored(dbctl, db):
    run(dbctl, db, "batch", stdin="put a 1\n\n\nput b 2\n", expect=0)
    assert run(dbctl, db, "scan", expect=0).stdout.splitlines() == ["a\t1", "b\t2"]


def test_batch_malformed_line_aborts_whole_batch(dbctl, db):
    # Pre-existing state must be preserved; no operation in the batch may apply.
    run(dbctl, db, "put", "keep", "orig", expect=0)
    proc = run(dbctl, db, "batch", stdin="put x 9\nput y 8\nTOTALLY BAD LINE\ndelete keep\n")
    assert proc.returncode != 0, "a malformed batch line must fail the batch"
    # none of the batch operations may have taken effect:
    run(dbctl, db, "get", "x", expect=3)
    run(dbctl, db, "get", "y", expect=3)
    assert run(dbctl, db, "get", "keep", expect=0).stdout == "orig\n"


def test_batch_bad_line_last_still_aborts(dbctl, db):
    run(dbctl, db, "put", "keep", "orig", expect=0)
    proc = run(dbctl, db, "batch", stdin="put p 1\nput q 2\nnonsense\n")
    assert proc.returncode != 0
    run(dbctl, db, "get", "p", expect=3)
    run(dbctl, db, "get", "q", expect=3)


def test_batch_empty_value(dbctl, db):
    run(dbctl, db, "batch", stdin="put empty \n", expect=0)
    assert run(dbctl, db, "get", "empty", expect=0).stdout == "\n"


# --------------------------------------------------------------------------- #
# Randomized model check (mix of single ops and batches)
# --------------------------------------------------------------------------- #
def test_randomized_model(dbctl, db):
    rng = random.Random(20260706)
    model = {}
    pool = [rand_str(rng) for _ in range(50)]

    for _ in range(400):
        r = rng.random()
        key = rng.choice(pool)
        if r < 0.45:
            val = rng.choice(["", rand_str(rng, 1, 15)])
            run(dbctl, db, "put", key, val, expect=0)
            model[key] = val
        elif r < 0.6:
            run(dbctl, db, "delete", key, expect=0)
            model.pop(key, None)
        elif r < 0.75:
            # a valid batch of a few ops
            lines, pending = [], {}
            for _ in range(rng.randint(1, 4)):
                k = rng.choice(pool)
                if rng.random() < 0.7:
                    v = rng.choice(["", rand_str(rng, 1, 8)])
                    lines.append(f"put {k} {v}")
                    pending[k] = ("put", v)
                else:
                    lines.append(f"delete {k}")
                    pending[k] = ("delete", None)
            run(dbctl, db, "batch", stdin="\n".join(lines) + "\n", expect=0)
            for k, (kind, v) in pending.items():
                if kind == "put":
                    model[k] = v
                else:
                    model.pop(k, None)
        else:
            proc = run(dbctl, db, "get", key)
            if key in model:
                assert proc.returncode == 0 and proc.stdout == model[key] + "\n"
            else:
                assert proc.returncode == 3 and proc.stdout == ""

    proc = run(dbctl, db, "scan", expect=0)
    assert proc.stdout.splitlines() == [f"{k}\t{model[k]}" for k in sorted(model)]


# --------------------------------------------------------------------------- #
# Usage errors
# --------------------------------------------------------------------------- #
def test_unknown_command_is_error_not_3(dbctl, db):
    assert run(dbctl, db, "frobnicate").returncode not in (0, 3)


def test_wrong_arg_count_is_error(dbctl, db):
    assert run(dbctl, db, "put", "onlykey").returncode not in (0, 3)
