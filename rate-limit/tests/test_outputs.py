"""
Grader for persistent log-structured token bucket rate limiter CLI (`rlctl`).

Strategy:
  - build agent source from /app/src with `go build ./...`
  - enforce stdlib-only
  - drive binary over CLI with fixed and randomized cases
"""

import os, random, re, shutil, string, subprocess, tempfile
import pytest

SRC_DIR = "/app/src"


@pytest.fixture(scope="session")
def rlctl():
    assert os.path.isdir(SRC_DIR)
    assert list(_walk_go(SRC_DIR))
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod"))
    go = shutil.which("go")
    assert go
    out_dir = tempfile.mkdtemp(prefix="rlctl_build_")
    binary = os.path.join(out_dir, "rlctl")
    proc = subprocess.run(
        [go, "build", "-o", binary, "./..."],
        cwd=SRC_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"go build failed:\n{proc.stdout}\n{proc.stderr}"
    assert os.path.isfile(binary)
    return binary


def _walk_go(root):
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".go"):
                yield os.path.join(dirpath, f)


def run(rlctl, db_path, *args, stdin=None, expect=None):
    proc = subprocess.run(
        [rlctl, "--db", db_path, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"args={args} expected {expect} got {proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return proc


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "store.db")


def rand_str(rng, n_min=1, n_max=8):
    n = rng.randint(n_min, n_max)
    alphabet = string.ascii_letters + string.digits
    return "".join(rng.choice(alphabet) for _ in range(n))


def test_go_mod_has_no_external_requires():
    with open(os.path.join(SRC_DIR, "go.mod")) as fh:
        contents = fh.read()
    for line in contents.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            first = m.group(2).split("/")[0]
            assert "." not in first, f"external dependency {m.group(2)}"


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


def test_set_then_peek(rlctl, db):
    run(rlctl, db, "set", "svc", "10", "5", expect=0)
    assert run(rlctl, db, "peek", "svc", "0", expect=0).stdout.strip() == "10"


def test_peek_missing_exits_3(rlctl, db):
    proc = run(rlctl, db, "peek", "nope", "0", expect=3)
    assert proc.stdout == ""


def test_allow_success(rlctl, db):
    run(rlctl, db, "set", "a", "10", "1", expect=0)
    out = run(rlctl, db, "allow", "a", "3", "0", expect=0)
    assert out.stdout.strip() == "allow"
    assert run(rlctl, db, "peek", "a", "0", expect=0).stdout.strip() == "7"


def test_allow_deny(rlctl, db):
    run(rlctl, db, "set", "a", "5", "0", expect=0)
    out = run(rlctl, db, "allow", "a", "6", "0", expect=3)
    assert out.stdout.strip() == "deny"
    # state unchanged
    assert run(rlctl, db, "peek", "a", "0", expect=0).stdout.strip() == "5"


def test_allow_missing_is_deny(rlctl, db):
    out = run(rlctl, db, "allow", "ghost", "1", "0", expect=3)
    assert out.stdout.strip() == "deny"


def test_set_overwrites_resets(rlctl, db):
    run(rlctl, db, "set", "k", "10", "5", expect=0)
    run(rlctl, db, "allow", "k", "8", "0", expect=0)
    run(rlctl, db, "set", "k", "20", "2", expect=0)
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "20"


def test_delete_then_peek_missing(rlctl, db):
    run(rlctl, db, "set", "k", "10", "1", expect=0)
    run(rlctl, db, "delete", "k", expect=0)
    run(rlctl, db, "peek", "k", "0", expect=3)


def test_delete_missing_is_idempotent(rlctl, db):
    run(rlctl, db, "delete", "ghost", expect=0)


def test_persistence_across_processes(rlctl, db):
    run(rlctl, db, "set", "p", "10", "0", expect=0)
    run(rlctl, db, "allow", "p", "4", "0", expect=0)
    assert run(rlctl, db, "peek", "p", "0", expect=0).stdout.strip() == "6"


def test_new_db_scan_empty(rlctl, db):
    assert run(rlctl, db, "scan", expect=0).stdout == ""


def test_creates_missing_parent_directories(rlctl, tmp_path):
    nested = str(tmp_path / "a" / "b" / "c" / "store.db")
    run(rlctl, nested, "set", "k", "5", "1", expect=0)
    assert os.path.isfile(nested)
    assert run(rlctl, nested, "peek", "k", "0", expect=0).stdout.strip() == "5"


def test_scan_bytewise_sorted(rlctl, db):
    for k in ["banana", "Apple", "apple", "10", "9", "Zed"]:
        run(rlctl, db, "set", k, "1", "1", expect=0)
    got = [
        ln.split("\t")[0] for ln in run(rlctl, db, "scan", expect=0).stdout.splitlines()
    ]
    assert got == ["10", "9", "Apple", "Zed", "apple", "banana"]


def test_scan_range(rlctl, db):
    for k in ["a", "b", "c", "d", "e"]:
        run(rlctl, db, "set", k, "1", "1", expect=0)
    out = run(rlctl, db, "scan", "b", "d", expect=0).stdout.splitlines()
    keys = [l.split("\t")[0] for l in out]
    assert keys == ["b", "c"]


def test_scan_start_only(rlctl, db):
    for k in ["a", "b", "c"]:
        run(rlctl, db, "set", k, "2", "1", expect=0)
    keys = [
        l.split("\t")[0]
        for l in run(rlctl, db, "scan", "b", expect=0).stdout.splitlines()
    ]
    assert keys == ["b", "c"]


def test_peek_refill_math(rlctl, db):
    run(rlctl, db, "set", "s", "10", "5", expect=0)  # capacity10 refill5 per sec
    run(rlctl, db, "allow", "s", "10", "0", expect=0)  # consume all at 0
    # at 1000ms refill 5
    assert run(rlctl, db, "peek", "s", "1000", expect=0).stdout.strip() == "5"
    # at 2000ms refill to 10 cap
    assert run(rlctl, db, "peek", "s", "2000", expect=0).stdout.strip() == "10"
    # at 2500ms still 10
    assert run(rlctl, db, "peek", "s", "2500", expect=0).stdout.strip() == "10"


def test_allow_refill_and_consume(rlctl, db):
    run(rlctl, db, "set", "x", "10", "2", expect=0)
    run(rlctl, db, "allow", "x", "10", "0", expect=0)
    # 1500 ms later refill 3
    run(rlctl, db, "allow", "x", "3", "1500", expect=0)
    assert run(rlctl, db, "peek", "x", "1500", expect=0).stdout.strip() == "0"


def test_batch_applies_all(rlctl, db):
    script = "set a 10 1\nset b 5 0\ndelete a\nset c 7 2\n"
    run(rlctl, db, "batch", stdin=script, expect=0)
    keys = [
        l.split("\t")[0] for l in run(rlctl, db, "scan", expect=0).stdout.splitlines()
    ]
    assert keys == ["b", "c"]


def test_batch_allow_success(rlctl, db):
    run(rlctl, db, "set", "k", "10", "0", expect=0)
    run(rlctl, db, "batch", stdin="allow k 4 0\nallow k 3 0\n", expect=0)
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "3"


def test_batch_blank_ignored(rlctl, db):
    run(rlctl, db, "batch", stdin="set a 1 1\n\n\nset b 2 2\n", expect=0)
    keys = [
        l.split("\t")[0] for l in run(rlctl, db, "scan", expect=0).stdout.splitlines()
    ]
    assert keys == ["a", "b"]


def test_batch_malformed_aborts(rlctl, db):
    run(rlctl, db, "set", "keep", "5", "1", expect=0)
    proc = run(rlctl, db, "batch", stdin="set x 1 1\nbad line\ndelete keep\n")
    assert proc.returncode != 0
    # keep still exists, x not created
    assert run(rlctl, db, "peek", "keep", "0", expect=0).stdout.strip() == "5"
    run(rlctl, db, "peek", "x", "0", expect=3)


def test_batch_allow_deny_aborts(rlctl, db):
    run(rlctl, db, "set", "k", "5", "0", expect=0)
    proc = run(rlctl, db, "batch", stdin="allow k 3 0\nallow k 3 0\n")
    assert proc.returncode != 0
    # no change
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "5"


def stats(rlctl, db):
    out = run(rlctl, db, "stats", expect=0).stdout.strip()
    m = re.fullmatch(r"live=(\d+)\tdead=(\d+)", out)
    assert m
    return int(m.group(1)), int(m.group(2))


def test_stats_empty(rlctl, db):
    assert stats(rlctl, db) == (0, 0)


def test_stats_counts_live(rlctl, db):
    for k in ["a", "b", "c"]:
        run(rlctl, db, "set", k, "1", "1", expect=0)
    assert stats(rlctl, db) == (3, 0)


def test_overwrites_create_dead(rlctl, db):
    run(rlctl, db, "set", "k", "10", "1", expect=0)
    run(rlctl, db, "set", "k", "10", "1", expect=0)
    run(rlctl, db, "set", "k", "10", "1", expect=0)
    assert stats(rlctl, db) == (1, 2)


def test_delete_present_leaves_two_dead(rlctl, db):
    run(rlctl, db, "set", "k", "5", "0", expect=0)
    run(rlctl, db, "delete", "k", expect=0)
    assert stats(rlctl, db) == (0, 2)


def test_delete_absent_still_tombstone(rlctl, db):
    run(rlctl, db, "delete", "ghost", expect=0)
    assert stats(rlctl, db) == (0, 1)


def test_allow_success_counts_dead(rlctl, db):
    run(rlctl, db, "set", "k", "10", "0", expect=0)
    run(rlctl, db, "allow", "k", "1", "0", expect=0)
    run(rlctl, db, "allow", "k", "1", "0", expect=0)
    assert stats(rlctl, db) == (1, 2)  # 3 records total 1 live


def test_batch_records_count(rlctl, db):
    run(rlctl, db, "batch", stdin="set k 10 0\nallow k 2 0\n", expect=0)
    assert stats(rlctl, db) == (1, 1)


def test_compact_reclaims(rlctl, db):
    run(rlctl, db, "set", "k", "10", "1", expect=0)
    run(rlctl, db, "allow", "k", "5", "0", expect=0)
    run(rlctl, db, "set", "gone", "5", "0", expect=0)
    run(rlctl, db, "delete", "gone", expect=0)
    run(rlctl, db, "set", "keep", "7", "2", expect=0)
    assert stats(rlctl, db)[1] > 0
    run(rlctl, db, "compact", expect=0)
    assert stats(rlctl, db) == (2, 0)
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "5"
    assert run(rlctl, db, "peek", "keep", "0", expect=0).stdout.strip() == "7"
    run(rlctl, db, "peek", "gone", "0", expect=3)


def test_compact_empty(rlctl, db):
    run(rlctl, db, "compact", expect=0)
    assert stats(rlctl, db) == (0, 0)


def test_compact_durable(rlctl, db):
    run(rlctl, db, "set", "k", "10", "0", expect=0)
    for _ in range(5):
        run(rlctl, db, "allow", "k", "1", "0", expect=0)
    run(rlctl, db, "compact", expect=0)
    assert stats(rlctl, db) == (1, 0)
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "5"


def test_writes_after_compact_accumulate_dead(rlctl, db):
    run(rlctl, db, "set", "k", "10", "0", expect=0)
    run(rlctl, db, "compact", expect=0)
    assert stats(rlctl, db) == (1, 0)
    run(rlctl, db, "allow", "k", "2", "0", expect=0)
    assert stats(rlctl, db) == (1, 1)


def test_randomized_model(rlctl, db):
    rng = random.Random(20260709)
    model = {}  # key -> (cap,refill,tokens,last)
    total = 0
    pool = [rand_str(rng) for _ in range(30)]
    for step in range(300):
        r = rng.random()
        key = rng.choice(pool)
        if r < 0.3:  # set
            cap = rng.randint(5, 20)
            ref = rng.randint(0, 5)
            run(rlctl, db, "set", key, str(cap), str(ref), expect=0)
            model[key] = (cap, ref, cap, 0)
            total += 1
        elif r < 0.5:  # allow attempt
            if key not in model:
                continue
            cap, ref, tok, last = model[key]
            ts = last + rng.randint(0, 3000)
            need = rng.randint(1, cap)
            avail = min(cap, tok + ref * (ts - last) // 1000)
            proc = run(rlctl, db, "allow", key, str(need), str(ts))
            if avail >= need:
                assert proc.returncode == 0 and proc.stdout.strip() == "allow"
                model[key] = (cap, ref, avail - need, ts)
                total += 1
            else:
                assert proc.returncode == 3 and proc.stdout.strip() == "deny"
        elif r < 0.65:  # delete
            run(rlctl, db, "delete", key, expect=0)
            model.pop(key, None)
            total += 1
        elif r < 0.8:  # peek check
            if key in model:
                cap, ref, tok, last = model[key]
                ts = last + rng.randint(0, 2000)
                avail = min(cap, tok + ref * (ts - last) // 1000)
                out = run(rlctl, db, "peek", key, str(ts), expect=0).stdout.strip()
                assert out == str(avail)
            else:
                run(rlctl, db, "peek", key, "0", expect=3)
        elif r < 0.9:  # stats
            assert stats(rlctl, db) == (len(model), total - len(model))
        else:  # compact
            run(rlctl, db, "compact", expect=0)
            total = len(model)
    assert stats(rlctl, db) == (len(model), total - len(model))
    # scan check
    proc = run(rlctl, db, "scan", expect=0)
    lines = proc.stdout.splitlines()
    expected = sorted(model.items())
    assert len(lines) == len(expected)
    for line, (k, (cap, ref, tok, last)) in zip(lines, expected):
        parts = line.split("\t")
        assert (
            parts[0] == k
            and int(parts[1]) == cap
            and int(parts[2]) == ref
            and int(parts[3]) == tok
            and int(parts[4]) == last
        )
    run(rlctl, db, "compact", expect=0)
    assert stats(rlctl, db) == (len(model), 0)


def test_unknown_command(rlctl, db):
    assert run(rlctl, db, "frobnicate").returncode not in (0, 3)


def test_wrong_arg_count(rlctl, db):
    assert run(rlctl, db, "set", "onlykey").returncode not in (0, 3)
