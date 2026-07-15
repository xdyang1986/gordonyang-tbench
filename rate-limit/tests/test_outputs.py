"""
Grader for crash-consistent, log-structured token-bucket rate limiter CLI (`rlctl`).

Strategy:
  - build agent source from /app/src with `go build ./...`
  - enforce stdlib-only
  - drive the binary over the CLI with fixed and randomized cases
  - verify the MANDATED on-disk binary record format byte-for-byte
  - inject torn / corrupt / stale-temp files to exercise crash recovery
"""

import os, random, re, shutil, string, struct, subprocess, tempfile, zlib
import pytest

SRC_DIR = "/app/src"


# ---------------------------------------------------------------------------
# On-disk record codec (mirrors the mandated format):
#   record  = uint32be(len) | payload[len] | uint32be(crc32ieee(payload))
#   set     = 'S' | uint32be(keylen) | key | int64be(cap,refill,tokens,last)
#   delete  = 'D' | uint32be(keylen) | key
# ---------------------------------------------------------------------------
def _frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def rec_set(key: bytes, cap, ref, tok, last) -> bytes:
    return _frame(b"S" + struct.pack(">I", len(key)) + key + struct.pack(">qqqq", cap, ref, tok, last))


def rec_del(key: bytes) -> bytes:
    return _frame(b"D" + struct.pack(">I", len(key)) + key)


def decode_all(data: bytes):
    """Decode a well-formed log into a list of ('S'|'D', key, tuple|None)."""
    out, off, n = [], 0, len(data)
    while off < n:
        (plen,) = struct.unpack(">I", data[off : off + 4])
        payload = data[off + 4 : off + 4 + plen]
        (crc,) = struct.unpack(">I", data[off + 4 + plen : off + 8 + plen])
        assert (zlib.crc32(payload) & 0xFFFFFFFF) == crc, "crc mismatch while decoding"
        klen = struct.unpack(">I", payload[1:5])[0]
        key = payload[5 : 5 + klen]
        if payload[0:1] == b"S":
            vals = struct.unpack(">qqqq", payload[5 + klen : 5 + klen + 32])
            out.append(("S", key, vals))
        else:
            out.append(("D", key, None))
        off += 8 + plen
    return out


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


# ---------------------------- stdlib-only ---------------------------------
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


# ---------------------------- core behavior -------------------------------
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


# ---------------------------- scan ----------------------------------------
def test_scan_bytewise_sorted(rlctl, db):
    for k in ["banana", "Apple", "apple", "10", "9", "Zed"]:
        run(rlctl, db, "set", k, "1", "1", expect=0)
    got = [ln.split("\t")[0] for ln in run(rlctl, db, "scan", expect=0).stdout.splitlines()]
    assert got == ["10", "9", "Apple", "Zed", "apple", "banana"]


def test_scan_range(rlctl, db):
    for k in ["a", "b", "c", "d", "e"]:
        run(rlctl, db, "set", k, "1", "1", expect=0)
    out = run(rlctl, db, "scan", "b", "d", expect=0).stdout.splitlines()
    assert [l.split("\t")[0] for l in out] == ["b", "c"]


def test_scan_start_only(rlctl, db):
    for k in ["a", "b", "c"]:
        run(rlctl, db, "set", k, "2", "1", expect=0)
    keys = [l.split("\t")[0] for l in run(rlctl, db, "scan", "b", expect=0).stdout.splitlines()]
    assert keys == ["b", "c"]


def test_scan_full_row_format(rlctl, db):
    run(rlctl, db, "set", "svc", "10", "5", expect=0)
    run(rlctl, db, "allow", "svc", "3", "2000", expect=0)  # tokens 7, last 2000
    line = run(rlctl, db, "scan", expect=0).stdout.splitlines()[0]
    assert line.split("\t") == ["svc", "10", "5", "7", "2000"]


# ---------------------------- refill math ---------------------------------
def test_peek_refill_math(rlctl, db):
    run(rlctl, db, "set", "s", "10", "5", expect=0)
    run(rlctl, db, "allow", "s", "10", "0", expect=0)
    assert run(rlctl, db, "peek", "s", "1000", expect=0).stdout.strip() == "5"
    assert run(rlctl, db, "peek", "s", "2000", expect=0).stdout.strip() == "10"
    assert run(rlctl, db, "peek", "s", "2500", expect=0).stdout.strip() == "10"


def test_allow_refill_and_consume(rlctl, db):
    run(rlctl, db, "set", "x", "10", "2", expect=0)
    run(rlctl, db, "allow", "x", "10", "0", expect=0)
    run(rlctl, db, "allow", "x", "3", "1500", expect=0)
    assert run(rlctl, db, "peek", "x", "1500", expect=0).stdout.strip() == "0"


def test_refill_floor_division(rlctl, db):
    # refill 3/sec, 500ms -> floor(3*500/1000)=1
    run(rlctl, db, "set", "f", "10", "3", expect=0)
    run(rlctl, db, "allow", "f", "10", "0", expect=0)
    assert run(rlctl, db, "peek", "f", "500", expect=0).stdout.strip() == "1"


def test_refill_overflow_saturates(rlctl, db):
    # refill*delta overflows int64; a naive multiply yields a wrong (negative) value.
    run(rlctl, db, "set", "x", "5", "1000000000", expect=0)
    run(rlctl, db, "allow", "x", "5", "0", expect=0)  # tokens 0, last 0
    # delta=1e10 -> refill*delta = 1e19 (> INT64_MAX, < UINT64_MAX): naive int64 goes negative
    assert run(rlctl, db, "peek", "x", "10000000000", expect=0).stdout.strip() == "5"


def test_refill_overflow_hi_word(rlctl, db):
    # refill*delta needs a 128-bit product (hi word != 0); must saturate to capacity.
    run(rlctl, db, "set", "y", "42", "9000000000", expect=0)
    run(rlctl, db, "allow", "y", "42", "0", expect=0)
    assert run(rlctl, db, "peek", "y", "9000000000000000000", expect=0).stdout.strip() == "42"


# ---------------------------- byte-safe keys ------------------------------
def test_key_with_space(rlctl, db):
    run(rlctl, db, "set", "svc a", "10", "5", expect=0)
    assert run(rlctl, db, "peek", "svc a", "0", expect=0).stdout.strip() == "10"
    line = run(rlctl, db, "scan", expect=0).stdout.splitlines()[0]
    assert line.split("\t")[0] == "svc a"


def test_key_with_forbidden_byte_rejected(rlctl, db):
    # A key containing a forbidden byte (TAB or LF) must exit 2 for every command
    # that takes a key, without touching the store. (NUL cannot pass through argv,
    # so it is not exercised here; TAB/LF are the framing-relevant bytes.)
    for bad in ["svc\tx", "svc\nx"]:
        run(rlctl, db, "set", bad, "10", "5", expect=2)
        run(rlctl, db, "delete", bad, expect=2)
        run(rlctl, db, "peek", bad, "0", expect=2)
        run(rlctl, db, "allow", bad, "1", "0", expect=2)
    # nothing was written
    assert run(rlctl, db, "scan", expect=0).stdout == ""


def test_batch_forbidden_key_byte_aborts(rlctl, db):
    # An LF inside a key can only reach batch by escaping the line grammar, but a
    # TAB in a key collides with the field delimiter, so a bare CR (still a legal
    # key byte) stays intact while a genuinely forbidden byte must abort the unit.
    run(rlctl, db, "set", "keep", "5", "1", expect=0)
    proc = run(rlctl, db, "batch", stdin="set\tbad\x00key\t3\t0\n")
    assert proc.returncode == 2
    assert run(rlctl, db, "peek", "keep", "0", expect=0).stdout.strip() == "5"


# ---------------------------- batch (TAB-delimited) -----------------------
def test_batch_applies_all(rlctl, db):
    script = "set\ta\t10\t1\nset\tb\t5\t0\ndelete\ta\nset\tc\t7\t2\n"
    run(rlctl, db, "batch", stdin=script, expect=0)
    keys = [l.split("\t")[0] for l in run(rlctl, db, "scan", expect=0).stdout.splitlines()]
    assert keys == ["b", "c"]


def test_batch_allow_success(rlctl, db):
    run(rlctl, db, "set", "k", "10", "0", expect=0)
    run(rlctl, db, "batch", stdin="allow\tk\t4\t0\nallow\tk\t3\t0\n", expect=0)
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "3"


def test_batch_key_with_space(rlctl, db):
    run(rlctl, db, "batch", stdin="set\ta b\t3\t0\n", expect=0)
    assert run(rlctl, db, "peek", "a b", "0", expect=0).stdout.strip() == "3"


def test_batch_blank_ignored(rlctl, db):
    run(rlctl, db, "batch", stdin="set\ta\t1\t1\n\n\nset\tb\t2\t2\n", expect=0)
    keys = [l.split("\t")[0] for l in run(rlctl, db, "scan", expect=0).stdout.splitlines()]
    assert keys == ["a", "b"]


def test_batch_space_only_line_aborts(rlctl, db):
    # A line that is not empty (even just a space) is not "blank" -> malformed -> abort.
    proc = run(rlctl, db, "batch", stdin="set\ta\t1\t1\n \nset\tb\t2\t2\n")
    assert proc.returncode != 0
    run(rlctl, db, "peek", "a", "0", expect=3)


def test_batch_space_delimited_rejected(rlctl, db):
    # Space-delimited (the naive format) is malformed under the TAB grammar.
    proc = run(rlctl, db, "batch", stdin="set a 1 1\n")
    assert proc.returncode != 0
    run(rlctl, db, "peek", "a", "0", expect=3)


def test_batch_malformed_aborts(rlctl, db):
    run(rlctl, db, "set", "keep", "5", "1", expect=0)
    proc = run(rlctl, db, "batch", stdin="set\tx\t1\t1\nbad line\ndelete\tkeep\n")
    assert proc.returncode != 0
    assert run(rlctl, db, "peek", "keep", "0", expect=0).stdout.strip() == "5"
    run(rlctl, db, "peek", "x", "0", expect=3)


def test_batch_allow_deny_aborts(rlctl, db):
    run(rlctl, db, "set", "k", "5", "0", expect=0)
    proc = run(rlctl, db, "batch", stdin="allow\tk\t3\t0\nallow\tk\t3\t0\n")
    assert proc.returncode != 0
    assert run(rlctl, db, "peek", "k", "0", expect=0).stdout.strip() == "5"


# ---------------------------- stats / compact -----------------------------
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
    for _ in range(3):
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
    assert stats(rlctl, db) == (1, 2)


def test_batch_records_count(rlctl, db):
    run(rlctl, db, "batch", stdin="set\tk\t10\t0\nallow\tk\t2\t0\n", expect=0)
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


def test_compact_output_is_framed_and_sorted(rlctl, db):
    run(rlctl, db, "set", "b", "5", "1", expect=0)
    run(rlctl, db, "set", "a", "9", "2", expect=0)
    run(rlctl, db, "compact", expect=0)
    with open(db, "rb") as fh:
        recs = decode_all(fh.read())
    assert [(t, k) for (t, k, _) in recs] == [("S", b"a"), ("S", b"b")]


# ---------------------------- on-disk format ------------------------------
def test_ondisk_format_set(rlctl, db):
    run(rlctl, db, "set", "a", "10", "5", expect=0)
    with open(db, "rb") as fh:
        assert fh.read() == rec_set(b"a", 10, 5, 10, 0)


def test_ondisk_format_allow_and_delete(rlctl, db):
    run(rlctl, db, "set", "a", "10", "0", expect=0)
    run(rlctl, db, "allow", "a", "4", "7", expect=0)
    run(rlctl, db, "delete", "a", expect=0)
    with open(db, "rb") as fh:
        data = fh.read()
    assert data == rec_set(b"a", 10, 0, 10, 0) + rec_set(b"a", 10, 0, 6, 7) + rec_del(b"a")


# ---------------------------- crash recovery ------------------------------
def test_recover_torn_trailing_partial(rlctl, db):
    with open(db, "wb") as fh:
        fh.write(rec_set(b"a", 10, 5, 10, 0))
        fh.write(rec_set(b"b", 7, 2, 7, 0))
        fh.write(b"\x00\x00\x00\x10partial")  # torn trailing record
    assert run(rlctl, db, "peek", "a", "0", expect=0).stdout.strip() == "10"
    assert run(rlctl, db, "peek", "b", "0", expect=0).stdout.strip() == "7"
    assert stats(rlctl, db) == (2, 0)


def test_next_write_truncates_torn_tail(rlctl, db):
    with open(db, "wb") as fh:
        fh.write(rec_set(b"a", 10, 5, 10, 0))
        fh.write(b"\xde\xad\xbe\xef\x01\x02")  # torn trailing junk
    run(rlctl, db, "set", "c", "5", "5", expect=0)
    with open(db, "rb") as fh:
        recs = decode_all(fh.read())  # must decode cleanly (junk gone)
    assert [(t, k) for (t, k, _) in recs] == [("S", b"a"), ("S", b"c")]


def test_recover_bad_crc_final_record_is_torn(rlctl, db):
    good = rec_set(b"a", 10, 5, 10, 0)
    bad = bytearray(rec_set(b"b", 7, 2, 7, 0))
    bad[len(bad) - 6] ^= 0xFF  # corrupt payload of the final record
    with open(db, "wb") as fh:
        fh.write(good)
        fh.write(bytes(bad))
    assert run(rlctl, db, "peek", "a", "0", expect=0).stdout.strip() == "10"
    run(rlctl, db, "peek", "b", "0", expect=3)
    assert stats(rlctl, db) == (1, 0)


def test_corrupt_midlog_exits_4(rlctl, db):
    r = [rec_set(b"a", 10, 5, 10, 0), rec_set(b"b", 5, 5, 5, 0), rec_set(b"c", 5, 5, 5, 0)]
    data = bytearray(b"".join(r))
    data[5] ^= 0xFF  # corrupt payload of the FIRST record (not at EOF)
    with open(db, "wb") as fh:
        fh.write(bytes(data))
    assert run(rlctl, db, "stats").returncode == 4
    assert run(rlctl, db, "peek", "a", "0").returncode == 4


def test_corrupt_unknown_type_exits_4(rlctl, db):
    # A fully valid frame (correct CRC) but an unknown type byte, followed by a good record.
    payload = b"X" + struct.pack(">I", 1) + b"a"
    bad = _frame(payload)
    with open(db, "wb") as fh:
        fh.write(bad)
        fh.write(rec_set(b"z", 1, 1, 1, 0))
    assert run(rlctl, db, "stats").returncode == 4


def test_compact_ignores_stale_tmp(rlctl, db):
    run(rlctl, db, "set", "a", "10", "0", expect=0)
    run(rlctl, db, "compact", expect=0)
    with open(db + ".compact.tmp", "wb") as fh:
        fh.write(b"garbage that is not a valid log")
    assert stats(rlctl, db) == (1, 0)
    assert run(rlctl, db, "peek", "a", "0", expect=0).stdout.strip() == "10"


# ---------------------------- randomized model ----------------------------
def test_randomized_model(rlctl, db):
    rng = random.Random(20260709)
    model = {}  # key -> (cap,refill,tokens,last)
    total = 0
    pool = [rand_str(rng) for _ in range(30)]
    for step in range(300):
        r = rng.random()
        key = rng.choice(pool)
        if r < 0.3:
            cap = rng.randint(5, 20)
            ref = rng.randint(0, 5)
            run(rlctl, db, "set", key, str(cap), str(ref), expect=0)
            model[key] = (cap, ref, cap, 0)
            total += 1
        elif r < 0.5:
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
        elif r < 0.65:
            run(rlctl, db, "delete", key, expect=0)
            model.pop(key, None)
            total += 1
        elif r < 0.8:
            if key in model:
                cap, ref, tok, last = model[key]
                ts = last + rng.randint(0, 2000)
                avail = min(cap, tok + ref * (ts - last) // 1000)
                out = run(rlctl, db, "peek", key, str(ts), expect=0).stdout.strip()
                assert out == str(avail)
            else:
                run(rlctl, db, "peek", key, "0", expect=3)
        elif r < 0.9:
            assert stats(rlctl, db) == (len(model), total - len(model))
        else:
            run(rlctl, db, "compact", expect=0)
            total = len(model)
    assert stats(rlctl, db) == (len(model), total - len(model))
    proc = run(rlctl, db, "scan", expect=0)
    lines = proc.stdout.splitlines()
    expected = sorted(model.items())
    assert len(lines) == len(expected)
    for line, (k, (cap, ref, tok, last)) in zip(lines, expected):
        parts = line.split("\t")
        assert parts[0] == k and int(parts[1]) == cap and int(parts[2]) == ref and int(parts[3]) == tok and int(parts[4]) == last
    run(rlctl, db, "compact", expect=0)
    assert stats(rlctl, db) == (len(model), 0)


# ---------------------------- usage errors --------------------------------
def test_unknown_command(rlctl, db):
    # Spec: usage errors exit exactly 2 (see the exit-code table in instruction.md).
    assert run(rlctl, db, "frobnicate").returncode == 2


def test_wrong_arg_count(rlctl, db):
    assert run(rlctl, db, "set", "onlykey").returncode == 2
