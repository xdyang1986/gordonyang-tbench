"""
Step1 tests – observable behavior, randomized differential
"""

import json
import os
import subprocess
import tempfile
import random
import re

BIN = "/app/file-analyzer"
APP = "/app"


def run(args, timeout=10):
    return subprocess.run(
        args, cwd=APP, capture_output=True, text=True, timeout=timeout
    )


def ensure_binary():
    if not os.path.isfile(BIN):
        subprocess.run(
            ["go", "build", "-o", "file-analyzer", "."], cwd=APP, capture_output=True
        )


KEYWORDS = [
    "confidential",
    "proprietary",
    "trade secret",
    "financial",
    "revenue",
    "budget",
    "forecast",
    "strategic",
    "merger",
    "acquisition",
    "contract",
    "nda",
    "intellectual property",
    "earnings",
    "profit",
    "balance sheet",
    "board meeting",
    "shareholder",
]
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE1 = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
PHONE_RE2 = re.compile(r"\(\d{3}\)\s*\d{3}[-.]\d{4}")
CC_RE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")
LOG_LINE_RE = re.compile(
    r"(?i)(^\d{4}-\d{2}-\d{2}|^\[?\d{2}:\d{2}:\d{2}|\b(INFO|DEBUG|WARN|ERROR|TRACE)\b)"
)


def reference_classify(dir_path):
    results = []
    for root, dirs, files in os.walk(dir_path):
        for name in files:
            fpath = os.path.join(root, name)
            if os.path.islink(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except:
                results.append((fpath, "non-essential"))
                continue
            if not content.strip():
                results.append((fpath, "non-essential"))
                continue
            is_pii = bool(
                SSN_RE.search(content)
                or EMAIL_RE.search(content)
                or PHONE_RE1.search(content)
                or PHONE_RE2.search(content)
                or CC_RE.search(content)
            )
            if is_pii:
                results.append((fpath, "pii"))
                continue
            if "\x00" in content:
                results.append((fpath, "non-essential"))
                continue
            # log heuristic: at least 3 non-empty lines, >50% match per spec
            lines = content.split("\n")
            non_empty = [l for l in lines if l.strip()]
            if len(non_empty) >= 3:
                matched = sum(1 for l in non_empty if LOG_LINE_RE.search(l))
                if matched / len(non_empty) > 0.5:
                    results.append((fpath, "non-essential"))
                    continue
            low = content.lower()
            is_biz = any(k in low for k in KEYWORDS)
            if is_biz:
                results.append((fpath, "business-critical"))
            else:
                results.append((fpath, "non-essential"))
    results.sort(key=lambda x: x[0])
    return results


def run_analyzer(input_dir, output_path):
    ensure_binary()
    r = run([BIN, "--dir", input_dir, "--output", output_path])
    assert r.returncode == 0, f"analyzer failed: {r.stdout} {r.stderr}"
    with open(output_path) as f:
        data = json.load(f)
    return data


# --- basic CLI tests ---


def test_help_and_exit_codes():
    ensure_binary()
    r = run([BIN])
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "dir" in out and "output" in out and "help" in out
    for flag in ["--help", "-h", "help"]:
        r = run([BIN, flag])
        assert r.returncode == 0
        out = (r.stdout + r.stderr).lower()
        assert "dir" in out
    r = run([BIN, "--unknown_xyz"])
    assert r.returncode == 2
    assert r.stdout.strip() == ""
    r = run([BIN, "--dir", "/tmp"])
    assert r.returncode == 2
    r = run([BIN, "--output", "/tmp/out.json"])
    assert r.returncode == 2
    r = run([BIN, "--dir", "/no_such_dir_xyz", "--output", "/tmp/out.json"])
    assert r.returncode == 2


def test_empty_and_sort_and_recursive_and_symlink():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data == []
        os.makedirs(os.path.join(tmpdir, "sub"))
        with open(os.path.join(tmpdir, "z.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("confidential data")
        with open(os.path.join(tmpdir, "sub", "b.txt"), "w") as f:
            f.write("email john@example.com")
        try:
            os.symlink(os.path.join(tmpdir, "a.txt"), os.path.join(tmpdir, "link.txt"))
        except:
            pass
        out2 = os.path.join(tmpdir, "out2.json")
        data2 = run_analyzer(tmpdir, out2)
        files = [d["file"] for d in data2]
        assert files == sorted(files)
    with tempfile.TemporaryDirectory() as tmpdir:
        real = os.path.join(tmpdir, "real.txt")
        with open(real, "w") as f:
            f.write("confidential")
        link = os.path.join(tmpdir, "link.txt")
        try:
            os.symlink(real, link)
        except:
            pass
        else:
            out_outside = os.path.join(tempfile.gettempdir(), "out_sym.json")
            data = run_analyzer(tmpdir, out_outside)
            assert len([d for d in data if "real.txt" in d["file"]]) == 1
            assert len([d for d in data if "link.txt" in d["file"]]) == 0


def test_atomic_and_json_shape():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("confidential")
        out_dir = os.path.join(tmpdir, "nested", "outdir")
        out = os.path.join(out_dir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert os.path.isfile(out)
        for fname in os.listdir(out_dir):
            assert "tmp" not in fname.lower() or fname == "out.json"
        for entry in data:
            assert "file" in entry and "category" in entry
            assert entry["category"] in ("business-critical", "pii", "non-essential")


def test_log_content_heuristic_in_step1():
    with tempfile.TemporaryDirectory() as tmpdir:
        content = "\n".join(
            [f"2024-01-01 INFO budget processing {i}" for i in range(10)]
        )
        with open(os.path.join(tmpdir, "loglike.txt"), "w") as f:
            f.write(content)
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "non-essential", (
            f"log-like with budget should be non-essential in step1, got {data[0]}"
        )


def test_sort_trap_prefix_dash():
    # Walk-order trap: '-' (0x2D) sorts before '/' (0x2F), so Walk visits d before d-1 but sorted order is d-1 before d
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "a"))
        os.makedirs(os.path.join(tmpdir, "a-b"))
        with open(os.path.join(tmpdir, "a", "b.txt"), "w") as f:
            f.write("confidential")
        with open(os.path.join(tmpdir, "a-b", "c.txt"), "w") as f:
            f.write("hello")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        files = [d["file"] for d in data]
        assert files == sorted(files), f"must be sorted lexicographically, got {files}"
        assert files[0].endswith("a-b/c.txt") and files[1].endswith("a/b.txt"), (
            f"sort trap failed: expected a-b/c.txt before a/b.txt, got {files}"
        )


def test_randomized_differential_small():
    # Flat files, includes phone with context, and at least 3 lines guard exercised
    random.seed(42)
    with tempfile.TemporaryDirectory() as tmpdir:
        words_pool = [
            "hello",
            "world",
            "confidential",
            "financial",
            "revenue",
            "budget",
            "random",
            "data",
            "john@example.com",
            "123-45-6789",
            "4111-1111-1111-1111",
            "Call 123-456-7890",
            "2024-01-01 INFO start",
        ]
        for i in range(30):
            content = " ".join(random.choices(words_pool, k=random.randint(0, 10)))
            with open(os.path.join(tmpdir, f"file_{i:02d}.txt"), "w") as f:
                f.write(content)
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        ref = reference_classify(tmpdir)
        ref_filtered = [(p, c) for p, c in ref if not p.endswith("out.json")]
        analyzer_map = {
            d["file"]: d["category"] for d in data if not d["file"].endswith("out.json")
        }
        assert len(ref_filtered) == len(analyzer_map), (
            f"counts differ ref={len(ref_filtered)} analyzer={len(analyzer_map)}"
        )
        for fpath, cat in ref_filtered:
            assert fpath in analyzer_map, f"missing {fpath}"
            assert analyzer_map[fpath] == cat, (
                f"mismatch for {fpath}: ref {cat} vs analyzer {analyzer_map[fpath]}"
            )


def test_randomized_differential_mixed_extensions():
    random.seed(123)
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(20):
            ext = random.choice([".txt", ".log", ".dat"])
            if i % 3 == 0:
                content = "confidential proprietary"
            elif i % 3 == 1:
                content = f"user email test_{i}@example.com"
            else:
                content = "just notes"
            with open(os.path.join(tmpdir, f"doc_{i}{ext}"), "w") as f:
                f.write(content)
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        ref = reference_classify(tmpdir)
        ref_filtered = [(p, c) for p, c in ref if not p.endswith("out.json")]
        analyzer_map = {
            d["file"]: d["category"] for d in data if not d["file"].endswith("out.json")
        }
        for fpath, cat in ref_filtered:
            assert analyzer_map.get(fpath) == cat, (
                f"diff {fpath} ref {cat} got {analyzer_map.get(fpath)}"
            )


def test_stdlib_only():
    go_mod = os.path.join(APP, "go.mod")
    if os.path.isfile(go_mod):
        with open(go_mod) as f:
            assert "github.com" not in f.read()
    r = run(["go", "list", "-f", '{{join .Imports " "}}', "."], timeout=5)
    if r.returncode == 0:
        for imp in r.stdout.split():
            assert "." not in imp, f"dotted import {imp}"


# --- New bounded-memory streaming tests (must be new functions with own fixtures) ---


def test_bounded_memory_large_file():
    # 512 MB file with SSN near end, RLIMIT_DATA 256 MiB - only streaming with overlap passes
    import resource

    with tempfile.TemporaryDirectory() as tmpdir:
        large_path = os.path.join(tmpdir, "large.dat")
        size = 512 * 1024 * 1024
        ssn = "123-45-6789"
        # Write in 1MiB chunks to avoid large memory in test itself
        chunk = b"A" * (1024 * 1024)
        remaining = size - len(ssn) - 10
        written = 0
        with open(large_path, "wb") as f:
            while written < remaining:
                to_write = min(len(chunk), remaining - written)
                f.write(chunk[:to_write])
                written += to_write
            f.write(b"\n")
            f.write(ssn.encode())
            f.write(b"\n")
        out = os.path.join(tmpdir, "out.json")

        def limit():
            # Use RLIMIT_DATA not RLIMIT_AS for Go (Go reserves large virtual arena)
            resource.setrlimit(
                resource.RLIMIT_DATA, (256 * 1024 * 1024, 256 * 1024 * 1024)
            )

        ensure_binary()
        r = subprocess.run(
            [BIN, "--dir", tmpdir, "--output", out],
            preexec_fn=limit,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, (
            f"large file must be processed with 256MiB DATA limit, rc={r.returncode} stderr={r.stderr}"
        )
        with open(out) as f:
            data = json.load(f)
        entry = [d for d in data if "large.dat" in d["file"]]
        assert len(entry) == 1
        assert entry[0]["category"] == "pii", (
            f"large file with SSN near end should be pii, got {entry[0]}"
        )


def test_no_newline_file():
    # 1 MB single line containing SSN - kills bufio.Scanner default token 64k, uses non-word padding for word boundaries
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "nonl.txt")
        content = "!" * (512 * 1024) + " 123-45-6789 " + "!" * (512 * 1024)
        with open(path, "w") as f:
            f.write(content)
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        entry = [d for d in data if "nonl.txt" in d["file"]]
        assert len(entry) == 1
        assert entry[0]["category"] == "pii", (
            f"1MB no-newline file with SSN should be pii, got {entry[0]}"
        )


def test_pattern_straddles_buffer_boundary():
    # Sweep offset 2^k -5 for k=12..20, SSN at boundary - requires overlap carry, use non-word padding for boundaries
    ssn = "123-45-6789"
    for k in range(12, 21):  # 4096 to ~1M
        offset = (1 << k) - 5
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "boundary.txt")
            with open(path, "wb") as f:
                f.write(b"!" * offset)
                f.write(b" ")
                f.write(ssn.encode())
                f.write(b" ")
                f.write(b"!" * 100)
            out = os.path.join(tmpdir, "out.json")
            data = run_analyzer(tmpdir, out)
            entry = [d for d in data if "boundary.txt" in d["file"]]
            assert len(entry) == 1
            assert entry[0]["category"] == "pii", (
                f"SSN at offset {offset} (2^{k}-5) should be pii, got {entry[0]['category']}"
            )
