"""
Grader for dbfsck — a corruption checker/recoverer for an append-only log file.

Recovery contract: forward resynchronization. Starting after the 8-byte header,
if a valid record (declared size fits the remaining bytes AND CRC matches) begins
at the cursor, it is output and the cursor advances past it; otherwise the cursor
advances one byte at a time until a valid record begins, or the file ends. The
summary is {"recovered":R,"skipped":S} — R records output, S bytes passed over.
This means a corrupt length field does NOT desync the rest of the file: records
after a corrupted region are still recovered. A naive "advance by the declared
length" reader loses everything after the first bad length and fails these tests.

Strategy:
  - build the agent's source from /app/src with `go build ./...`
  - enforce the standard-library-only constraint by scanning imports + go.mod
  - construct database files as raw bytes in Python (struct + zlib.crc32, the same
    IEEE CRC-32 as Go's crc32.ChecksumIEEE), inject corruption, and drive the
    built binary. `scan()` below is a reference implementation of the exact
    recovery contract; every test computes its expected {recovered, skipped} and
    expected recovered bytes from it, so hard-coded outputs cannot pass.
"""

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib

import pytest

SRC_DIR = "/app/src"

MAGIC = b"DBLG"
HEADER = MAGIC + struct.pack("<I", 1)


# --------------------------------------------------------------------------- #
# Build the agent's binary once for the whole session.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def dbfsck():
    assert os.path.isdir(SRC_DIR), f"{SRC_DIR} does not exist"
    assert list(_walk_go(SRC_DIR)), f"no .go source files found under {SRC_DIR}"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "missing /app/src/go.mod"

    go = shutil.which("go")
    assert go, "the go toolchain is not available in the verifier environment"

    out_dir = tempfile.mkdtemp(prefix="dbfsck_build_")
    binary = os.path.join(out_dir, "dbfsck")
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
# Binary format helpers (mirror of the on-disk format from the instruction)
# --------------------------------------------------------------------------- #
def record(key: bytes, val: bytes) -> bytes:
    """A well-formed record whose CRC verifies."""
    body = struct.pack("<II", len(key), len(val)) + key + val
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return body + struct.pack("<I", crc)


def corrupt_content(rec: bytes) -> bytes:
    """Flip a byte inside the key/val region so the CRC fails but the length
    prefix (and the record's on-disk size) is unchanged."""
    assert len(rec) >= 8 + 1 + 4, "record too small to corrupt its content"
    i = 8  # first byte after the length prefix (start of key, or val if key empty)
    return rec[:i] + bytes([rec[i] ^ 0xFF]) + rec[i + 1 :]


def corrupt_length(rec: bytes, delta: int = 7) -> bytes:
    """Overstate the declared val_len without changing the record's on-disk byte
    count. A reader that trusts the length prefix and advances by the declared
    size desyncs (overshoots into later records); a reader that resynchronizes
    still finds the true next record boundary. CRC no longer matches either way."""
    kl, vl = struct.unpack("<II", rec[:8])
    return struct.pack("<II", kl, vl + delta) + rec[8:]


def db(*records: bytes) -> bytes:
    return HEADER + b"".join(records)


def scan(payload: bytes):
    """Reference implementation of the recovery contract. Returns
    (list_of_recovered_record_bytes, skipped_byte_count). Assumes a valid header
    (callers only pass payloads with a good header)."""
    assert payload[:4] == MAGIC and struct.unpack("<I", payload[4:8])[0] == 1
    n = len(payload)

    def record_at(p):
        if n - p < 12:
            return None
        kl, vl = struct.unpack("<II", payload[p : p + 8])
        size = 8 + kl + vl + 4
        if size > n - p:
            return None
        crc_pos = p + 8 + kl + vl
        body = payload[p:crc_pos]
        stored = struct.unpack("<I", payload[crc_pos : crc_pos + 4])[0]
        if (zlib.crc32(body) & 0xFFFFFFFF) != stored:
            return None
        return size

    off = 8
    recs = []
    skipped = 0
    while off < n:
        s = record_at(off)
        if s is not None:
            recs.append(payload[off : off + s])
            off += s
            continue
        start = off
        off += 1
        while off < n and record_at(off) is None:
            off += 1
        skipped += off - start
    return recs, skipped


def expected(payload: bytes):
    recs, skipped = scan(payload)
    return {"recovered": len(recs), "skipped": skipped}, db(*recs)


def run(dbfsck, in_bytes, out=False, expect=None):
    """Write in_bytes to a temp file, run dbfsck (optionally with --out). Returns
    (proc, summary_or_None, recovered_bytes_or_None, out_path)."""
    tmp = tempfile.mkdtemp(prefix="dbfsck_run_")
    in_path = os.path.join(tmp, "in.db")
    with open(in_path, "wb") as fh:
        fh.write(in_bytes)
    args = [dbfsck, "--in", in_path]
    out_path = None
    if out:
        out_path = os.path.join(tmp, "out.db")
        args += ["--out", out_path]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected exit {expect}, got {proc.returncode}; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    summary = None
    if proc.stdout.strip():
        try:
            summary = json.loads(proc.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            summary = None
    recovered = None
    if out_path is not None and os.path.isfile(out_path):
        with open(out_path, "rb") as fh:
            recovered = fh.read()
    return proc, summary, recovered, out_path


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
    found_any = False
    for path in _walk_go(SRC_DIR):
        with open(path) as fh:
            text = fh.read()
        for block in re.findall(r"import\s*\((.*?)\)", text, flags=re.S):
            for imp in import_re.findall(block):
                found_any = True
                _assert_stdlib(imp, path)
        for imp in re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', text):
            found_any = True
            _assert_stdlib(imp, path)
    assert found_any, "no imports found in any .go file (unexpected)"


def _assert_stdlib(import_path, src_file):
    first = import_path.split("/")[0]
    assert "." not in first, f"non-stdlib import {import_path!r} in {src_file}"


# --------------------------------------------------------------------------- #
# Clean files
# --------------------------------------------------------------------------- #
def test_clean_file_reports_all_recovered(dbfsck):
    data = db(record(b"a", b"1"), record(b"b", b"2"), record(b"c", b"3"))
    _proc, summary, _rec, _p = run(dbfsck, data, expect=0)
    assert summary == {"recovered": 3, "skipped": 0}


def test_empty_database_header_only_is_clean(dbfsck):
    _proc, summary, _rec, _p = run(dbfsck, db(), expect=0)
    assert summary == {"recovered": 0, "skipped": 0}


def test_clean_file_with_out_is_byte_identical(dbfsck):
    data = db(record(b"alpha", b"one"), record(b"beta", b"two"))
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == {"recovered": 2, "skipped": 0}
    assert recovered == data, "clean file recovered with --out must be identical"


def test_empty_key_and_empty_value_records_are_valid(dbfsck):
    data = db(record(b"", b""), record(b"k", b""), record(b"", b"v"))
    _proc, summary, _rec, _p = run(dbfsck, data, expect=0)
    assert summary == {"recovered": 3, "skipped": 0}


def test_binary_safe_keys_and_values(dbfsck):
    data = db(
        record(b"k\x00\t\n", b"v\x00\n"),
        record(b"\xff\xfe", b"\x00\x01\x02"),
    )
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == {"recovered": 2, "skipped": 0}
    assert recovered == data


# --------------------------------------------------------------------------- #
# Content corruption (length prefix intact)
# --------------------------------------------------------------------------- #
def test_single_content_corrupt_record_is_dropped_and_rest_recovered(dbfsck):
    good1, good2, good3 = record(b"a", b"1"), record(b"b", b"2"), record(b"c", b"3")
    data = db(good1, corrupt_content(good2), good3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 2, "skipped": len(good2)}
    assert recovered == exp_bytes == db(good1, good3)


def test_multiple_nonadjacent_corrupt_records(dbfsck):
    recs = [record(bytes([65 + i]), bytes([48 + i])) for i in range(6)]
    corrupted = list(recs)
    corrupted[1] = corrupt_content(corrupted[1])
    corrupted[4] = corrupt_content(corrupted[4])
    data = db(*corrupted)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 4
    assert recovered == exp_bytes == db(recs[0], recs[2], recs[3], recs[5])


def test_adjacent_corrupt_records_are_one_skipped_region(dbfsck):
    r0, r1, r2, r3 = (record(bytes([65 + i]), b"v" * (i + 1)) for i in range(4))
    data = db(r0, corrupt_content(r1), corrupt_content(r2), r3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2  # r0 and r3
    assert recovered == exp_bytes == db(r0, r3)


def test_all_records_corrupt(dbfsck):
    r0, r1 = record(b"x", b"11"), record(b"y", b"22")
    data = db(corrupt_content(r0), corrupt_content(r1))
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 0
    assert recovered == exp_bytes == db()  # header only


# --------------------------------------------------------------------------- #
# Length corruption — the separator: records AFTER a corrupted region must still
# be recovered via resynchronization, not lost to a desync.
# --------------------------------------------------------------------------- #
def test_length_corruption_midfile_recovers_following_records(dbfsck):
    g1 = record(b"aa", b"11")
    g2 = record(b"bb", b"22")
    bad = record(b"cc", b"33")
    g4 = record(b"dd", b"44")
    g5 = record(b"ee", b"55")
    data = db(g1, g2, corrupt_length(bad), g4, g5)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    # A resynchronizing reader recovers g1, g2, g4, g5 (only the bad record's
    # bytes are skipped). A naive "advance by declared length" reader overshoots
    # and loses g4/g5.
    assert summary == exp_summary
    assert summary["recovered"] == 4, f"expected 4 recovered, got {summary}"
    assert summary["skipped"] == len(bad)
    assert recovered == exp_bytes == db(g1, g2, g4, g5)


def test_length_corruption_first_record_recovers_rest(dbfsck):
    bad = record(b"aa", b"11")
    g2 = record(b"bb", b"22")
    g3 = record(b"cc", b"33")
    data = db(corrupt_length(bad), g2, g3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(g2, g3)


def test_understated_length_still_resyncs(dbfsck):
    # Understate the length: naive advance undershoots, landing inside the record.
    g1 = record(b"aa", b"1111")
    g2 = record(b"bb", b"2222")
    g3 = record(b"cc", b"3333")
    bad = corrupt_length(g2, delta=-3)
    data = db(g1, bad, g3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(g1, g3)


# --------------------------------------------------------------------------- #
# Truncated tail (a partial trailing record is skipped)
# --------------------------------------------------------------------------- #
def test_truncated_inside_last_record(dbfsck):
    good1, good2 = record(b"a", b"first"), record(b"b", b"second")
    last = record(b"c", b"third-value-that-is-longer")
    data = db(good1, good2, last[:-5])  # chop strictly inside the last record
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(good1, good2)


def test_truncated_partial_length_prefix(dbfsck):
    good = record(b"a", b"1")
    data = db(good) + b"\x03\x00\x00"  # 3 dangling bytes: fewer than 8 remain
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary == {"recovered": 1, "skipped": 3}
    assert recovered == exp_bytes == db(good)


def test_corrupt_then_truncated(dbfsck):
    good = record(b"a", b"1")
    bad = corrupt_content(record(b"b", b"2"))
    tail = record(b"c", b"three")[:-3]
    data = db(good, bad, tail)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 1
    assert recovered == exp_bytes == db(good)


# --------------------------------------------------------------------------- #
# Unusable input -> exit 2, no output file
# --------------------------------------------------------------------------- #
def test_bad_magic_is_exit_2(dbfsck):
    data = b"XXXX" + struct.pack("<I", 1) + record(b"a", b"1")
    _proc, _summary, recovered, out_path = run(dbfsck, data, out=True, expect=2)
    assert recovered is None, "no output file may be written for an unusable input"
    assert not os.path.isfile(out_path)


def test_wrong_version_is_exit_2(dbfsck):
    data = MAGIC + struct.pack("<I", 2) + record(b"a", b"1")
    _proc, _summary, recovered, out_path = run(dbfsck, data, out=True, expect=2)
    assert recovered is None
    assert not os.path.isfile(out_path)


def test_file_shorter_than_header_is_exit_2(dbfsck):
    run(dbfsck, b"DBL", expect=2)


def test_zero_length_file_is_exit_2(dbfsck):
    run(dbfsck, b"", expect=2)


def test_missing_input_file_is_exit_2(dbfsck):
    tmp = tempfile.mkdtemp(prefix="dbfsck_run_")
    missing = os.path.join(tmp, "does_not_exist.db")
    proc = subprocess.run(
        [dbfsck, "--in", missing], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 2


# --------------------------------------------------------------------------- #
# Detect-only mode: no --out means no file is written
# --------------------------------------------------------------------------- #
def test_detect_only_writes_no_file(dbfsck):
    data = db(record(b"a", b"1"), corrupt_content(record(b"b", b"2")))
    exp_summary, _exp_bytes = expected(data)
    _proc, summary, _rec, _p = run(dbfsck, data, out=False, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 1


# --------------------------------------------------------------------------- #
# Adversarial: an oversized length must not crash, hang, or over-allocate.
# --------------------------------------------------------------------------- #
def test_huge_length_prefix_does_not_crash(dbfsck):
    good = record(b"a", b"1")
    bomb = struct.pack("<II", 1, 0xFFFFFFFF) + b"k"  # claims ~4 GiB, none present
    data = db(good) + bomb
    exp_summary, exp_bytes = expected(data)
    proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert "panic" not in proc.stderr.lower(), proc.stderr
    assert summary == exp_summary
    assert recovered == exp_bytes  # the clean record ahead of the bomb survives
    assert db(good) == recovered


# --------------------------------------------------------------------------- #
# The repaired output is itself a clean database.
# --------------------------------------------------------------------------- #
def test_repaired_output_is_clean_when_rechecked(dbfsck):
    g1 = record(b"a", b"1")
    g2 = record(b"b", b"2")
    g3 = record(b"c", b"3")
    data = db(g1, corrupt_content(record(b"x", b"9")), g2, corrupt_length(record(b"y", b"8")), g3)
    _proc, _summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert recovered == db(g1, g2, g3)
    _proc2, summary2, _rec2, _p2 = run(dbfsck, recovered, expect=0)
    assert summary2 == {"recovered": 3, "skipped": 0}


# --------------------------------------------------------------------------- #
# Randomized model check: many records, a random mix of clean / content-corrupt /
# length-corrupt. Expected recovered/skipped and the exact recovered file are
# computed by the reference scan().
# --------------------------------------------------------------------------- #
def test_randomized_model(dbfsck):
    import random

    rng = random.Random(20260707)
    for trial in range(16):
        n = rng.randint(1, 40)
        pieces = []
        for _ in range(n):
            klen = rng.randint(0, 10)
            vlen = rng.randint(1, 20)  # >=1 so content/length corruption is possible
            key = bytes(rng.randrange(256) for _ in range(klen))
            val = bytes(rng.randrange(256) for _ in range(vlen))
            rec = record(key, val)
            r = rng.random()
            if r < 0.20 and len(rec) >= 13:
                pieces.append(corrupt_content(rec))
            elif r < 0.35:
                pieces.append(corrupt_length(rec, delta=rng.choice([1, 3, 7, 11])))
            else:
                pieces.append(rec)
        data = db(*pieces)
        exp_summary, exp_bytes = expected(data)
        expected_exit = 0 if exp_summary["skipped"] == 0 else 1
        _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=expected_exit)
        assert summary == exp_summary, f"trial {trial}: {summary} != {exp_summary}"
        assert recovered == exp_bytes, f"trial {trial}: recovered mismatch"
