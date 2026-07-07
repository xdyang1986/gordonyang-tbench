"""
Grader for dbfsck — a corruption checker/recoverer for an append-only log file.

Recovery contract: recover the MAXIMUM number of non-overlapping valid records.
A record is "valid at offset p" iff its declared size (8 + key_len + val_len + 4)
fits within the bytes remaining from p AND its CRC matches. Crucially, a valid
record may begin at an offset that lies inside another valid record's bytes, so
the maximum is NOT the greedy "take the first valid record and jump past it" — it
can require skipping a valid record so that more records fit. The summary is
{"recovered":R,"skipped":S}: R records recovered, S bytes not covered by any
recovered record.

Strategy:
  - build the agent's source from /app/src with `go build ./...`
  - enforce the standard-library-only constraint
  - construct database files as raw bytes in Python (struct + zlib.crc32, the same
    IEEE CRC-32 as Go's crc32.ChecksumIEEE), inject corruption, drive the binary
  - `scan_max()` is a reference DP for the exact contract; `scan_greedy()` is the
    naive first-valid recovery, used to *prove* a crafted input is a real
    separator (greedy recovers strictly fewer records)

Fairness on ties: the maximum record count is unique, but the specific set that
achieves it need not be. Exact-output assertions use only inputs with a unique
optimum. The randomized test asserts the invariant maximum count and independently
validates the tool's output (all records valid, non-overlapping, in order, with a
consistent skipped count) — so a different-but-optimal tie-break is not punished.
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
    """Change the declared val_len without changing the on-disk byte count. A
    reader that trusts the length prefix desyncs; the CRC no longer matches."""
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
    """Reference DP: recover the maximum number of records (prefer-take tie-break
    for a deterministic reconstruction). Returns (recs, skipped, max_count)."""
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
    recs = []
    while off < n:
        s = size[off]
        if s != 0 and 1 + best[off + s] == best[off]:
            recs.append(payload[off : off + s])
            off += s
        else:
            off += 1
    skipped = (n - 8) - sum(len(r) for r in recs)
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
    return recs, (n - 8) - sum(len(r) for r in recs)


def expected(payload):
    recs, skipped, cnt = scan_max(payload)
    return {"recovered": cnt, "skipped": skipped}, db(*recs)


def parse_records(payload):
    """Parse a well-formed database file into (list_of_record_bytes). Used to
    validate the tool's --out file; asserts every record is valid and framing is
    exact."""
    assert payload[:8] == HEADER, f"bad header on recovered file: {payload[:8]!r}"
    n = len(payload)
    off = 8
    recs = []
    while off < n:
        s = _record_at(payload, off)
        assert s is not None, f"recovered file has an invalid record at offset {off}"
        recs.append(payload[off : off + s])
        off += s
    assert off == n, "trailing bytes in recovered file"
    return recs


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
# Clean files (unique optimum: take everything)
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
# Content corruption (no overlapping frames -> unique optimum == greedy)
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
# The separator: maximize recovered records where greedy is strictly worse.
# B is a valid record whose value bytes are themselves two valid records S1, S2,
# with nothing after B. Greedy takes B (1 record); the maximum is {S1, S2} (2).
# --------------------------------------------------------------------------- #
def _overlap_case():
    s1 = record(b"s1", b"aaaa")
    s2 = record(b"s2", b"bbbb")
    inner = s1 + s2
    big = record(b"", inner)  # empty key: s1 begins right after big's 8-byte prefix
    return db(big), s1, s2, big


def test_maximize_records_beats_greedy_overlap(dbfsck):
    data, s1, s2, big = _overlap_case()
    # This input must be a genuine separator with a unique optimum of 2 (= {s1,s2}).
    max_recs, max_skipped, max_cnt = scan_max(data)
    greedy_recs, _greedy_skipped = scan_greedy(data)
    assert max_cnt == 2 and db(*max_recs) == db(s1, s2)
    assert len(greedy_recs) == 1 and greedy_recs[0] == big
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"recovered": 2, "skipped": max_skipped}
    assert recovered == db(s1, s2), (
        "must recover the two inner records, not the one enclosing record"
    )


def test_overlap_case_detect_only(dbfsck):
    data, _s1, _s2, _big = _overlap_case()
    _proc, summary, _rec, _p = run(dbfsck, data, out=False, expect=1)
    assert summary["recovered"] == 2


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
    data = db(good) + b"\x03\x00\x00"
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 1, "skipped": 3}
    assert recovered == exp_bytes == db(good)


# --------------------------------------------------------------------------- #
# Unusable input -> exit 2, no output file
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
# More corner cases
# --------------------------------------------------------------------------- #
def test_adjacent_content_corrupt_records(dbfsck):
    r0, r1, r2, r3 = (record(bytes([65 + i]), b"v" * (i + 1)) for i in range(4))
    data = db(r0, corrupt_content(r1), corrupt_content(r2), r3)
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(r0, r3)


def test_only_garbage_after_header(dbfsck):
    # Bytes after the header that cannot form any record: recovered 0, all skipped.
    data = db() + b"\x01\x02\x03\x04\x05"
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary == {"recovered": 0, "skipped": 5}
    assert recovered == exp_bytes == db()


def test_leading_garbage_before_first_record(dbfsck):
    # Garbage immediately after the header, then two clean records.
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = HEADER + b"\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f" + g1 + g2
    exp_summary, exp_bytes = expected(data)
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == exp_summary
    assert summary["recovered"] == 2
    assert recovered == exp_bytes == db(g1, g2)


def test_out_overwrites_existing_file(dbfsck):
    # A pre-existing --out file must be replaced with the repaired content.
    tmp = tempfile.mkdtemp(prefix="dbfsck_ovw_")
    in_path = os.path.join(tmp, "in.db")
    out_path = os.path.join(tmp, "out.db")
    g1, g2 = record(b"a", b"1"), record(b"b", b"2")
    data = db(g1, corrupt_content(record(b"z", b"9")), g2)
    with open(in_path, "wb") as fh:
        fh.write(data)
    with open(out_path, "wb") as fh:
        fh.write(b"STALE CONTENT THAT MUST BE OVERWRITTEN")
    proc = subprocess.run(
        [dbfsck, "--in", in_path, "--out", out_path],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    with open(out_path, "rb") as fh:
        assert fh.read() == db(g1, g2)


def test_deeper_overlap_three_nested(dbfsck):
    # A big record whose value bytes are three valid records: the maximum is 3
    # (the inner records), greedy takes 1 (the enclosing record).
    s1, s2, s3 = record(b"p", b"11"), record(b"q", b"22"), record(b"r", b"33")
    big = record(b"", s1 + s2 + s3)
    data = db(big)
    max_recs, max_skipped, max_cnt = scan_max(data)
    greedy_recs, _ = scan_greedy(data)
    assert max_cnt == 3 and db(*max_recs) == db(s1, s2, s3)
    assert len(greedy_recs) == 1 and greedy_recs[0] == big
    _proc, summary, recovered, _p = run(dbfsck, data, out=True, expect=1)
    assert summary == {"recovered": 3, "skipped": max_skipped}
    assert recovered == db(s1, s2, s3)


# --------------------------------------------------------------------------- #
# Randomized model: assert the invariant maximum count and validate the tool's
# output (valid, non-overlapping, in order, consistent skipped) — tie-fair.
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
        _recs, _skipped, max_cnt = scan_max(data)
        expected_exit = 0 if max_cnt == n and _skipped == 0 else (0 if _skipped == 0 else 1)
        proc, summary, recovered, _p = run(dbfsck, data, out=True)
        assert summary is not None, f"trial {trial}: no JSON summary"
        # invariant: the maximum number of recoverable records
        assert summary["recovered"] == max_cnt, f"trial {trial}: {summary} cnt!={max_cnt}"
        # validate the tool's own output independently of tie-break choice
        out_recs = parse_records(recovered)
        assert len(out_recs) == max_cnt, f"trial {trial}: output record count"
        assert summary["skipped"] == (len(data) - 8) - sum(len(r) for r in out_recs)
        assert (proc.returncode == 0) == (summary["skipped"] == 0), f"trial {trial}: exit"
