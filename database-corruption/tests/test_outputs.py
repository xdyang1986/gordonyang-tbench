"""
Grader for dbfsck — a corruption checker/recoverer for a pre-allocated append-only
log file.

Behaviors under test (several are only implied by the instruction):
  - format: 8-byte header ("DBLG" + uint32-LE version 1) then back-to-back records
    (key_len:u32 val_len:u32 key val crc:u32, little-endian); crc is CRC-32 IEEE
    over the record minus its crc field.
  - a record is "valid at offset p" iff its declared size fits within the bytes
    remaining from p AND its crc matches.
  - MAXIMIZE recovered records. A valid record may begin inside another valid
    record's bytes, so greedy "take the first valid record" is not optimal — the
    maximum needs a DP.
  - TRAILING ZERO PADDING IS NOT CORRUPTION: a run of 0x00 bytes at the end of the
    file (after the last recovered record) is unused pre-allocated space; it is
    excluded from `skipped` and does not make the file dirty. Zeros *between*
    records are ordinary skipped bytes, and a record whose value legitimately ends
    in 0x00 must not be trimmed.
  - summary {"recovered":R,"skipped":S}; exit 0 iff S==0, else 1; unusable header
    -> exit 2 with NO output (a pre-existing --out is left untouched).

`scan_max()` is a reference implementation of the whole contract; every test's
expected values are computed from it (or from raw bytes), so hard-coded outputs
cannot pass. All test inputs have a unique optimum, so exact assertions are fair.
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
# Binary format helpers
# --------------------------------------------------------------------------- #
def record(key: bytes, val: bytes) -> bytes:
    body = struct.pack("<II", len(key), len(val)) + key + val
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return body + struct.pack("<I", crc)


def corrupt_content(rec: bytes) -> bytes:
    """Flip a byte inside the key/val region: CRC fails, on-disk size unchanged."""
    assert len(rec) >= 8 + 1 + 4, "record too small to corrupt its content"
    i = 8
    return rec[:i] + bytes([rec[i] ^ 0xFF]) + rec[i + 1 :]


def corrupt_length(rec: bytes, delta: int = 7) -> bytes:
    kl, vl = struct.unpack("<II", rec[:8])
    return struct.pack("<II", kl, vl + delta) + rec[8:]


def db(*records: bytes) -> bytes:
    return HEADER + b"".join(records)


def _record_at(payload, p):
    n = len(payload)
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


def scan_max(payload):
    """Reference contract: maximize recovered records (prefer-take tie-break);
    a trailing run of 0x00 bytes not covered by any record is padding, not skipped.
    Returns (recs, skipped, max_count)."""
    assert payload[:4] == MAGIC and struct.unpack("<I", payload[4:8])[0] == 1
    n = len(payload)
    size = [0] * (n + 1)
    best = [0] * (n + 2)
    for p in range(n - 1, 7, -1):
        best[p] = best[p + 1]
        s = _record_at(payload, p)
        if s is not None:
            size[p] = s
            if 1 + best[p + s] > best[p]:
                best[p] = 1 + best[p + s]
    off = 8
    end = 8
    recs = []
    while off < n:
        s = size[off]
        if s != 0 and 1 + best[off + s] == best[off]:
            recs.append(payload[off : off + s])
            off += s
            end = off
        else:
            off += 1
    padding = 0
    i = n - 1
    while i >= end and payload[i] == 0:
        padding += 1
        i -= 1
    skipped = (n - 8) - sum(len(r) for r in recs) - padding
    return recs, skipped, best[8]


def scan_greedy(payload):
    """Naive first-valid recovery (take every valid record left to right)."""
    n = len(payload)
    off = 8
    recs = []
    while off < n:
        s = _record_at(payload, off)
        if s is not None:
            recs.append(payload[off : off + s])
            off += s
        else:
            off += 1
    return recs


def expected(payload):
    recs, skipped, cnt = scan_max(payload)
    return {"recovered": cnt, "skipped": skipped}, db(*recs)


def run(dbfsck, in_bytes, out=False, expect=None):
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
    assert recovered == data


def test_empty_key_and_empty_value_records_are_valid(dbfsck):
    data = db(record(b"", b""), record(b"k", b""), record(b"", b"v"))
    _proc, summary, _rec, _p = run(dbfsck, data, expect=0)
    assert summary == {"recovered": 3, "skipped": 0}


def test_binary_safe_keys_and_values(dbfsck):
    data = db(record(b"k\x00\t\n", b"v\x00\n"), record(b"\xff\xfe", b"\x00\x01\x02"))
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == {"recovered": 2, "skipped": 0}
    assert recovered == data


# --------------------------------------------------------------------------- #
# Content corruption
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


def test_adjacent_content_corrupt_records(dbfsck):
    r0, r1, r2, r3 = (record(bytes([65 + i]), b"v" * (i + 1)) for i in range(4))
    data = db(r0, corrupt_content(r1), corrupt_content(r2), r3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(r0, r3)


def test_all_records_corrupt(dbfsck):
    r0, r1 = record(b"x", b"11"), record(b"y", b"22")
    data = db(corrupt_content(r0), corrupt_content(r1))
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 0
    assert recovered == exp_bytes == db()


# --------------------------------------------------------------------------- #
# Length corruption — must resync to records after the corrupted region
# --------------------------------------------------------------------------- #
def test_length_corruption_midfile_recovers_following_records(dbfsck):
    g1, g2 = record(b"aa", b"11"), record(b"bb", b"22")
    bad = record(b"cc", b"33")
    g4, g5 = record(b"dd", b"44"), record(b"ee", b"55")
    data = db(g1, g2, corrupt_length(bad), g4, g5)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 4
    assert recovered == exp_bytes == db(g1, g2, g4, g5)


def test_length_corruption_first_record_recovers_rest(dbfsck):
    bad = record(b"aa", b"11")
    g2, g3 = record(b"bb", b"22"), record(b"cc", b"33")
    data = db(corrupt_length(bad), g2, g3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(g2, g3)


# --------------------------------------------------------------------------- #
# Maximize records: greedy is strictly worse when records nest inside others.
# --------------------------------------------------------------------------- #
def test_maximize_records_beats_greedy_overlap(dbfsck):
    s1, s2 = record(b"s1", b"aaaa"), record(b"s2", b"bbbb")
    big = record(b"", s1 + s2)
    data = db(big)
    max_recs, max_skipped, max_cnt = scan_max(data)
    greedy_recs = scan_greedy(data)
    assert max_cnt == 2 and db(*max_recs) == db(s1, s2)
    assert len(greedy_recs) == 1 and greedy_recs[0] == big
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"recovered": 2, "skipped": max_skipped}
    assert recovered == db(s1, s2)


def test_overlap_case_detect_only(dbfsck):
    s1, s2 = record(b"s1", b"aaaa"), record(b"s2", b"bbbb")
    data = db(record(b"", s1 + s2))
    _proc, summary, _rec, _p = run(dbfsck, data, out=False, expect=1)
    assert summary["recovered"] == 2


def test_deeper_overlap_three_nested(dbfsck):
    s1, s2, s3 = record(b"p", b"11"), record(b"q", b"22"), record(b"r", b"33")
    big = record(b"", s1 + s2 + s3)
    data = db(big)
    max_recs, max_skipped, max_cnt = scan_max(data)
    greedy_recs = scan_greedy(data)
    assert max_cnt == 3 and db(*max_recs) == db(s1, s2, s3)
    assert len(greedy_recs) == 1 and greedy_recs[0] == big
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"recovered": 3, "skipped": max_skipped}
    assert recovered == db(s1, s2, s3)


# --------------------------------------------------------------------------- #
# Trailing zero padding is not corruption; zeros between records are.
# --------------------------------------------------------------------------- #
def test_trailing_zero_padding_is_clean(dbfsck):
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = db(g1, g2) + b"\x00" * 10  # pre-allocated free space
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == exp_summary == {"recovered": 2, "skipped": 0}
    assert recovered == exp_bytes == db(g1, g2)


def test_header_plus_all_zeros_is_clean(dbfsck):
    data = db() + b"\x00" * 20  # freshly pre-allocated, no records yet
    exp_summary, _exp = expected(data)
    _proc, summary, _rec, _p = run(dbfsck, data, expect=0)
    assert summary == exp_summary == {"recovered": 0, "skipped": 0}


def test_record_value_ending_in_zero_then_padding(dbfsck):
    # The record's value legitimately ends in 0x00; trailing padding follows. The
    # record must be recovered whole (not trimmed), and only the padding ignored.
    g = record(b"k", b"ab\x00")
    data = db(g) + b"\x00" * 6
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == exp_summary == {"recovered": 1, "skipped": 0}
    assert recovered == exp_bytes == db(g)


def test_zeros_between_records_are_skipped(dbfsck):
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = db(g1) + b"\x00" * 5 + g2  # a zero gap in the MIDDLE is corruption
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 2, "skipped": 5}
    assert recovered == exp_bytes == db(g1, g2)


def test_garbage_then_padding(dbfsck):
    g = record(b"a", b"1")
    data = db(g) + b"\x11\x22\x33" + b"\x00" * 4  # garbage then padding
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 1, "skipped": 3}
    assert recovered == exp_bytes == db(g)


# --------------------------------------------------------------------------- #
# Truncated tail
# --------------------------------------------------------------------------- #
def test_truncated_inside_last_record(dbfsck):
    good1, good2 = record(b"a", b"first"), record(b"b", b"second")
    last = record(b"c", b"third-value-that-is-longer")
    data = db(good1, good2, last[:-5])
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(good1, good2)


def test_truncated_partial_length_prefix(dbfsck):
    good = record(b"a", b"1")
    data = db(good) + b"\xff\xee\xdd"  # non-zero dangling bytes (< 8 remain)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 1, "skipped": 3}
    assert recovered == exp_bytes == db(good)


# --------------------------------------------------------------------------- #
# Unusable input -> exit 2, no output file / no clobber
# --------------------------------------------------------------------------- #
def test_bad_magic_is_exit_2(dbfsck):
    data = b"XXXX" + struct.pack("<I", 1) + record(b"a", b"1")
    _proc, _summary, recovered, out_path = run(dbfsck, data, out=True, expect=2)
    assert recovered is None
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
    proc = subprocess.run([dbfsck, "--in", missing], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2


def test_no_clobber_out_on_unusable_input(dbfsck):
    # A pre-existing --out must be left untouched when the input is unusable.
    tmp = tempfile.mkdtemp(prefix="dbfsck_nc_")
    in_path = os.path.join(tmp, "in.db")
    out_path = os.path.join(tmp, "out.db")
    with open(in_path, "wb") as fh:
        fh.write(b"XXXX" + struct.pack("<I", 1) + record(b"a", b"1"))  # bad magic
    sentinel = b"PRIOR OUTPUT THAT MUST SURVIVE"
    with open(out_path, "wb") as fh:
        fh.write(sentinel)
    proc = subprocess.run(
        [dbfsck, "--in", in_path, "--out", out_path],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    with open(out_path, "rb") as fh:
        assert fh.read() == sentinel, "--out was clobbered on an unusable input"


def test_out_overwrites_existing_file_on_success(dbfsck):
    tmp = tempfile.mkdtemp(prefix="dbfsck_ovw_")
    in_path = os.path.join(tmp, "in.db")
    out_path = os.path.join(tmp, "out.db")
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = db(g1, corrupt_content(record(b"z", b"9")), g2)
    with open(in_path, "wb") as fh:
        fh.write(data)
    with open(out_path, "wb") as fh:
        fh.write(b"STALE CONTENT THAT MUST BE REPLACED")
    proc = subprocess.run(
        [dbfsck, "--in", in_path, "--out", out_path],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    with open(out_path, "rb") as fh:
        assert fh.read() == db(g1, g2)


# --------------------------------------------------------------------------- #
# Adversarial: oversized length must not crash / over-allocate
# --------------------------------------------------------------------------- #
def test_huge_length_prefix_does_not_crash(dbfsck):
    good = record(b"a", b"1")
    bomb = struct.pack("<II", 1, 0xFFFFFFFF) + b"k"
    data = db(good) + bomb
    exp_summary, exp_bytes = expected(data)
    proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert "panic" not in proc.stderr.lower(), proc.stderr
    assert summary == exp_summary
    assert recovered == exp_bytes == db(good)


# --------------------------------------------------------------------------- #
# Repaired output is itself clean
# --------------------------------------------------------------------------- #
def test_repaired_output_is_clean_when_rechecked(dbfsck):
    g1, g2, g3 = record(b"a", b"1"), record(b"b", b"2"), record(b"c", b"3")
    data = db(g1, corrupt_content(record(b"x", b"9")), g2, corrupt_length(record(b"y", b"8")), g3)
    _proc, _summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert recovered == db(g1, g2, g3)
    _proc2, summary2, _rec2, _p2 = run(dbfsck, recovered, expect=0)
    assert summary2 == {"recovered": 3, "skipped": 0}


# --------------------------------------------------------------------------- #
# The magic "DBLG" appears only in the 8-byte header; it is ordinary data
# anywhere else. Recovery is by CRC/length framing, never by scanning for magic.
# --------------------------------------------------------------------------- #
def test_magic_bytes_inside_record_value_are_data(dbfsck):
    # A record whose value literally contains "DBLG" + a version word must be
    # recovered normally, not mistaken for a nested header.
    g = record(b"k", b"DBLG" + struct.pack("<I", 1) + b"payload")
    data = db(record(b"a", b"1"), g, record(b"b", b"2"))
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=0)
    assert summary == exp_summary == {"recovered": 3, "skipped": 0}
    assert recovered == exp_bytes == data


def test_magic_bytes_inside_corruption_are_not_a_header(dbfsck):
    g1, g2 = record(b"a", b"first"), record(b"b", b"second")
    # A damaged region that happens to contain the magic (and header-looking
    # bytes) must be skipped, not resynced-to as if a new log started there.
    junk = b"DBLG" + struct.pack("<I", 1) + struct.pack("<II", 2, 99) + b"zz"
    data = db(g1) + junk + g2
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert recovered == exp_bytes == db(g1, g2)


# --------------------------------------------------------------------------- #
# Overlap topology beyond simple nesting: an overlap region followed by a normal
# record. Max = the two inner records + the trailing record (skip the encloser).
# --------------------------------------------------------------------------- #
def test_overlap_region_then_normal_record(dbfsck):
    s1, s2 = record(b"s1", b"aaaa"), record(b"s2", b"bbbb")
    big = record(b"", s1 + s2)
    tail = record(b"g", b"end")
    data = db(big, tail)
    max_recs, max_skipped, max_cnt = scan_max(data)
    greedy_recs = scan_greedy(data)
    assert max_cnt == 3 and db(*max_recs) == db(s1, s2, tail)
    assert len(greedy_recs) == 2  # greedy takes big, then tail
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"recovered": 3, "skipped": max_skipped}
    assert recovered == db(s1, s2, tail)


# --------------------------------------------------------------------------- #
# A damaged region that looks like a fresh header + record (magic + version 1 +
# plausible lengths) must NOT be resynced-to; only genuinely CRC-valid records
# are recovered.
# --------------------------------------------------------------------------- #
def test_fake_header_in_corruption_is_ignored(dbfsck):
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    fake = MAGIC + struct.pack("<I", 1) + corrupt_content(record(b"x", b"yy"))
    data = db(g1) + fake + g2
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert recovered == exp_bytes == db(g1, g2)


# --------------------------------------------------------------------------- #
# No alignment assumptions: a record after an odd-length damaged run must still
# be found (records are not 4-byte aligned).
# --------------------------------------------------------------------------- #
def test_record_after_odd_length_garbage(dbfsck):
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = db(g1) + b"\x11\x22\x33" + g2  # 3 non-zero bytes -> g2 at an odd offset
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 2, "skipped": 3}
    assert recovered == exp_bytes == db(g1, g2)


# --------------------------------------------------------------------------- #
# Bounded reads on the key side too: an oversized key_len must not crash.
# --------------------------------------------------------------------------- #
def test_huge_key_length_does_not_crash(dbfsck):
    good = record(b"a", b"1")
    bomb = struct.pack("<II", 0xFFFFFFFF, 1) + b"k"  # key claims ~4 GiB
    data = db(good) + bomb
    exp_summary, exp_bytes = expected(data)
    proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert "panic" not in proc.stderr.lower(), proc.stderr
    assert summary == exp_summary
    assert recovered == exp_bytes == db(good)


# --------------------------------------------------------------------------- #
# In-place repair: --in and --out may be the same path (read fully before write).
# --------------------------------------------------------------------------- #
def test_in_place_repair_on_clean_file_is_noop(dbfsck):
    tmp = tempfile.mkdtemp(prefix="dbfsck_ipc_")
    path = os.path.join(tmp, "log.db")
    data = db(record(b"a", b"1"), record(b"b", b"2"))
    with open(path, "wb") as fh:
        fh.write(data)
    proc = subprocess.run(
        [dbfsck, "--in", path, "--out", path], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    with open(path, "rb") as fh:
        assert fh.read() == data


def test_in_place_repair_same_path(dbfsck):
    tmp = tempfile.mkdtemp(prefix="dbfsck_ip_")
    path = os.path.join(tmp, "log.db")
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = db(g1, corrupt_content(record(b"z", b"9")), g2)
    with open(path, "wb") as fh:
        fh.write(data)
    proc = subprocess.run(
        [dbfsck, "--in", path, "--out", path],  # same path
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1, f"stderr={proc.stderr!r}"
    with open(path, "rb") as fh:
        assert fh.read() == db(g1, g2), "in-place repair corrupted the file"


# --------------------------------------------------------------------------- #
# Larger input: correctness (and tractable performance) at scale.
# --------------------------------------------------------------------------- #
def test_larger_input_with_scattered_corruption(dbfsck):
    import random

    rng = random.Random(3141592)
    pieces = []
    for i in range(300):
        rec = record(f"key{i}".encode(), bytes(rng.randrange(256) for _ in range(rng.randint(0, 24))))
        if rng.random() < 0.1 and len(rec) >= 13:
            pieces.append(corrupt_content(rec))
        elif rng.random() < 0.1:
            pieces.append(corrupt_length(rec, delta=rng.choice([1, 5, 9])))
        else:
            pieces.append(rec)
    data = db(*pieces) + b"\x00" * 32
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True)
    assert summary == exp_summary
    assert recovered == exp_bytes


# --------------------------------------------------------------------------- #
# Randomized model (unique optima -> exact assertions). Mixes clean / content-
# corrupt / length-corrupt records and optional trailing zero padding.
# --------------------------------------------------------------------------- #
def test_randomized_model(dbfsck):
    import random

    rng = random.Random(20260707)
    for trial in range(16):
        n = rng.randint(1, 40)
        pieces = []
        for _ in range(n):
            klen = rng.randint(0, 10)
            vlen = rng.randint(1, 20)
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
        if rng.random() < 0.5:
            data += b"\x00" * rng.randint(1, 16)  # pre-allocated padding
        exp_summary, exp_bytes = expected(data)
        expected_exit = 0 if exp_summary["skipped"] == 0 else 1
        _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=expected_exit)
        assert summary == exp_summary, f"trial {trial}: {summary} != {exp_summary}"
        assert recovered == exp_bytes, f"trial {trial}: recovered mismatch"
