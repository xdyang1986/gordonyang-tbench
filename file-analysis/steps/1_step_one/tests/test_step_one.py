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
            # log-content heuristic moved to step1: predominantly log lines -> non-essential even with business words
            lines = content.split("\n")
            if len(lines) >= 3:
                non_empty = [l for l in lines if l.strip()]
                matched = sum(1 for l in non_empty if LOG_LINE_RE.search(l))
                if non_empty and matched / len(non_empty) > 0.5:
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
    # This discriminator moved from step2 to step1 to increase difficulty
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


def test_randomized_differential_small():
    # Fixed phone ambiguity: use Call 123-456-7890 with context so conservative detectors agree
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
