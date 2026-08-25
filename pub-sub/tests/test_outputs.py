"""Balanced hard tests for hierarchical allocator with burst, cost, dynamic weights, negative, global rebalancing, T lines output - hard but solvable.
Improved grading: exact multi-batch cases killing dynamic weight, burst carryover, served-decay vs eligible-but-unallocated, cost>1 negative, credit off-by-one.
"""

import os
import subprocess
import random
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
            timeout=120,
        )

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, f"go build failed:\n{r.stdout}\n{r.stderr}"
    yield


def run_case(T, loads, groups, subs):
    lines = [str(T)]
    for ld in loads:
        lines.append(str(ld))
    lines.append(str(len(groups)))
    for g in groups:
        if len(g) == 5:
            g = tuple(list(g) + [0])
        lines.append(" ".join(map(str, g)))
    lines.append(str(len(subs)))
    for s in subs:
        if len(s) == 6:
            s = tuple(list(s) + [0, 1])
        elif len(s) == 7:
            s = tuple(list(s) + [1])
        lines.append(" ".join(map(str, s)))
    inp = "\n".join(lines) + "\n"
    proc = subprocess.run([BIN], input=inp, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\ninput:\n{inp}"
    return [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""]


def run_case_raw(raw):
    proc = subprocess.run([BIN], input=raw, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\nraw:\n{raw}"
    return [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""]


CASES = [
    (
        1,
        [16],
        [(10, 0, 5, 10, 0, 0), (5, 0, 3, 10, 0, 0)],
        [
            (0, 10, 0, 5, 6, 0, 0, 1),
            (0, 5, 0, 3, 9, 0, 0, 1),
            (1, 5, 0, 4, 3, 0, 0, 1),
            (1, 1, 0, 1, 12, 0, 0, 1),
        ],
        ["6,4,3,3"],
    ),
    (
        1,
        [9],
        [(0, 0, 5, 10, 0, 0), (0, 0, 6, 10, 0, 0)],
        [
            (0, 10, 2, 5, 10, 2, 0, 1),
            (0, 5, 1, 6, 10, 10, 0, 1),
            (1, 1, 0, 6, 1, 0, 0, 1),
        ],
        ["2,6,1"],
    ),
    (
        2,
        [6, 6],
        [(0, 0, 4, 11, 0, 0), (0, 0, 1, 6, 0, 0)],
        [
            (0, 5, 0, 4, 11, 0, 0, 1),
            (0, 1, 0, 1, 6, 0, 0, 1),
            (1, 10, 0, 2, 5, 0, 0, 1),
        ],
        ["4,1,1", "4,1,1"],
    ),
    (1, [5], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 2, 0, 0, 1)], ["2"]),
    (1, [0], [(1, 0, 1, 5, 0, 0)], [(0, 1, 0, 1, 5, 0, 0, 1)], ["0"]),
    (
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0, 1), (0, 0, 0, 1, 500000000000, 0, 0, 1)],
        ["500000000000,500000000000"],
    ),
    (
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0, 0, 1),
            (0, 0, 0, 1000000000000, 500000000000, 0, 0, 1),
        ],
        ["500000000000,500000000000"],
    ),
    (
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [
            (0, 0, 0, 4000000000000000000, 10, 0, 0, 1),
            (0, 0, 0, 4000000000000000000, 10, 0, 0, 1),
        ],
        ["2,1"],
    ),
    (
        2,
        [0, 9],
        [(3, 1, 7, 5, 0, 0), (5, 0, 7, 8, 0, 0)],
        [(0, 5, 2, 6, 1, 0, 0, 1), (1, 9, 2, 2, 2, 4, 0, 1), (0, 1, 0, 3, 5, 0, 0, 1)],
        ["0,0,0", "1,2,4"],
    ),
    (
        1,
        [14],
        [(0, 2, 7, 0, 0, 0), (0, 2, 3, 18, 0, 0)],
        [(1, 1, 2, 4, 1, 0, 0, 1), (0, 6, 0, 5, 9, 0, 0, 1), (1, 0, 0, 1, 7, 0, 0, 1)],
        ["1,0,7"],
    ),
    (
        1,
        [20],
        [(9, 0, 3, 16, 0, 0), (3, 1, 5, 4, 0, 0), (8, 2, 1, 8, 0, 0)],
        [
            (0, 5, 2, 5, 9, 0, 0, 1),
            (0, 10, 0, 2, 5, 0, 0, 1),
            (2, 10, 0, 3, 11, 0, 0, 1),
            (1, 10, 1, 4, 4, 0, 0, 1),
            (1, 9, 1, 3, 11, 0, 0, 1),
            (1, 7, 0, 3, 7, 0, 0, 1),
            (0, 3, 1, 2, 0, 0, 1),
        ],
        ["9,2,5,2,2,0,0"],
    ),
    (
        1,
        [12],
        [(10, 0, 10, 1, 0, 0), (8, 0, 8, 16, 0, 0)],
        [
            (0, 6, 0, 6, 12, 0, 0, 1),
            (0, 2, 0, 2, 8, 0, 0, 1),
            (1, 3, 0, 3, 13, 0, 0, 1),
            (1, 7, 0, 7, 1, 0, 0, 1),
            (0, 8, 0, 8, 5, 0, 0, 1),
        ],
        ["0,0,10,1,1"],
    ),
    (
        1,
        [23],
        [(0, 1, 1, 13, 0, 0), (4, 2, 3, 14, 6, 0), (2, 1, 6, 8, 4, 0)],
        [(0, 10, 0, 4, 7, 0, 0, 1), (2, 1, 1, 3, 11, 0, 0, 1)],
        ["7,4"],
    ),
    (
        1,
        [10],
        [(5, 0, 5, 10, 5, 0)],
        [(0, 0, 0, 1, 10, 2, 0, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
        ["2,2"],
    ),
    (1, [0], [(1, 0, 1, 5, 0, 0)], [(0, 1, 0, 1, 5, 0, 0, 1)], ["0"]),
    (
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 3, 10, 0, 0, 1), (0, 1, 1, 3, 10, 0, 0, 1)],
        ["3,2", "3,2", "0,0"],
    ),
    (
        1,
        [12],
        [(10, 0, 10, 1, 0, 0), (8, 0, 8, 16, 0, 0)],
        [
            (0, 6, 0, 6, 12, 0, 0, 1),
            (0, 2, 0, 2, 8, 0, 0, 1),
            (1, 3, 0, 3, 13, 0, 0, 1),
            (1, 7, 0, 7, 1, 0, 0, 1),
            (0, 8, 0, 8, 5, 0, 0, 1),
        ],
        ["0,0,10,1,1"],
    ),
    (
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
        ["3,2"],
    ),
    (
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0, 1), (0, 1, 0, 2, 10, 0, 0, 1)],
        ["3,3", "-3,-1"],
    ),
    (
        1,
        [8],
        [(0, 0, 2, 10, 3, 2)],
        [(0, 10, 1, 2, 10, 3, 1, 1), (0, 1, 1, 2, 10, 0, 0, 1)],
        ["3,2"],
    ),
    (
        2,
        [10, -5],
        [(5, 0, 5, 10, 3, 2), (5, 0, 5, 10, 0, 0)],
        [
            (0, 10, 1, 5, 10, 2, 1, 1),
            (0, 1, 1, 5, 10, 0, 0, 1),
            (1, 5, 0, 4, 10, 1, 0, 1),
        ],
        ["3,2,1", "-3,-2,0"],
    ),
    (
        1,
        [10],
        [(0, 0, 1, 10, 2, 5)],
        [(0, 0, 0, 1, 10, 2, 5, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
        ["5,2"],
    ),
    (
        1,
        [5],
        [(0, 0, 1, 20, 0, 0)],
        [(0, 0, 0, 1, 10, 0, 0, 2), (0, 0, 0, 1, 10, 0, 0, 5)],
        ["3,2"],
    ),
    (
        1,
        [8],
        [(0, 0, 2, 20, 0, 0)],
        [(0, 10, 1, 2, 20, 0, 0, 2), (0, 1, 1, 2, 20, 0, 0, 3)],
        ["4,4"],
    ),
    # --- New exact multi-batch cases closing grading holes ---
    # Dynamic weight AFTR 1,1,10 case: weight decays when served, +1 when eligible but unallocated
    (
        3,
        [1, 1, 10],
        [(1, 1, 4, 18, 0, 3)],
        [
            (0, 1, 1, 1, 13, 0, 2, 2),
            (0, 0, 1, 3, 14, 0, 0, 2),
            (0, 5, 0, 1, 17, 0, 0, 1),
            (0, 5, 0, 5, 8, 0, 0, 1),
        ],
        ["1,0,0,0", "1,0,0,0", "1,3,1,5"],
    ),
    # Served-decay isolated: weight 10% decay when served
    (
        3,
        [1, 1, 10],
        [(0, 0, 5, 12, 0, 0), (0, 0, 7, 30, 4, 2)],
        [
            (0, 3, 0, 2, 19, 0, 2, 1),
            (1, 0, 1, 10, 15, 0, 2, 2),
            (1, 0, 0, 5, 20, 0, 2, 1),
            (0, 0, 1, 3, 16, 0, 3, 2),
        ],
        ["0,1,0,0", "0,0,0,1", "2,3,3,2"],
    ),
    # Eligible-but-unallocated +1 isolated
    (
        3,
        [1, 1, 10],
        [(2, 1, 3, 8, 0, 0)],
        [
            (0, 3, 1, 1, 5, 0, 0, 1),
            (0, 2, 1, 4, 5, 1, 1, 2),
            (0, 0, 1, 1, 13, 0, 0, 1),
            (0, 3, 0, 3, 11, 0, 1, 1),
        ],
        ["1,0,0,0", "1,0,0,0", "1,2,2,1"],
    ),
    # Burst carryover: batch2 cap differs because batch1 consumed burst
    (
        2,
        [5, 5],
        [(5, 0, 4, 11, 2, 3)],
        [
            (0, 4, 0, 1, 5, 0, 2, 1),
            (0, 3, 1, 3, 18, 0, 0, 2),
            (0, 1, 1, 3, 6, 0, 0, 1),
        ],
        ["1,2,2", "0,1,1"],
    ),
    # Burst carryover simple 5,5 with rate2 burst3
    (
        2,
        [5, 5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
        ["3,2", "1,1"],
    ),
    # Negative deallocation with cost >1
    (
        2,
        [6, -4],
        [(2, 0, 2, 30, 0, 0)],
        [(0, 0, 0, 2, 28, 0, 0, 2), (0, 1, 0, 1, 23, 0, 0, 3)],
        ["4,2", "-2,-2"],
    ),
    # Credit off-by-one (f400820 bug) – multi-round only
    (
        1,
        [8],
        [(2, 0, 3, 12, 0, 0)],
        [
            (0, 2, 0, 1, 25, 0, 0, 1),
            (0, 2, 0, 3, 16, 0, 0, 1),
            (0, 0, 0, 1, 18, 0, 0, 1),
        ],
        ["2,5,1"],
    ),
    # Sensitive cases converted to exact (previously invariant-only)
    (
        2,
        [11, 17],
        [(9, 0, 6, 15, 0, 0), (7, 0, 6, 10, 0, 0), (6, 1, 3, 12, 0, 0)],
        [
            (2, 0, 2, 1, 7, 0, 0, 1),
            (0, 1, 2, 2, 0, 0, 0, 1),
            (0, 4, 0, 5, 1, 0, 0, 1),
            (1, 7, 1, 1, 6, 0, 0, 1),
            (0, 3, 1, 6, 12, 0, 0, 1),
            (0, 6, 2, 5, 3, 0, 0, 1),
            (2, 7, 0, 2, 8, 0, 0, 1),
            (2, 8, 2, 2, 2, 0, 0, 1),
            (2, 7, 0, 6, 3, 0, 0, 1),
            (0, 1, 2, 1, 1, 0, 0, 1),
        ],
        ["1,0,0,4,1,2,0,2,0,1", "2,0,1,2,7,1,1,0,3,0"],
    ),
    (
        2,
        [11, 6],
        [(4, 0, 2, 6, 0, 0), (4, 2, 1, 0, 0, 0), (2, 2, 3, 10, 0, 0)],
        [
            (0, 9, 2, 3, 8, 0, 0, 1),
            (1, 0, 0, 4, 6, 0, 0, 1),
            (2, 9, 1, 6, 10, 0, 0, 1),
            (2, 10, 1, 5, 8, 0, 0, 1),
        ],
        ["4,0,4,3", "2,0,2,1"],
    ),
    (
        2,
        [10, 10],
        [(5, 0, 5, 10, 0, 0), (5, 0, 5, 10, 0, 0)],
        [
            (0, 10, 1, 5, 10, 0, 0, 1),
            (0, 1, 1, 5, 10, 0, 0, 1),
            (1, 5, 0, 4, 10, 0, 0, 1),
        ],
        ["3,2,5", "3,2,5"],
    ),
]


@pytest.mark.parametrize("T,loads,groups,subs,expected", CASES)
def test_allocation(T, loads, groups, subs, expected):
    out = run_case(T, loads, groups, subs)
    assert out == expected


def test_conservation():
    for T, loads, groups, subs, _ in CASES:
        out = run_case(T, loads, groups, subs)
        assert len(out) == T
        S = len(subs)
        caps = [s[4] for s in subs]
        costs = [s[7] if len(s) >= 8 else 1 for s in subs]
        tot_cost = [0] * S
        for line in out:
            parts = [int(x) for x in line.split(",")]
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert tot_cost[i] + v * costs[i] >= 0
                assert tot_cost[i] + v * costs[i] <= caps[i]
            for i, v in enumerate(parts):
                tot_cost[i] += v * costs[i]


def test_min_exceeds_cap():
    out = run_case(1, [5], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 2, 0, 0, 1)])
    assert out == ["2"]


def test_min_gt_rate():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 0, 1)])
    assert out == ["2"]


def test_min_gt_rate_with_burst():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 3, 1)])
    assert out == ["5"]


def test_priority_tie_and_order():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 2, 1, 10, 0, 0, 1), (0, 1, 2, 1, 10, 0, 0, 1)],
    )
    assert out == ["2,1"]
    out2 = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 5, 2, 1, 10, 0, 0, 1), (0, 5, 2, 1, 10, 0, 0, 1)],
    )
    assert out2 == ["2,1"]


def test_group_no_members():
    out = run_case(
        1, [10], [(0, 0, 5, 10, 0, 0), (0, 0, 5, 10, 0, 0)], [(0, 0, 0, 1, 5, 0, 0, 1)]
    )
    assert out == ["5"]


def test_invalid_gid():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 5, 0, 0, 1), (99, 0, 0, 1, 5, 0, 0, 1)],
    )
    assert out == ["5,0"]


def test_blank_lines_and_spaces():
    raw = """
1
16

2
10 0 5 10 0 0
5 0 3 10 0 0
4
0 10 0 5 6 0 0 1
  0 5 0 3 9 0 0 1
1 5 0 4 3 0 0 1
1 1 0 1 12 0 0 1

"""
    out = run_case_raw(raw)
    assert out == ["6,4,3,3"]


def test_tab_delimited_and_spaces():
    raw = "1\n16\n2\n10\t0\t5\t10\t0\t0\n5\t0\t3\t10\t0\t0\n4\n0\t10\t0\t5\t6\t0\t0\t1\n0\t5\t0\t3\t9\t0\t0\t1\n1\t5\t0\t4\t3\t0\t0\t1\n1\t1\t0\t1\t12\t0\t0\t1\n"
    out = run_case_raw(raw)
    assert out == ["6,4,3,3"]


def test_large_numbers():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0, 1), (0, 0, 0, 1, 500000000000, 0, 0, 1)],
    )
    assert out == ["500000000000,500000000000"]


def test_large_weight_overflow():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0, 0, 1),
            (0, 0, 0, 1000000000000, 500000000000, 0, 0, 1),
        ],
    )
    assert out == ["500000000000,500000000000"]


def test_large_credit_overflow():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [
            (0, 0, 0, 4000000000000000000, 10, 0, 0, 1),
            (0, 0, 0, 4000000000000000000, 10, 0, 0, 1),
        ],
    )
    assert out == ["2,1"]


def test_rate_limiting():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 5, 0)],
        [(0, 0, 0, 1, 10, 2, 0, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
    )
    assert out == ["2,2"]


def test_rate_with_burst():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out == ["3,2"]


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0, 0, 0)], [(0, 0, 0, 1, 0, 0, 0, 1)])
    assert out == ["0"]


def test_global_rebalancing():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0), (0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 1, 0, 0, 1), (0, 0, 0, 1, 1, 0, 0, 1), (1, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out == ["1,1,8"]


def test_cost_factor():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 20, 0, 0)],
        [(0, 0, 0, 1, 10, 0, 0, 2), (0, 0, 0, 1, 10, 0, 0, 5)],
    )
    assert out == ["3,2"]


def test_negative_deallocation():
    out = run_case(
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0, 1), (0, 1, 0, 2, 10, 0, 0, 1)],
    )
    assert out == ["3,3", "-3,-1"]


def test_dynamic_weight_isolated():
    out = run_case(
        2,
        [5, 5],
        [(0, 0, 10, 20, 0, 0)],
        [(0, 10, 1, 10, 20, 0, 0, 1), (0, 1, 1, 10, 20, 0, 0, 1)],
    )
    assert out == ["3,2", "3,2"]


def test_burst_carryover_multi_batch():
    # Stronger burst carryover: second batch rate-limited due to burst consumption
    out = run_case(
        2,
        [5, 5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out == ["3,2", "1,1"]


def test_cost_factor_isolated():
    out = run_case(
        1,
        [8],
        [(0, 0, 2, 20, 0, 0)],
        [(0, 10, 1, 2, 20, 0, 0, 2), (0, 1, 1, 2, 20, 0, 0, 3)],
    )
    assert out == ["4,4"]


def test_backward_compat_old_format_5_6():
    raw_old = """
1
10
1
0 0 1 10 0
2
0 10 0 1 5 0
0 1 0 1 5 0
"""
    out_old = run_case_raw(raw_old)
    out_new = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 0, 1, 5, 0, 0, 1), (0, 1, 0, 1, 5, 0, 0, 1)],
    )
    assert out_old == out_new
    assert out_old == ["5,5"]


def test_backward_compat_7_fields():
    raw_7 = """
1
10
1
0 0 1 10 0 0
2
0 10 0 1 5 0 0
0 1 0 1 5 0 0
"""
    out_7 = run_case_raw(raw_7)
    out_8 = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 0, 1, 5, 0, 0, 1), (0, 1, 0, 1, 5, 0, 0, 1)],
    )
    assert out_7 == out_8
    assert out_7 == ["5,5"]


def test_deterministic():
    rnd = random
    rnd.seed(303)
    for _ in range(20):
        T = rnd.randint(1, 3)
        loads = [rnd.randint(0, 20) for _ in range(T)]
        G = rnd.randint(1, 3)
        groups = [
            (
                rnd.randint(0, 5),
                rnd.randint(0, 2),
                rnd.randint(1, 4),
                rnd.randint(0, 10),
                rnd.choice([0, rnd.randint(1, 5)]),
                rnd.choice([0, rnd.randint(1, 3)]),
            )
            for _ in range(G)
        ]
        S = rnd.randint(1, 6)
        subs = [
            (
                rnd.randint(0, G - 1),
                rnd.randint(0, 5),
                rnd.randint(0, 2),
                rnd.randint(1, 4),
                rnd.randint(0, 10),
                rnd.choice([0, rnd.randint(1, 5)]),
                rnd.choice([0, rnd.randint(1, 3)]),
                rnd.choice([1, 2, 3]),
            )
            for _ in range(S)
        ]
        a = run_case(T, loads, groups, subs)
        b = run_case(T, loads, groups, subs)
        assert a == b


def test_fuzz_invariants():
    rnd = random
    rnd.seed(2024)
    for _ in range(30):
        T = rnd.randint(1, 3)
        loads = [rnd.randint(0, 30) for _ in range(T)]
        G = rnd.randint(1, 3)
        groups = [
            (
                rnd.randint(0, 10),
                rnd.randint(0, 3),
                rnd.randint(1, 8),
                rnd.randint(0, 20),
                rnd.choice([0, 0, rnd.randint(1, 10)]),
                rnd.choice([0, 0, rnd.randint(1, 5)]),
            )
            for _ in range(G)
        ]
        S = rnd.randint(1, G * 4 + 2)
        subs = [
            (
                rnd.randint(0, G - 1),
                rnd.randint(0, 10),
                rnd.randint(0, 2),
                rnd.randint(1, 6),
                rnd.randint(0, 15),
                rnd.choice([0, 0, rnd.randint(1, 10)]),
                rnd.choice([0, 0, rnd.randint(1, 5)]),
                rnd.choice([1, 2, 3]),
            )
            for _ in range(S)
        ]
        out = run_case(T, loads, groups, subs)
        assert len(out) == T
        caps = [s[4] for s in subs]
        costs = [s[7] if len(s) >= 8 else 1 for s in subs]
        tot_cost = [0] * S
        for line in out:
            parts = [int(x) for x in line.split(",")]
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert tot_cost[i] + v * costs[i] >= 0
                assert tot_cost[i] + v * costs[i] <= caps[i]
            for i, v in enumerate(parts):
                tot_cost[i] += v * costs[i]


# --- Additional grading-hole killers (exact, multi-batch) ---


def test_dynamic_weight_1_1_10():
    # AFTR case: loads 1,1,10 weight evolution matters
    out = run_case(
        3,
        [1, 1, 10],
        [(1, 1, 4, 18, 0, 3)],
        [
            (0, 1, 1, 1, 13, 0, 2, 2),
            (0, 0, 1, 3, 14, 0, 0, 2),
            (0, 5, 0, 1, 17, 0, 0, 1),
            (0, 5, 0, 5, 8, 0, 0, 1),
        ],
    )
    assert out == ["1,0,0,0", "1,0,0,0", "1,3,1,5"]


def test_served_decay_vs_unallocated():
    # served-decay isolated
    out = run_case(
        3,
        [1, 1, 10],
        [(0, 0, 5, 12, 0, 0), (0, 0, 7, 30, 4, 2)],
        [
            (0, 3, 0, 2, 19, 0, 2, 1),
            (1, 0, 1, 10, 15, 0, 2, 2),
            (1, 0, 0, 5, 20, 0, 2, 1),
            (0, 0, 1, 3, 16, 0, 3, 2),
        ],
    )
    assert out == ["0,1,0,0", "0,0,0,1", "2,3,3,2"]


def test_eligible_but_unallocated_plus1():
    out = run_case(
        3,
        [1, 1, 10],
        [(2, 1, 3, 8, 0, 0)],
        [
            (0, 3, 1, 1, 5, 0, 0, 1),
            (0, 2, 1, 4, 5, 1, 1, 2),
            (0, 0, 1, 1, 13, 0, 0, 1),
            (0, 3, 0, 3, 11, 0, 1, 1),
        ],
    )
    assert out == ["1,0,0,0", "1,0,0,0", "1,2,2,1"]


def test_burst_carryover_cap_diff():
    # batch2 cap differs because batch1 consumed burst
    out = run_case(
        2,
        [5, 5],
        [(5, 0, 4, 11, 2, 3)],
        [
            (0, 4, 0, 1, 5, 0, 2, 1),
            (0, 3, 1, 3, 18, 0, 0, 2),
            (0, 1, 1, 3, 6, 0, 0, 1),
        ],
    )
    assert out == ["1,2,2", "0,1,1"]


def test_negative_dealloc_cost_gt1():
    out = run_case(
        2,
        [6, -4],
        [(2, 0, 2, 30, 0, 0)],
        [(0, 0, 0, 2, 28, 0, 0, 2), (0, 1, 0, 1, 23, 0, 0, 3)],
    )
    assert out == ["4,2", "-2,-2"]


def test_credit_off_by_one():
    # f400820 bug locus: credit should be c/2+1 not c/2, only manifests multi-round
    out = run_case(
        1,
        [8],
        [(2, 0, 3, 12, 0, 0)],
        [
            (0, 2, 0, 1, 25, 0, 0, 1),
            (0, 2, 0, 3, 16, 0, 0, 1),
            (0, 0, 0, 1, 18, 0, 0, 1),
        ],
    )
    assert out == ["2,5,1"]
