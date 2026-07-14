"""
Grader for the Go region-diverse provider-routing CLI with a crash-consistent journal.

Strategy:
  - build agent source from /app/src with `go build ./...`
  - enforce stdlib-only
  - drive the built binary; assert region-diverse failover chains + exit codes
  - verify the byte-exact journal format; inject torn/corrupt journals for recovery
  - exercise IMPLICIT routing edges (best-effort-not-strict diversity, second-pass
    order, primary-only capacity, spillover) with corner-case configs
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
HEADER = b"URJRNL01"


# ------------------------- byte-exact journal codec -------------------------
def encode_record(seq, req_id, chain):
    idb = req_id.encode("utf-8")
    body = struct.pack(">I", seq) + struct.pack(">H", len(idb)) + idb + struct.pack(">H", len(chain))
    for pid in chain:
        pb = pid.encode("utf-8")
        body += struct.pack(">H", len(pb)) + pb
    return body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def decode_journal(data):
    assert data[:8] == HEADER, "bad header"
    off, n = 8, len(data)
    recs, ends = [], []
    while off < n:
        start = off
        seq = struct.unpack_from(">I", data, off)[0]; off += 4
        id_len = struct.unpack_from(">H", data, off)[0]; off += 2
        idb = data[off:off + id_len]; off += id_len
        nprov = struct.unpack_from(">H", data, off)[0]; off += 2
        chain = []
        for _ in range(nprov):
            plen = struct.unpack_from(">H", data, off)[0]; off += 2
            chain.append(data[off:off + plen].decode("utf-8")); off += plen
        crc = struct.unpack_from(">I", data, off)[0]; off += 4
        assert (zlib.crc32(data[start:off - 4]) & 0xFFFFFFFF) == crc, "bad crc"
        recs.append((seq, idb.decode("utf-8"), chain))
        ends.append(off)
    return recs, ends


# ------------------------------- build ------------------------------------
def _walk_go(root):
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".go"):
                yield os.path.join(dirpath, f)


@pytest.fixture(scope="session")
def router_bin():
    assert os.path.isdir(SRC_DIR), "/app/src missing"
    assert list(_walk_go(SRC_DIR)), "no .go sources"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "go.mod missing"
    go = shutil.which("go")
    assert go, "go toolchain not found"
    out_dir = tempfile.mkdtemp(prefix="router_build_")
    binary = os.path.join(out_dir, "router")
    proc = subprocess.run([go, "build", "-o", binary, "./..."], cwd=SRC_DIR,
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"go build failed:\n{proc.stdout}\n{proc.stderr}"
    return binary


def run(router_bin, cfg, reqs, journal, resume=False, td=None):
    cfg_path = os.path.join(td, "cfg.json")
    req_path = os.path.join(td, "req.jsonl")
    with open(cfg_path, "w") as f:
        json.dump(cfg, f)
    with open(req_path, "w") as f:
        for r in reqs:
            f.write(json.dumps(r) + "\n")
    args = [router_bin, "--config", cfg_path, "--requests", req_path, "--journal", journal]
    if resume:
        args.append("--resume")
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    out = proc.stdout.strip().splitlines() if proc.stdout.strip() else []
    return proc.returncode, [json.loads(x) for x in out], proc.stderr


def prov(pid, region, latency, cap=10, cost=0.01, err=0.0, status="up"):
    return {"id": pid, "region": region, "latency_ms": latency, "cost_per_1k": cost,
            "error_rate": err, "capacity_rps": cap, "status": status}


# ------------------------------ stdlib-only -------------------------------
def test_go_mod_no_external_requires():
    with open(os.path.join(SRC_DIR, "go.mod")) as fh:
        for line in fh:
            m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line.strip())
            if m and "." in m.group(2).split("/")[0]:
                raise AssertionError(f"external dependency {m.group(2)}")


def test_imports_stdlib_only():
    ext = re.compile(r'"([a-z0-9.\-]+\.[a-z]{2,}/[^"]+)"')
    for path in _walk_go(SRC_DIR):
        with open(path) as fh:
            for line in fh:
                if ext.search(line):
                    raise AssertionError(f"non-stdlib import in {path}: {line.strip()}")


# ------------------------------ basic routing -----------------------------
def test_single_replica_routing_and_journal(router_bin):
    cfg = {"strategy": "latency", "providers": [
        prov("aws-us", "us-east", 40), prov("aws-eu", "eu-west", 40)]}
    reqs = [{"id": "r1", "user_region": "us-east"},
            {"id": "r2", "user_region": "eu-west"},
            {"id": "r3", "user_region": "asia"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, reqs, j, td=td)
        assert code == 0
        assert out == [["aws-us"], ["aws-eu"], ["aws-eu"]]  # r3 tie -> lexicographic aws-eu
        with open(j, "rb") as f:
            data = f.read()
        assert data == (HEADER + encode_record(0, "r1", ["aws-us"])
                        + encode_record(1, "r2", ["aws-eu"]) + encode_record(2, "r3", ["aws-eu"]))


def test_cost_and_error_rate_scoring(router_bin):
    cfg = {"strategy": "cost", "providers": [
        prov("fast-exp", "us", 10, cost=0.1), prov("slow-cheap", "us", 100, cost=0.01)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        assert run(router_bin, cfg, [{"id": "r", "user_region": "us"}], j, td=td)[1] == [["slow-cheap"]]
    cfg2 = {"strategy": "latency", "providers": [
        prov("err-high", "us", 10, err=0.1), prov("err-low", "us", 10, err=0.001)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        assert run(router_bin, cfg2, [{"id": "r", "user_region": "us"}], j, td=td)[1] == [["err-low"]]


# ---------------------- IMPLICIT region-diversity edges --------------------
def test_best_effort_diversity_not_strict(router_bin):
    # max_replicas=3, three providers across only TWO regions. A strict distinct-region
    # reading returns 2 (under-replicated); the correct best-effort reading reuses a
    # region in the second pass to reach 3 distinct providers -> exit 0.
    cfg = {"strategy": "latency", "max_replicas": 3, "providers": [
        prov("us1", "us", 30), prov("us2", "us", 31), prov("eu1", "eu", 32)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, [{"id": "1", "user_region": "us"}], j, td=td)
        assert out == [["us1", "eu1", "us2"]]  # pass1: us1,eu1 ; pass2 fills us2
        assert code == 0


def test_second_pass_preserves_score_order(router_bin):
    # Single region, so first pass picks only the best; second pass fills the rest in
    # ascending score order (us2 before us3), NOT arbitrary order.
    cfg = {"strategy": "latency", "max_replicas": 3, "providers": [
        prov("us1", "us", 30), prov("us2", "us", 31), prov("us3", "us", 32)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, [{"id": "1", "user_region": "us"}], j, td=td)
        assert out == [["us1", "us2", "us3"]]
        assert code == 0


def test_primary_only_capacity_consumption(router_bin):
    # Only the PRIMARY (first) provider in a chain consumes capacity. a,b each cap 1.
    # req1 -> [a,b] consumes a only; req2 -> [b,c] consumes b only (b still had capacity).
    # If a naive impl consumed every replica, req2 would be just [c] (degraded).
    cfg = {"strategy": "latency", "max_replicas": 2, "providers": [
        prov("a", "ra", 10, cap=1), prov("b", "rb", 20, cap=1), prov("c", "rc", 30, cap=10)]}
    reqs = [{"id": "1", "user_region": "x"}, {"id": "2", "user_region": "x"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, reqs, j, td=td)
        assert out == [["a", "b"], ["b", "c"]]
        assert code == 0


def test_spillover_when_primary_exhausted(router_bin):
    cfg = {"strategy": "latency", "max_replicas": 1, "providers": [
        prov("a", "r", 10, cap=1), prov("b", "r", 50, cap=10)]}
    reqs = [{"id": str(i), "user_region": "r"} for i in range(3)]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, reqs, j, td=td)
        assert out == [["a"], ["b"], ["b"]]
        assert code == 0


def test_degraded_when_fewer_than_max_replicas(router_bin):
    cfg = {"strategy": "latency", "max_replicas": 3, "providers": [
        prov("a", "us", 10), prov("b", "eu", 10)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, [{"id": "r", "user_region": "us"}], j, td=td)
        assert out == [["a", "b"]] and code == 1
        with open(j, "rb") as f:
            recs, _ = decode_journal(f.read())
        assert recs == [(0, "r", ["a", "b"])]


def test_all_ineligible_empty_chain_exit1(router_bin):
    cfg = {"strategy": "latency", "max_replicas": 1, "providers": [
        prov("slow", "us", 200)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, [{"id": "r", "user_region": "us", "sla_ms": 100}], j, td=td)
        assert out == [[]] and code == 1
        with open(j, "rb") as f:
            recs, _ = decode_journal(f.read())
        assert recs == [(0, "r", [])]


def test_status_down_and_capacity_zero_filtered(router_bin):
    cfg = {"strategy": "latency", "max_replicas": 1, "providers": [
        prov("down", "us", 5, status="down"), prov("zero", "us", 6, cap=0), prov("up", "us", 50)]}
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        assert run(router_bin, cfg, [{"id": "r", "user_region": "us"}], j, td=td)[1] == [["up"]]


def test_blank_lines_ignored(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("p", "us", 10)]}
    with tempfile.TemporaryDirectory() as td:
        cp, rp, j = (os.path.join(td, x) for x in ("c.json", "r.jsonl", "j.bin"))
        with open(cp, "w") as f:
            json.dump(cfg, f)
        with open(rp, "w") as f:
            f.write('{"id":"1","user_region":"us"}\n\n   \n{"id":"2","user_region":"us"}\n')
        proc = subprocess.run([router_bin, "--config", cp, "--requests", rp, "--journal", j],
                              capture_output=True, text=True, timeout=60)
        assert proc.stdout.strip().splitlines() == ['["p"]', '["p"]']


# ------------------------------ recovery ----------------------------------
CFG_CAP = {"strategy": "latency", "max_replicas": 1, "providers": [
    prov("a", "r", 10, cap=1), prov("b", "r", 50, cap=10)]}
REQS_CAP = [{"id": "1", "user_region": "r"}, {"id": "2", "user_region": "r"}]


def test_resume_idempotent_complete(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("p", "us", 10)]}
    reqs = [{"id": "1", "user_region": "us"}, {"id": "2", "user_region": "us"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        c1, o1, _ = run(router_bin, cfg, reqs, j, td=td)
        with open(j, "rb") as f:
            b1 = f.read()
        c2, o2, _ = run(router_bin, cfg, reqs, j, resume=True, td=td)
        with open(j, "rb") as f:
            b2 = f.read()
        assert (c1, o1, b1) == (c2, o2, b2)


def test_resume_reconstructs_capacity(router_bin):
    # The crux: resume must rebuild remaining capacity by replaying recorded PRIMARY
    # consumption. After truncating to request 1's record, request 2 must go to 'b'.
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        _, out_full, _ = run(router_bin, CFG_CAP, REQS_CAP, j, td=td)
        assert out_full == [["a"], ["b"]]
        with open(j, "rb") as f:
            full = f.read()
        _, ends = decode_journal(full)
        with open(j, "wb") as f:
            f.write(full[:ends[0]])
        code, out, _ = run(router_bin, CFG_CAP, REQS_CAP, j, resume=True, td=td)
        assert code == 0 and out == [["a"], ["b"]]  # naive resume gives [["a"],["a"]]
        with open(j, "rb") as f:
            assert f.read() == full


def test_resume_torn_tail(router_bin):
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        _, out_full, _ = run(router_bin, CFG_CAP, REQS_CAP, j, td=td)
        with open(j, "rb") as f:
            full = f.read()
        _, ends = decode_journal(full)
        with open(j, "wb") as f:
            f.write(full[:ends[0]] + full[ends[0]:ends[1]][:4])  # partial 2nd record
        code, out, _ = run(router_bin, CFG_CAP, REQS_CAP, j, resume=True, td=td)
        assert code == 0 and out == out_full
        with open(j, "rb") as f:
            assert f.read() == full


def test_resume_bad_crc_tail_recovered(router_bin):
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        _, out_full, _ = run(router_bin, CFG_CAP, REQS_CAP, j, td=td)
        with open(j, "rb") as f:
            full = f.read()
        _, ends = decode_journal(full)
        corrupt = bytearray(full)
        corrupt[ends[1] - 1] ^= 0xFF
        with open(j, "wb") as f:
            f.write(bytes(corrupt))
        code, out, _ = run(router_bin, CFG_CAP, REQS_CAP, j, resume=True, td=td)
        assert code == 0 and out == out_full
        with open(j, "rb") as f:
            assert f.read() == full


def test_corrupt_midfile_bad_crc_exit3(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("p", "us", 10)]}
    reqs = [{"id": "1", "user_region": "us"}, {"id": "2", "user_region": "us"}, {"id": "3", "user_region": "us"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        r1 = bytearray(encode_record(1, "2", ["p"]))
        r1[-1] ^= 0xFF
        with open(j, "wb") as f:
            f.write(HEADER + encode_record(0, "1", ["p"]) + bytes(r1) + encode_record(2, "3", ["p"]))
        assert run(router_bin, cfg, reqs, j, resume=True, td=td)[0] == 3


def test_corrupt_header_seqgap_idmismatch_exit3(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("p", "us", 10)]}
    reqs = [{"id": "1", "user_region": "us"}, {"id": "2", "user_region": "us"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        with open(j, "wb") as f:
            f.write(b"XXXXXXXX" + encode_record(0, "1", ["p"]))
        assert run(router_bin, cfg, reqs, j, resume=True, td=td)[0] == 3
        with open(j, "wb") as f:
            f.write(HEADER + encode_record(0, "1", ["p"]) + encode_record(2, "2", ["p"]))  # seq gap
        assert run(router_bin, cfg, reqs, j, resume=True, td=td)[0] == 3
        with open(j, "wb") as f:
            f.write(HEADER + encode_record(0, "WRONG", ["p"]))  # id mismatch
        assert run(router_bin, cfg, reqs, j, resume=True, td=td)[0] == 3


def test_journal_exists_without_resume_exit2(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("p", "us", 10)]}
    reqs = [{"id": "1", "user_region": "us"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        run(router_bin, cfg, reqs, j, td=td)
        with open(j, "rb") as f:
            before = f.read()
        assert run(router_bin, cfg, reqs, j, resume=False, td=td)[0] == 2
        with open(j, "rb") as f:
            assert f.read() == before


def test_unicode_id_byte_length(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("aws-us", "us-east", 40)]}
    reqs = [{"id": "café-日本", "user_region": "us-east"}]
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        code, out, _ = run(router_bin, cfg, reqs, j, td=td)
        assert code == 0 and out == [["aws-us"]]
        with open(j, "rb") as f:
            assert f.read() == HEADER + encode_record(0, "café-日本", ["aws-us"])


# --------------------------- validation exit 2 ----------------------------
def test_invalid_config_exit2_variants(router_bin):
    reqs = [{"id": "r", "user_region": "us"}]
    def base():
        return {"strategy": "latency", "max_replicas": 1, "providers": [prov("p", "us", 10)]}
    bad = []
    b = base(); b["strategy"] = "bogus"; bad.append(b)
    b = base(); del b["providers"]; bad.append(b)
    b = base(); b["max_replicas"] = 0; bad.append(b)
    b = base(); b["providers"][0]["id"] = ""; bad.append(b)
    b = base(); b["providers"].append(dict(b["providers"][0])); bad.append(b)
    b = base(); b["providers"][0]["latency_ms"] = -1; bad.append(b)
    b = base(); b["providers"][0]["latency_ms"] = 1.5; bad.append(b)
    b = base(); b["providers"][0]["error_rate"] = 1.5; bad.append(b)
    b = base(); b["providers"][0]["capacity_rps"] = -3; bad.append(b)
    b = base(); b["providers"][0]["status"] = "maybe"; bad.append(b)
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.bin")
        for cfg in bad:
            code, _, _ = run(router_bin, cfg, reqs, j, td=td)
            assert code == 2, f"expected 2 for {cfg}"
            assert not os.path.exists(j)


def test_invalid_requests_line_exit2(router_bin):
    cfg = {"strategy": "latency", "providers": [prov("p", "us", 10)]}
    with tempfile.TemporaryDirectory() as td:
        cp, rp, j = (os.path.join(td, x) for x in ("c.json", "r.jsonl", "j.bin"))
        with open(cp, "w") as f:
            json.dump(cfg, f)
        with open(rp, "w") as f:
            f.write('{"id":"1","user_region":"us"}\nnot json\n')
        proc = subprocess.run([router_bin, "--config", cp, "--requests", rp, "--journal", j],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2 and proc.stdout.strip() == ""
