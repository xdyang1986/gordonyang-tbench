"""Black-box tests for single-level multi-batch allocator with min, priority, credit-decay - balanced."""

import os
import subprocess

import pytest

APP = "/app"
BIN = "/tmp/agent_allocator"

GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}


def _find_main_pkg():
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                try:
                    if "func main(" in open(os.path.join(root, f)).read():
                        rel = os.path.relpath(root, APP)
                        return "." if rel == "." else "./" + rel
                except OSError:
                    pass
    return None


@pytest.fixture(scope="session", autouse=True)
def built():
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(
            ["go", "mod", "init", "allocator"],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
            text=True,
        )

    def _build(pkg):
        return subprocess.run(
            ["go", "build", "-o", BIN, pkg],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
            text=True,
            timeout=240,
        )

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, (
        f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run_case(T, loads, subs):
    lines = [str(T)]
    for ld in loads:
        lines.append(str(ld))
    lines.append(str(len(subs)))
    for p, mn, w, c in subs:
        lines.append(f"{p} {mn} {w} {c}")
    inp = "\n".join(lines) + "\n"
    proc = subprocess.run([BIN], input=inp, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\ninput:\n{inp}"
    out_lines = [
        ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""
    ]
    return out_lines


def run_case_raw(raw):
    proc = subprocess.run([BIN], input=raw, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\nraw:\n{raw}"
    out_lines = [
        ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""
    ]
    return out_lines


# (T, loads, subs[(prio,min,w,cap)], expected_lines)
CASES = [
    (1, [10], [(0, 0, 3, 100), (0, 0, 1, 100)], ["8,2"]),
    (1, [9], [(10, 2, 5, 10), (5, 1, 6, 10), (1, 0, 6, 1)], ["4,4,1"]),
    (2, [6, 6], [(5, 0, 4, 11), (1, 0, 1, 6), (10, 0, 2, 5)], ["4,0,2", "3,1,2"]),
    (1, [5], [(10, 10, 1, 2)], ["2"]),
    (1, [7], [(1, 2, 1, 2), (1, 2, 1, 2), (1, 2, 1, 2)], ["2,2,2"]),
    (1, [0], [(1, 0, 1, 5)], ["0"]),
    (1, [1], [(10, 2, 5, 10), (1, 2, 1, 10)], ["1,0"]),
    (
        3,
        [6, 17, 11],
        [(0, 1, 5, 7), (4, 3, 6, 6), (5, 3, 1, 8), (3, 1, 1, 14)],
        ["0,3,3,0", "7,3,4,3", "0,0,1,10"],
    ),
    (
        3,
        [11, 6, 15],
        [(3, 3, 4, 6), (1, 3, 4, 9), (3, 0, 6, 10)],
        ["4,4,3", "2,3,1", "0,2,6"],
    ),
    (3, [8, 12, 12], [(6, 0, 4, 13), (0, 3, 2, 3)], ["5,3", "8,0", "0,0"]),
    (
        2,
        [0, 11],
        [
            (5, 0, 3, 4),
            (0, 3, 2, 8),
            (1, 1, 4, 11),
            (10, 3, 2, 15),
            (8, 2, 2, 0),
            (1, 0, 1, 11),
        ],
        ["0,0,0,0,0,0", "1,4,3,3,0,0"],
    ),
    (
        3,
        [14, 3, 19],
        [(7, 1, 6, 5), (3, 3, 2, 2), (2, 2, 3, 12), (1, 0, 5, 11), (7, 0, 2, 8)],
        ["5,2,3,3,1", "0,0,2,1,0", "0,0,7,5,7"],
    ),
    (3, [10, 2, 8], [(4, 3, 2, 13), (1, 2, 2, 3)], ["7,3", "2,0", "4,0"]),
    (2, [20, 5], [(0, 3, 5, 14)], ["14", "0"]),
    (3, [18, 1, 10], [(4, 3, 6, 6), (8, 2, 3, 0)], ["6,0", "0,0", "0,0"]),
    (1, [9], [(10, 2, 4, 12), (10, 0, 6, 7), (8, 0, 2, 4)], ["4,4,1"]),
    (3, [3, 4, 10], [(5, 0, 4, 6)], ["3", "3", "0"]),
    (
        3,
        [11, 16, 20],
        [(3, 1, 4, 10), (4, 2, 6, 8), (3, 3, 2, 14), (4, 0, 2, 1), (9, 2, 6, 14)],
        ["2,3,3,0,3", "3,4,4,1,4", "5,1,7,0,7"],
    ),
    (
        2,
        [1, 8],
        [(3, 0, 4, 1), (0, 3, 4, 8), (9, 1, 4, 1), (0, 2, 5, 3), (1, 3, 3, 2)],
        ["0,0,1,0,0", "0,3,0,3,2"],
    ),
    (
        1,
        [15],
        [
            (6, 0, 3, 0),
            (3, 2, 2, 3),
            (3, 1, 6, 5),
            (2, 1, 4, 4),
            (0, 1, 5, 7),
            (1, 0, 3, 4),
        ],
        ["0,3,5,3,3,1"],
    ),
    (3, [15, 4, 3], [(0, 3, 5, 2)], ["2", "0", "0"]),
]


@pytest.mark.parametrize("T,loads,subs,expected", CASES)
def test_allocation(T, loads, subs, expected):
    out_lines = run_case(T, loads, subs)
    assert len(out_lines) == len(expected), (
        f"expected {len(expected)} lines, got {len(out_lines)}: {out_lines}"
    )
    for got, exp in zip(out_lines, expected):
        assert got == exp


def test_conservation():
    for T, loads, subs, expected in CASES:
        out_lines = run_case(T, loads, subs)
        S = len(subs)
        caps = [c for _, _, _, c in subs]
        total = [0] * S
        for line in out_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert 0 <= v
                assert total[i] + v <= caps[i]
            for i, v in enumerate(parts):
                total[i] += v


def test_min_exceeds_cap():
    T = 1
    loads = [5]
    subs = [(10, 10, 1, 2)]
    out = run_case(T, loads, subs)
    assert out == ["2"]


def test_priority_tie_and_order():
    T = 1
    loads = [3]
    subs = [(10, 2, 1, 10), (1, 2, 1, 10)]
    out = run_case(T, loads, subs)
    assert out == ["2,1"]
    subs2 = [(5, 2, 1, 10), (5, 2, 1, 10)]
    out2 = run_case(T, loads, subs2)
    assert out2 == ["2,1"]


def test_blank_lines_and_spaces():
    raw = """
1

10

2

  0   0  3  100
  0 0 1 100

"""
    out = run_case_raw(raw)
    assert out == ["8,2"]


def test_large_numbers():
    T = 1
    loads = [1000000000000]
    subs = [(0, 0, 1, 500000000000), (0, 0, 1, 500000000000)]
    out = run_case(T, loads, subs)
    assert out == ["500000000000,500000000000"]


def test_zero_caps():
    T = 1
    loads = [10]
    subs = [(0, 0, 1, 0)]
    out = run_case(T, loads, subs)
    assert out == ["0"]


def test_rr_fallback_efficiency():
    # total credit never 0 with correct decay, but ensure many small batches don't deadlock and are deterministic
    T = 5
    loads = [1, 1, 1, 1, 1]
    subs = [(0, 0, 1, 10), (0, 0, 1, 10)]
    out = run_case(T, loads, subs)
    assert len(out) == 5
    total = sum(int(v) for line in out for v in line.split(","))
    assert total == 5


def test_deterministic():
    T = 1
    loads = [10]
    subs = [(0, 0, 3, 100), (0, 0, 1, 100)]
    a = run_case(T, loads, subs)
    b = run_case(T, loads, subs)
    assert a == b == ["8,2"]
