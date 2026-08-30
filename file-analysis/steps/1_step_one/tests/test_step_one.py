"""
Tests for Step1 file relevance analyzer
"""

import json
import os
import subprocess
import tempfile
import shutil
import stat
import time

BIN = "/app/file-analyzer"
APP = "/app"


def run(args, cwd=None, timeout=10):
    return subprocess.run(
        args, cwd=cwd or APP, capture_output=True, text=True, timeout=timeout
    )


def ensure_binary():
    if not os.path.isfile(BIN):
        # try to build
        subprocess.run(
            ["go", "build", "-o", "file-analyzer", "."], cwd=APP, capture_output=True
        )


# --- help & arg parsing ---


def test_help_no_args():
    ensure_binary()
    r = run([BIN])
    assert r.returncode == 0, (
        f"expected 0 for no args help, got {r.returncode} stderr={r.stderr}"
    )
    out = (r.stdout + r.stderr).lower()
    assert "dir" in out and "output" in out and "help" in out, (
        f"help should contain dir, output, help, got {r.stdout}"
    )


def test_help_flag():
    ensure_binary()
    for flag in ["--help", "-h", "help"]:
        r = run([BIN, flag])
        assert r.returncode == 0, f"{flag} should exit 0, got {r.returncode}"
        out = (r.stdout + r.stderr).lower()
        assert "dir" in out and "output" in out, f"help with {flag} missing keywords"


def test_unknown_flag():
    ensure_binary()
    r = run([BIN, "--unknown"])
    assert r.returncode == 2, f"unknown flag should exit 2, got {r.returncode}"
    # no stdout
    assert r.stdout.strip() == "", (
        f"unknown flag should produce no stdout, got {r.stdout}"
    )


def test_missing_flags():
    ensure_binary()
    r = run([BIN, "--dir", "/tmp"])
    assert r.returncode == 2, "missing output should exit 2"
    r = run([BIN, "--output", "/tmp/out.json"])
    assert r.returncode == 2, "missing dir should exit 2"


def test_dir_not_exist():
    ensure_binary()
    r = run([BIN, "--dir", "/nonexistent_xyz_123", "--output", "/tmp/out.json"])
    assert r.returncode == 2


def test_dir_is_file():
    ensure_binary()
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(b"hi")
        tf_path = tf.name
    try:
        r = run([BIN, "--dir", tf_path, "--output", "/tmp/out.json"])
        assert r.returncode == 2
    finally:
        os.unlink(tf_path)


# --- classification helpers ---


def create_file(dir_path, name, content):
    full = os.path.join(dir_path, name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


def run_analyzer(input_dir, output_path):
    ensure_binary()
    r = run([BIN, "--dir", input_dir, "--output", output_path])
    assert r.returncode == 0, (
        f"analyzer failed: stdout={r.stdout} stderr={r.stderr} code={r.returncode}"
    )
    assert os.path.isfile(output_path), "output file not created"
    with open(output_path, "r") as f:
        data = json.load(f)
    return data


def test_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data == [], f"empty dir should produce [] got {data}"


def test_empty_file_non_essential():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "empty.txt", "")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert len(data) == 1
        assert data[0]["category"] == "non-essential"
        assert data[0]["file"].endswith("empty.txt")


def test_whitespace_only_non_essential():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "ws.txt", "   \n\t  \n")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "non-essential"


def test_business_critical_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(
            tmpdir,
            "biz.txt",
            "This document contains confidential financial revenue forecast for Q4 merger.",
        )
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "business-critical", (
            f"expected business-critical got {data[0]}"
        )


def test_business_multiple_keywords():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(
            tmpdir,
            "biz2.txt",
            "Our proprietary trade secret and intellectual property contract for acquisition",
        )
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "business-critical"


def test_pii_email():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "email.txt", "Contact us at john.doe@example.com for info")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "pii", f"email should be pii got {data}"


def test_pii_ssn():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "ssn.txt", "SSN is 123-45-6789 please keep secret")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "pii"


def test_pii_phone():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "phone.txt", "Call me at 123-456-7890")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "pii"


def test_pii_credit_card():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "cc.txt", "Card number 4111-1111-1111-1111 is my card")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "pii"


def test_pii_priority_over_business():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(
            tmpdir, "mixed.txt", "confidential financial data. Contact john@example.com"
        )
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "pii", (
            f"mixed should be pii due to priority, got {data[0]['category']}"
        )


def test_non_essential_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(
            tmpdir, "notes.txt", "Just some random text about weather and sports"
        )
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "non-essential"


def test_sorted_output():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "zebra.txt", "hello")
        create_file(tmpdir, "apple.txt", "hello")
        create_file(tmpdir, "middle.txt", "hello")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        files = [d["file"] for d in data]
        assert files == sorted(files), f"output not sorted: {files}"


def test_recursive_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "a.txt", "hello")
        create_file(tmpdir, "subdir/b.txt", "confidential")
        create_file(tmpdir, "subdir/nested/c.txt", "123-45-6789")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert len(data) == 3, f"expected 3 files got {len(data)}"
        cats = {os.path.basename(d["file"]): d["category"] for d in data}
        assert cats["a.txt"] == "non-essential"
        assert cats["b.txt"] == "business-critical"
        assert cats["c.txt"] == "pii"


def test_atomic_write_and_valid_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "a.txt", "confidential")
        out = os.path.join(tmpdir, "nested/dir/out.json")
        data = run_analyzer(tmpdir, out)
        assert os.path.isfile(out)
        # Check no temp files left in output dir
        out_dir = os.path.dirname(out)
        for fname in os.listdir(out_dir):
            assert "tmp" not in fname.lower() or fname == "out.json", (
                f"temp file residue {fname}"
            )
        # Valid JSON already checked


def test_symlink_ignored():
    with tempfile.TemporaryDirectory() as tmpdir:
        real_file = create_file(tmpdir, "real.txt", "confidential")
        link_path = os.path.join(tmpdir, "link.txt")
        try:
            os.symlink(real_file, link_path)
        except OSError:
            # Symlink not supported, skip test
            return
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        # Should have only real file, not symlink (or if counts symlink as file, we allow but ensure no crash)
        # Our spec says symlink ignored, so expect 1
        assert len(data) == 1, (
            f"symlink should be ignored, got {len(data)} files: {data}"
        )
        assert data[0]["file"].endswith("real.txt")


def test_stdlib_only():
    # Check go.mod has no external requires beyond stdlib
    go_mod_path = os.path.join(APP, "go.mod")
    if not os.path.isfile(go_mod_path):
        return
    with open(go_mod_path, "r") as f:
        content = f.read()
    # Should not contain require with github.com or external
    # Allow only indirect? Strict check: no github.com
    assert "github.com" not in content, (
        f"go.mod should have no external deps, got {content}"
    )
    # Check imports via go list
    r = run(["go", "list", "-f", '{{join .Imports " "}}', "."], cwd=APP)
    assert r.returncode == 0
    imports = r.stdout
    # Should not contain dot
    for imp in imports.split():
        assert "." not in imp, f"stdlib only, found dotted import {imp} in {imports}"


def test_large_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        big_content = "A" * (1024 * 1024) + " confidential revenue "
        create_file(tmpdir, "big.txt", big_content)
        out = os.path.join(tmpdir, "out.json")
        start = time.time()
        data = run_analyzer(tmpdir, out)
        elapsed = time.time() - start
        assert data[0]["category"] == "business-critical"
        assert elapsed < 5, f"large file should be processed <5s, took {elapsed}"


def test_output_format():
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "a.txt", "hello")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert isinstance(data, list)
        for entry in data:
            assert "file" in entry
            assert "category" in entry
            assert entry["category"] in ("business-critical", "pii", "non-essential")


def test_tmp_extension_still_business_in_step1():
    # Step1 should NOT have extension override, so .tmp with confidential should be business-critical
    with tempfile.TemporaryDirectory() as tmpdir:
        create_file(tmpdir, "notes.tmp", "This is confidential financial data")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "business-critical", (
            f"Step1: .tmp with confidential should be business-critical, got {data[0]['category']}"
        )
