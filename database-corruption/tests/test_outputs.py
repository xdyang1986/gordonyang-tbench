"""
Grader for dbfsck — a corruption checker/recoverer for an append-only log file.

Strategy:
  - build the agent's source from /app/src with `go build ./...` (a broken build
    fails the whole task)
  - enforce the standard-library-only constraint by scanning imports + go.mod
  - construct database files as raw bytes in Python (struct + zlib.crc32, which is
    the same IEEE CRC-32 as Go's crc32.ChecksumIEEE), inject corruption, then drive
    the built binary over its CLI contract and check the JSON summary, the exit
    code, and the recovered output file.

Because every test builds its inputs from randomized bytes, hard-coded outputs
cannot pass. For cases where every reasonable implementation must agree (clean
files, content corruption that leaves framing intact, and truncated tails) the
tests assert exact counts and the exact recovered file. For adversarial length
corruption — where a smarter recovery strategy could legitimately differ — the
tests only require that the tool does not crash, reports corruption, and never
emits an invalid record.
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
    """Flip a byte inside the key/val region so the CRC fails but framing (the
    8-byte length prefix) stays intact and the record still occupies the same
    number of bytes."""
    assert len(rec) >= 8 + 1 + 4, "record too small to corrupt its content"
    # Byte 8 is the first byte after the length prefix (start of key, or of val if
    # the key is empty). Flipping it never changes key_len/val_len or the size.
    i = 8
    return rec[:i] + bytes([rec[i] ^ 0xFF]) + rec[i + 1 :]


def db(*records: bytes) -> bytes:
    return HEADER + b"".join(records)


def run(dbfsck, in_bytes, out=False, expect=None):
    """Write in_bytes to a temp file, run dbfsck, optionally with --out. Returns
    (proc, summary_or_None, recovered_bytes_or_None)."""
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


def split_records(payload: bytes):
    """Parse a (well-formed) database file into its list of raw record bytes.
    Only used on outputs the tool claims are clean, so it may assume valid framing."""
    assert payload[:8] == HEADER, f"bad header on recovered file: {payload[:8]!r}"
    body = payload[8:]
    off = 0
    recs = []
    while off < len(body):
        key_len, val_len = struct.unpack("<II", body[off : off + 8])
        size = 8 + key_len + val_len + 4
        recs.append(body[off : off + size])
        off += size
    assert off == len(body), "trailing bytes in a file that should be clean"
    return recs


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
# Clean files
# --------------------------------------------------------------------------- #
def test_clean_file_reports_all_valid(dbfsck):
    data = db(record(b"a", b"1"), record(b"b", b"2"), record(b"c", b"3"))
    _proc, summary, _rec, _p = run(dbfsck, data, expect=0)
    assert summary == {"valid": 3, "corrupt": 0, "truncated": 0}


def test_empty_database_header_only_is_clean(dbfsck):
    _proc, summary, _rec, _p = run(dbfsck, db(), expect=0)
    assert summary == {"valid": 0, "corrupt": 0, "truncated": 0}


def test_clean_file_with_out_is_byte_identical(dbfsck):
    data = db(record(b"alpha", b"one"), record(b"beta", b"two"))
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == {"valid": 2, "corrupt": 0, "truncated": 0}
    assert recovered == data, "clean file recovered with --out must be identical"


def test_empty_key_and_empty_value_records_are_valid(dbfsck):
    data = db(record(b"", b""), record(b"k", b""), record(b"", b"v"))
    _proc, summary, _rec, _p = run(dbfsck, data, expect=0)
    assert summary == {"valid": 3, "corrupt": 0, "truncated": 0}


def test_binary_safe_keys_and_values(dbfsck):
    # The format is binary, so NUL/tab/newline in keys and values are ordinary data.
    data = db(
        record(b"k\x00\t\n", b"v\x00\n"),
        record(b"\xff\xfe", b"\x00\x01\x02"),
    )
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == {"valid": 2, "corrupt": 0, "truncated": 0}
    assert recovered == data


# --------------------------------------------------------------------------- #
# Content corruption (framing intact -> deterministic for any implementation)
# --------------------------------------------------------------------------- #
def test_single_content_corrupt_record_is_dropped_and_rest_recovered(dbfsck):
    good1, good2, good3 = record(b"a", b"1"), record(b"b", b"2"), record(b"c", b"3")
    data = db(good1, corrupt_content(good2), good3)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 2, "corrupt": 1, "truncated": 0}
    # scanning must continue past the corrupt record: both good records survive.
    assert recovered == db(good1, good3)


def test_multiple_corrupt_records_counted(dbfsck):
    recs = [record(bytes([65 + i]), bytes([48 + i])) for i in range(6)]
    corrupted = list(recs)
    corrupted[1] = corrupt_content(corrupted[1])
    corrupted[4] = corrupt_content(corrupted[4])
    data = db(*corrupted)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 4, "corrupt": 2, "truncated": 0}
    assert recovered == db(recs[0], recs[2], recs[3], recs[5])


def test_first_and_last_record_corrupt(dbfsck):
    r0, r1, r2 = record(b"x", b"1"), record(b"y", b"2"), record(b"z", b"3")
    data = db(corrupt_content(r0), r1, corrupt_content(r2))
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 1, "corrupt": 2, "truncated": 0}
    assert recovered == db(r1)


def test_all_records_corrupt(dbfsck):
    r0, r1 = record(b"x", b"11"), record(b"y", b"22")
    data = db(corrupt_content(r0), corrupt_content(r1))
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 0, "corrupt": 2, "truncated": 0}
    assert recovered == db()  # header only


# --------------------------------------------------------------------------- #
# Truncated tail (deterministic: everything before the partial record survives)
# --------------------------------------------------------------------------- #
def test_truncated_inside_last_record(dbfsck):
    good1, good2 = record(b"a", b"first"), record(b"b", b"second")
    last = record(b"c", b"third-value-that-is-longer")
    # Chop strictly inside the last record so it cannot form a complete record.
    data = db(good1, good2, last[:-5])
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 2, "corrupt": 0, "truncated": 1}
    assert recovered == db(good1, good2)


def test_truncated_partial_length_prefix(dbfsck):
    # Only a few bytes after a clean record: fewer than 8 bytes remain.
    good = record(b"a", b"1")
    data = db(good) + b"\x03\x00\x00"  # 3 dangling bytes
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 1, "corrupt": 0, "truncated": 1}
    assert recovered == db(good)


def test_truncated_only_no_valid_records(dbfsck):
    # A single partial record right after the header.
    partial = record(b"key", b"value")[:6]
    data = db() + partial
    _proc, summary, _rec, _p = run(dbfsck, data, expect=1)
    assert summary == {"valid": 0, "corrupt": 0, "truncated": 1}


def test_corrupt_then_truncated(dbfsck):
    good = record(b"a", b"1")
    bad = corrupt_content(record(b"b", b"2"))
    tail = record(b"c", b"three")[:-3]
    data = db(good, bad, tail)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"valid": 1, "corrupt": 1, "truncated": 1}
    assert recovered == db(good)


# --------------------------------------------------------------------------- #
# Unusable input -> exit 2, no output file
# --------------------------------------------------------------------------- #
def test_bad_magic_is_exit_2(dbfsck):
    data = b"XXXX" + struct.pack("<I", 1) + record(b"a", b"1")
    proc, _summary, recovered, out_path = run(dbfsck, data, out=True, expect=2)
    assert recovered is None, "no output file may be written for an unusable input"
    assert not os.path.isfile(out_path)


def test_wrong_version_is_exit_2(dbfsck):
    data = MAGIC + struct.pack("<I", 2) + record(b"a", b"1")
    _proc, _summary, recovered, out_path = run(dbfsck, data, out=True, expect=2)
    assert recovered is None
    assert not os.path.isfile(out_path)


def test_file_shorter_than_header_is_exit_2(dbfsck):
    _proc, _summary, _rec, _p = run(dbfsck, b"DBL", expect=2)


def test_zero_length_file_is_exit_2(dbfsck):
    _proc, _summary, _rec, _p = run(dbfsck, b"", expect=2)


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
    proc, summary, _rec, _p = run(dbfsck, data, out=False, expect=1)
    assert summary == {"valid": 1, "corrupt": 1, "truncated": 0}


# --------------------------------------------------------------------------- #
# Adversarial: a corrupt length prefix must not crash or hang or over-allocate.
# Recovery strategy after such a record may legitimately differ, so we only
# require: terminates, does not panic, reports corruption, and any output it
# produces contains only genuinely-valid records (a subsequence of the good ones).
# --------------------------------------------------------------------------- #
def test_huge_length_prefix_does_not_crash(dbfsck):
    good = record(b"a", b"1")
    # A record header claiming a ~4 GiB value, with no such bytes present.
    bomb = struct.pack("<II", 1, 0xFFFFFFFF) + b"k"  # deliberately incomplete
    data = db(good) + bomb
    proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert "panic" not in proc.stderr.lower(), proc.stderr
    # the clean record ahead of the bomb must always be recovered
    assert recovered is not None
    _assert_recovered_is_valid_subsequence(recovered, [good])


def test_corrupt_length_prefix_midfile_terminates(dbfsck):
    good1 = record(b"a", b"1")
    good2 = record(b"b", b"2")
    mid = bytearray(record(b"c", b"3"))
    mid[0:8] = struct.pack("<II", 0x40000000, 0x40000000)  # 1 GiB + 1 GiB
    data = db(good1, good2, bytes(mid), record(b"d", b"4"))
    proc, _summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert "panic" not in proc.stderr.lower(), proc.stderr
    assert recovered is not None
    _assert_recovered_is_valid_subsequence(recovered, [good1, good2, record(b"d", b"4")])


def _assert_recovered_is_valid_subsequence(recovered: bytes, all_good: list):
    """Every record in the recovered file must be CRC-valid, and the sequence of
    recovered records must be a subsequence of all_good (in order)."""
    recs = split_records(recovered)
    for rec in recs:
        key_len, val_len = struct.unpack("<II", rec[:8])
        body = rec[: 8 + key_len + val_len]
        stored = struct.unpack("<I", rec[8 + key_len + val_len : 8 + key_len + val_len + 4])[0]
        assert (zlib.crc32(body) & 0xFFFFFFFF) == stored, "emitted an invalid record"
    it = iter(all_good)
    for rec in recs:
        assert rec in it, "recovered records are not an in-order subsequence of the valid ones"


# --------------------------------------------------------------------------- #
# The repaired output is itself a clean database.
# --------------------------------------------------------------------------- #
def test_repaired_output_is_clean_when_rechecked(dbfsck):
    good1 = record(b"a", b"1")
    good2 = record(b"b", b"2")
    data = db(good1, corrupt_content(record(b"x", b"9")), good2)
    _proc, _summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert recovered == db(good1, good2)
    # feeding the repaired file back in must report a fully clean database.
    _proc2, summary2, _rec2, _p2 = run(dbfsck, recovered, expect=0)
    assert summary2 == {"valid": 2, "corrupt": 0, "truncated": 0}


# --------------------------------------------------------------------------- #
# Randomized model check: many records, a random subset content-corrupted.
# Framing stays intact, so valid/corrupt counts and the recovered file are exact.
# --------------------------------------------------------------------------- #
def test_randomized_content_corruption_model(dbfsck):
    import random

    rng = random.Random(20260706)
    for trial in range(12):
        n = rng.randint(1, 40)
        goods = []
        for _ in range(n):
            klen = rng.randint(0, 10)
            vlen = rng.randint(0, 20)
            key = bytes(rng.randrange(256) for _ in range(klen))
            val = bytes(rng.randrange(256) for _ in range(vlen))
            goods.append(record(key, val))

        on_disk = []
        expected_valid = []
        exp_valid = exp_corrupt = 0
        for rec in goods:
            # Only corrupt records big enough that flipping byte 8 is inside key/val.
            if len(rec) >= 8 + 1 + 4 and rng.random() < 0.35:
                on_disk.append(corrupt_content(rec))
                exp_corrupt += 1
            else:
                on_disk.append(rec)
                expected_valid.append(rec)
                exp_valid += 1

        data = db(*on_disk)
        expected_exit = 0 if exp_corrupt == 0 else 1
        _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=expected_exit)
        assert summary == {"valid": exp_valid, "corrupt": exp_corrupt, "truncated": 0}, (
            f"trial {trial}: {summary}"
        )
        assert recovered == db(*expected_valid), f"trial {trial}: recovered mismatch"
