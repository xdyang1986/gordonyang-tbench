"""
Step2 tests – non-obvious semantics + randomized differential
"""

import json
import os
import subprocess
import tempfile
import random
import re
import time
import pathlib

BIN = "/app/file-analyzer"
APP = "/app"


def run(args, timeout=15):
    return subprocess.run(
        args, cwd=APP, capture_output=True, text=True, timeout=timeout
    )


def ensure_binary():
    if not os.path.isfile(BIN):
        subprocess.run(
            ["go", "build", "-o", "file-analyzer", "."], cwd=APP, capture_output=True
        )


# Reference implementation mirroring Go step2 new semantics (including non-obvious rules)
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
CC_STRICT_RE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")
CC_RE = re.compile(r"\b(?:\d[-\s]*){13,19}\b")
DOLLAR_RE = re.compile(r"\$\s*\d")
PERCENT_RE = re.compile(r"\b\d+(\.\d+)?\s*%")
LOG_LINE_RE = re.compile(
    r"(?i)(^\d{4}-\d{2}-\d{2}|^\[?\d{2}:\d{2}:\d{2}|\b(INFO|DEBUG|WARN|ERROR|TRACE)\b)"
)


def is_valid_ssn(s):
    parts = s.split("-")
    if len(parts) != 3:
        return False
    try:
        area = int(parts[0])
        group = int(parts[1])
        serial = int(parts[2])
    except:
        return False
    if area == 0 or area == 666 or area >= 900 or group == 0 or serial == 0:
        return False
    return True


def luhn(s):
    digits = re.sub(r"\D", "", s)
    if not digits.isdigit():
        return False
    total = 0
    alt = False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def is_all_same(digits):
    return len(set(digits)) == 1 if digits else False


def reference_classify(dir_path, relative=False):
    file_paths = []
    for root, dirs, files in os.walk(dir_path):
        for name in files:
            fpath = os.path.join(root, name)
            if os.path.islink(fpath):
                continue
            file_paths.append(fpath)
    # log heavy count by .log extension
    log_count = sum(1 for p in file_paths if pathlib.Path(p).suffix.lower() == ".log")
    total = len(file_paths)
    log_heavy = total > 0 and (log_count / total) > 0.7

    results = []
    for path in file_paths:
        ext = pathlib.Path(path).suffix.lower()
        out_file = os.path.relpath(path, dir_path) if relative else path
        if ext == ".bak":
            results.append((out_file, "non-essential"))
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except:
            results.append((out_file, "non-essential"))
            continue
        if not content.strip():
            results.append((out_file, "non-essential"))
            continue

        # PII with validation
        is_pii = False
        for m in SSN_RE.findall(content):
            if is_valid_ssn(m):
                is_pii = True
                break
        if not is_pii:
            for m in EMAIL_RE.findall(content):
                if ".." in m:
                    continue
                if "@" not in m or "." not in m.split("@")[1]:
                    continue
                is_pii = True
                break
        if not is_pii:
            for mm in PHONE_RE1.findall(content):
                digits = re.sub(r"\D", "", mm)
                if len(digits) >= 10 and not is_all_same(digits):
                    is_pii = True
                    break
        if not is_pii:
            for mm in PHONE_RE2.findall(content):
                digits = re.sub(r"\D", "", mm)
                if len(digits) >= 10 and not is_all_same(digits):
                    is_pii = True
                    break
        if not is_pii:
            candidates = CC_STRICT_RE.findall(content) or CC_RE.findall(content)
            for cand in candidates:
                if PHONE_RE1.search(cand) or PHONE_RE2.search(cand):
                    continue
                digits = re.sub(r"\D", "", cand)
                if (
                    len(digits) < 13
                    or len(digits) > 19
                    or is_all_same(digits)
                    or not luhn(digits)
                ):
                    continue
                is_pii = True
                break

        if is_pii:
            results.append((out_file, "pii"))
            continue

        if "\x00" in content:
            results.append((out_file, "non-essential"))
            continue

        low = content.lower()
        distinct = 0
        total_occ = 0
        seen = set()
        for kw in KEYWORDS:
            c = low.count(kw)
            if c > 0:
                if kw not in seen:
                    distinct += 1
                    seen.add(kw)
                total_occ += c
        has_fin = bool(DOLLAR_RE.search(content) or PERCENT_RE.search(content))
        is_biz = distinct >= 2 or total_occ >= 2 or (distinct >= 1 and has_fin)

        # extension override .log .tmp etc except .bak
        if ext in (".log", ".tmp", ".cache", ".old", ".swp", ".temp"):
            if is_biz:
                results.append((out_file, "non-essential"))
                continue

        # log content predominantly
        lines = content.split("\n")
        if len(lines) >= 3:
            non_empty = [l for l in lines if l.strip()]
            matched = sum(1 for l in non_empty if LOG_LINE_RE.search(l))
            if non_empty and matched / len(non_empty) > 0.5:
                # strong business exception
                if is_biz and distinct >= 2 and has_fin:
                    results.append((out_file, "business-critical"))
                else:
                    results.append((out_file, "non-essential"))
                continue

        if log_heavy and ext != ".log" and is_biz:
            results.append((out_file, "non-essential"))
            continue

        if is_biz:
            results.append((out_file, "business-critical"))
        else:
            results.append((out_file, "non-essential"))

    results.sort(key=lambda x: x[0])
    return results


def run_analyzer(input_dir, output_path, workers=None, relative=False):
    ensure_binary()
    cmd = [BIN, "--dir", input_dir, "--output", output_path]
    if workers is not None:
        cmd.extend(["--workers", str(workers)])
    if relative:
        cmd.append("--relative")
    r = run(cmd, timeout=20)
    assert r.returncode == 0, f"analyzer failed {r.stdout} {r.stderr}"
    with open(output_path) as f:
        data = json.load(f)
    return data


def test_help_workers_relative():
    ensure_binary()
    r = run([BIN, "--help"])
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "workers" in out and "relative" in out and "dir" in out
    r = run([BIN])
    assert r.returncode == 0


def test_workers_and_relative_flags():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("confidential financial revenue")
        out = os.path.join(tmpdir, "out.json")
        # workers 0 invalid
        r = run([BIN, "--dir", tmpdir, "--output", out, "--workers", "0"])
        assert r.returncode == 2
        # valid workers 1 vs 4 same category when not log heavy
        out1 = os.path.join(tmpdir, "o1.json")
        out2 = os.path.join(tmpdir, "o2.json")
        # put outputs outside scanned dir to avoid counting them
        out_dir = os.path.join(tmpdir, "outs")
        os.makedirs(out_dir, exist_ok=True)
        out1 = os.path.join(out_dir, "o1.json")
        out2 = os.path.join(out_dir, "o2.json")
        d1 = run_analyzer(tmpdir, out1, workers=1, relative=False)
        d2 = run_analyzer(tmpdir, out2, workers=4, relative=False)
        m1 = {x["file"]: x["category"] for x in d1 if "outs" not in x["file"]}
        m2 = {x["file"]: x["category"] for x in d2 if "outs" not in x["file"]}
        assert m1 == m2


def test_relative_flag_changes_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        sub = os.path.join(tmpdir, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "a.txt"), "w") as f:
            f.write("confidential financial")
        out_abs = os.path.join(tmpdir, "out_abs.json")
        out_rel = os.path.join(tmpdir, "out_rel.json")
        data_abs = run_analyzer(tmpdir, out_abs, relative=False)
        data_rel = run_analyzer(tmpdir, out_rel, relative=True)
        # abs should contain absolute paths
        abs_files = [d["file"] for d in data_abs if not d["file"].endswith(".json")]
        rel_files = [d["file"] for d in data_rel if not d["file"].endswith(".json")]
        assert any(os.path.isabs(p) for p in abs_files), (
            f"abs expected absolute {abs_files}"
        )
        assert all(not os.path.isabs(p) for p in rel_files), (
            f"rel expected relative {rel_files}"
        )
        # relative sorted
        assert rel_files == sorted(rel_files)
        # relative file should be sub/a.txt
        assert "sub/a.txt" in rel_files or "sub\\a.txt" in rel_files


def test_bak_precedence_inversion():
    # .bak files are always non-essential even with valid PII – non-obvious
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "pii.bak"), "w") as f:
            f.write("SSN 123-45-6789 email john@example.com")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        entry = [d for d in data if "pii.bak" in d["file"]]
        assert len(entry) == 1
        assert entry[0]["category"] == "non-essential", (
            f".bak with PII should be non-essential due to inversion, got {entry[0]}"
        )


def test_log_heavy_sibling_downgrade():
    with tempfile.TemporaryDirectory() as tmpdir:
        # 8 logs + 2 biz files = 80% logs -> log heavy
        for i in range(8):
            with open(os.path.join(tmpdir, f"f{i}.log"), "w") as f:
                f.write(f"log line {i}")
        with open(os.path.join(tmpdir, "biz.txt"), "w") as f:
            f.write("confidential revenue forecast strategic merger")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        biz = [d for d in data if "biz.txt" in d["file"]]
        assert len(biz) == 1
        assert biz[0]["category"] == "non-essential", (
            f"biz should be downgraded due to >70% logs, got {biz[0]}"
        )
        # not log heavy when 50%
        with tempfile.TemporaryDirectory() as tmpdir2:
            for i in range(2):
                with open(os.path.join(tmpdir2, f"f{i}.log"), "w") as f:
                    f.write("log")
            with open(os.path.join(tmpdir2, "biz.txt"), "w") as f:
                f.write("confidential revenue forecast strategic merger")
            out2 = os.path.join(tmpdir2, "out.json")
            data2 = run_analyzer(tmpdir2, out2)
            biz2 = [d for d in data2 if "biz.txt" in d["file"]]
            assert biz2[0]["category"] == "business-critical"


def test_structurally_impossible_ssn_and_luhn():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "bad_ssn.txt"), "w") as f:
            f.write("SSN 000-12-3456")
        with open(os.path.join(tmpdir, "good_ssn.txt"), "w") as f:
            f.write("SSN 123-45-6789")
        with open(os.path.join(tmpdir, "bad_cc.txt"), "w") as f:
            f.write("card 4111-1111-1111-1112")
        with open(os.path.join(tmpdir, "good_cc.txt"), "w") as f:
            f.write("card 4111-1111-1111-1111")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        m = {os.path.basename(d["file"]): d["category"] for d in data}
        assert m["good_ssn.txt"] == "pii"
        assert m["bad_ssn.txt"] == "non-essential"
        assert m["good_cc.txt"] == "pii"
        assert m["bad_cc.txt"] == "non-essential"


def test_log_content_not_business():
    with tempfile.TemporaryDirectory() as tmpdir:
        content = "\n".join(
            [f"2024-01-01 INFO budget processing {i}" for i in range(10)]
        )
        with open(os.path.join(tmpdir, "loglike.txt"), "w") as f:
            f.write(content)
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        assert data[0]["category"] == "non-essential"


def test_confidence_and_reasons():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "a.txt"), "w") as f:
            f.write("SSN 123-45-6789")
        out = os.path.join(tmpdir, "out.json")
        data = run_analyzer(tmpdir, out)
        for entry in data:
            assert "confidence" in entry and "reasons" in entry
            assert 0 <= entry["confidence"] <= 1
            assert isinstance(entry["reasons"], list) and len(entry["reasons"]) > 0


def test_randomized_differential_with_relative():
    random.seed(99)
    with tempfile.TemporaryDirectory() as tmpdir:
        pool = [
            "hello",
            "confidential",
            "financial",
            "revenue",
            "budget",
            "john@example.com",
            "123-45-6789",
            "000-12-3456",
            "4111-1111-1111-1111",
            "4111-1111-1111-1112",
            "2024-01-01 INFO start",
            "$5000",
            "10%",
            "data",
        ]
        for i in range(40):
            ext = random.choice([".txt", ".log", ".bak", ".tmp"])
            content = " ".join(random.choices(pool, k=random.randint(0, 8)))
            with open(os.path.join(tmpdir, f"f{i:02d}{ext}"), "w") as f:
                f.write(content)
        out_abs = os.path.join(tmpdir, "out_abs.json")
        out_rel = os.path.join(tmpdir, "out_rel.json")
        data_abs = run_analyzer(tmpdir, out_abs, workers=4, relative=False)
        data_rel = run_analyzer(tmpdir, out_rel, workers=4, relative=True)

        ref_abs = reference_classify(tmpdir, relative=False)
        ref_rel = reference_classify(tmpdir, relative=True)

        # filter out output json files themselves from comparison
        def filter_out(results, out_names):
            return [
                (f, c) for f, c in results if not any(f.endswith(n) for n in out_names)
            ]

        ref_abs_f = filter_out(ref_abs, ["out_abs.json", "out_rel.json"])
        ref_rel_f = filter_out(ref_rel, ["out_abs.json", "out_rel.json"])

        analyzer_abs_map = {
            d["file"]: d["category"]
            for d in data_abs
            if not d["file"].endswith(".json")
        }
        analyzer_rel_map = {
            d["file"]: d["category"]
            for d in data_rel
            if not d["file"].endswith(".json")
        }

        # compare absolute
        for fpath, cat in ref_abs_f:
            if fpath.endswith(".json"):
                continue
            assert fpath in analyzer_abs_map, f"missing abs {fpath}"
            assert analyzer_abs_map[fpath] == cat, (
                f"abs mismatch {fpath}: ref {cat} vs got {analyzer_abs_map[fpath]}"
            )
        # compare relative
        for fpath, cat in ref_rel_f:
            if fpath.endswith(".json"):
                continue
            assert fpath in analyzer_rel_map, f"missing rel {fpath}"
            assert analyzer_rel_map[fpath] == cat, (
                f"rel mismatch {fpath}: ref {cat} vs got {analyzer_rel_map[fpath]}"
            )


def test_performance():
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(500):
            with open(os.path.join(tmpdir, f"file_{i}.txt"), "w") as f:
                f.write(f"file {i} confidential revenue $100")
        out = os.path.join(tmpdir, "out.json")
        start = time.time()
        run_analyzer(tmpdir, out, workers=4)
        elapsed = time.time() - start
        assert elapsed < 5
