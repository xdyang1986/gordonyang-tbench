"""Ultimate hard tests for hierarchical allocator with burst, cost, dynamic weights, priority aging, negative deallocation, global rebalancing, T+8 output - very hard."""

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
            timeout=240,
        )

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, f"go build failed:\n{r.stdout}\n{r.stderr}"
    yield


def _chmod_no_access():
    try:
        os.chmod(__file__, 0o000)
    except Exception:
        pass


def _chmod_restore():
    try:
        os.chmod(__file__, 0o644)
    except Exception:
        pass


def run_case(T, loads, groups, subs):
    # groups 6 fields, subs 8 fields (cost), but allow old formats
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
    _chmod_no_access()
    try:
        proc = subprocess.run(
            [BIN], input=inp, capture_output=True, text=True, timeout=30
        )
    finally:
        _chmod_restore()
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\ninput:\n{inp}"
    return [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""]


def run_case_raw(raw):
    _chmod_no_access()
    try:
        proc = subprocess.run(
            [BIN], input=raw, capture_output=True, text=True, timeout=30
        )
    finally:
        _chmod_restore()
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
        [
            "6,4,3,3",
            "10,6",
            "6,4,3,3",
            "3,2",
            "3,2,3,1",
            "0,0",
            "0,0,0,0",
            "4,2",
            "4,2,3,1",
        ],
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
        ["2,6,1", "8,1", "2,6,1", "3,4", "3,4,4", "0,0", "0,0,0", "4,5", "4,5,5"],
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
        [
            "4,1,1",
            "4,1,1",
            "10,2",
            "8,2,2",
            "2,1",
            "2,1,2",
            "0,0",
            "0,0,0",
            "2,1",
            "2,1,1",
        ],
    ),
    (
        1,
        [5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 10, 1, 2, 0, 0, 1)],
        ["2", "2", "2", "1", "1", "0", "0", "1", "1"],
    ),
    (
        1,
        [0],
        [(1, 0, 1, 5, 0, 0)],
        [(0, 1, 0, 1, 5, 0, 0, 1)],
        ["0", "0", "0", "2", "2", "0", "0", "2", "2"],
    ),
    (
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0, 1), (0, 0, 0, 1, 500000000000, 0, 0, 1)],
        [
            "500000000000,500000000000",
            "1000000000000",
            "500000000000,500000000000",
            "1",
            "1,1",
            "0",
            "0,0",
            "1",
            "1,1",
        ],
    ),
    (
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0, 0, 1),
            (0, 0, 0, 1000000000000, 500000000000, 0, 0, 1),
        ],
        [
            "500000000000,500000000000",
            "1000000000000",
            "500000000000,500000000000",
            "500000000001",
            "500000000001,500000000001",
            "0",
            "0,0",
            "900000000000",
            "900000000000,900000000000",
        ],
    ),
    (
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [
            (0, 0, 0, 4000000000000000000, 10, 0, 0, 1),
            (0, 0, 0, 4000000000000000000, 10, 0, 0, 1),
        ],
        [
            "2,1",
            "3",
            "2,1",
            "1",
            "2000000000000000001,2000000000000000001",
            "0",
            "0,0",
            "1",
            "1,1",
        ],
    ),
    (
        2,
        [0, 9],
        [(3, 1, 7, 5, 0, 0), (5, 0, 7, 8, 0, 0)],
        [(0, 5, 2, 6, 1, 0, 0, 1), (1, 9, 2, 2, 2, 4, 0, 1), (0, 1, 0, 3, 5, 0, 0, 1)],
        [
            "0,0,0",
            "1,2,4",
            "5,2",
            "1,2,4",
            "8,8",
            "7,3,4",
            "0,0",
            "0,0,0",
            "7,7",
            "6,2,3",
        ],
    ),
    (
        1,
        [14],
        [(0, 2, 7, 0, 0, 0), (0, 2, 3, 18, 0, 0)],
        [(1, 1, 2, 4, 1, 0, 0, 1), (0, 6, 0, 5, 9, 0, 0, 1), (1, 0, 0, 1, 7, 0, 0, 1)],
        ["1,0,7", "0,8", "1,0,7", "7,2", "3,10,1", "0,0", "0,0,0", "7,2", "3,6,1"],
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
        [
            "9,2,5,2,2,0,0",
            "11,4,5",
            "9,2,5,2,2,0,0",
            "2,3,1",
            "3,2,2,3,2,6,2",
            "0,0,0",
            "0,0,0,0,0,0,1",
            "2,4,1",
            "4,1,2,3,2,4,2",
        ],
    ),
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
        [
            "1,0,0,4,1,2,0,2,0,1",
            "2,0,1,2,7,1,1,0,3,0",
            "13,6,9",
            "3,0,1,6,8,3,1,2,3,1",
            "3,3,2",
            "1,2,6,1,3,2,3,2,7,1",
            "0,0,0",
            "0,0,0,0,0,0,0,0,0,0",
            "4,4,1",
            "1,2,5,1,4,3,2,1,6,1",
        ],
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
        [
            "0,0,10,1,1",
            "1,11",
            "0,0,10,1,1",
            "6,5",
            "12,4,2,4,5",
            "0,0",
            "0,0,0,0,0",
            "9,7",
            "7,3,2,6,7",
        ],
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
        [
            "4,0,4,3",
            "2,0,2,1",
            "6,0,10",
            "6,0,6,4",
            "2,1,2",
            "2,13,3,2",
            "0,0,0",
            "0,0,0,0",
            "1,1,1",
            "1,6,4,3",
        ],
    ),
    (
        1,
        [23],
        [(0, 1, 1, 13, 0, 0), (4, 2, 3, 14, 6, 0), (2, 1, 6, 8, 4, 0)],
        [(0, 10, 0, 4, 7, 0, 0, 1), (2, 1, 1, 3, 11, 0, 0, 1)],
        ["7,4", "7,0,4", "7,4", "1,3,4", "3,2", "0,0,0", "0,0", "1,3,5", "3,2"],
    ),
    (
        1,
        [10],
        [(5, 0, 5, 10, 5, 0)],
        [(0, 0, 0, 1, 10, 2, 0, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
        ["2,2", "4", "2,2", "3", "1,1", "0", "0,0", "4", "1,1"],
    ),
    (
        1,
        [0],
        [(1, 0, 1, 5, 0, 0)],
        [(0, 1, 0, 1, 5, 0, 0, 1)],
        ["0", "0", "0", "2", "2", "0", "0", "2", "2"],
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
        [
            "3,2,5",
            "3,2,5",
            "10,10",
            "6,4,10",
            "2,2",
            "2,2,2",
            "0,0",
            "0,0,0",
            "3,3",
            "3,3,2",
        ],
    ),
    (
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 3, 10, 0, 0, 1), (0, 1, 1, 3, 10, 0, 0, 1)],
        ["3,2", "3,2", "0,0", "10", "6,4", "1", "3,3", "0", "0,0", "1", "2,2"],
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
        [
            "0,0,10,1,1",
            "1,11",
            "0,0,10,1,1",
            "6,5",
            "12,4,2,4,5",
            "0,0",
            "0,0,0,0,0",
            "9,7",
            "7,3,2,6,7",
        ],
    ),
    (
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
        ["3,2", "5", "3,2", "1", "1,1", "0", "2,0", "1", "1,1"],
    ),
    (
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0, 1), (0, 1, 0, 2, 10, 0, 0, 1)],
        ["3,3", "-3,-1", "2", "0,2", "2", "2,2", "0", "0,0", "1", "1,1"],
    ),
    (
        1,
        [8],
        [(0, 0, 2, 10, 3, 2)],
        [(0, 10, 1, 2, 10, 3, 1, 1), (0, 1, 1, 2, 10, 0, 0, 1)],
        ["3,2", "5", "3,2", "2", "2,2", "0", "1,0", "1", "1,1"],
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
        [
            "3,2,1",
            "-3,-2,0",
            "0,1",
            "0,0,1",
            "2,7",
            "2,2,6",
            "0,0",
            "0,0,0",
            "3,5",
            "3,3,4",
        ],
    ),
    (
        1,
        [10],
        [(0, 0, 1, 10, 2, 5)],
        [(0, 0, 0, 1, 10, 2, 5, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
        ["5,2", "7", "5,2", "1", "1,1", "0", "2,0", "1", "1,1"],
    ),
    (
        1,
        [5],
        [(0, 0, 1, 20, 0, 0)],
        [(0, 0, 0, 1, 10, 0, 0, 2), (0, 0, 0, 1, 10, 0, 0, 5)],
        ["3,2", "16", "6,10", "1", "1,1", "0", "0,0", "1", "1,1"],
    ),
    (
        1,
        [8],
        [(0, 0, 2, 20, 0, 0)],
        [(0, 10, 1, 2, 20, 0, 0, 2), (0, 1, 1, 2, 20, 0, 0, 3)],
        ["4,4", "20", "8,12", "2", "2,2", "0", "0,0", "1", "1,1"],
    ),
]


@pytest.mark.parametrize("T,loads,groups,subs,expected", CASES)
def test_allocation(T, loads, groups, subs, expected):
    out_lines = run_case(T, loads, groups, subs)
    assert len(out_lines) == len(expected), (
        f"expected {len(expected)} lines, got {len(out_lines)}: {out_lines}\nExpected: {expected}"
    )
    for got, exp in zip(out_lines, expected):
        assert got == exp


def test_conservation():
    for T, loads, groups, subs, _ in CASES:
        out_lines = run_case(T, loads, groups, subs)
        batch_lines = out_lines[:T]
        assert len(out_lines) == T + 8
        G = len(groups)
        S = len(subs)
        group_caps = [g[3] for g in groups]
        sub_caps = [s[4] for s in subs]
        # cost factor
        sub_costs = [s[7] if len(s) >= 8 else 1 for s in subs]
        group_tot = [0] * G
        sub_tot_cost = [0] * S
        for line in batch_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                # allow negative for deallocation
                assert sub_tot_cost[i] + v * sub_costs[i] >= 0
                assert sub_tot_cost[i] + v * sub_costs[i] <= sub_caps[i]
            for i, v in enumerate(parts):
                sub_tot_cost[i] += v * sub_costs[i]
            for g in range(G):
                gs_cost = sum(
                    parts[i] * sub_costs[i]
                    for i, sg in enumerate([s[0] for s in subs])
                    if sg == g
                )
                group_tot[g] += gs_cost

        group_tot_line = (
            [int(x) for x in out_lines[T].split(",")] if out_lines[T] else []
        )
        sub_tot_line = (
            [int(x) for x in out_lines[T + 1].split(",")] if out_lines[T + 1] else []
        )
        assert group_tot_line == group_tot
        assert sub_tot_line == sub_tot_cost


def test_min_exceeds_cap():
    out = run_case(1, [5], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 2, 0, 0, 1)])
    assert out[0] == "2"
    assert len(out) == 9


def test_min_gt_rate():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 0, 1)])
    assert out[0] == "2"
    assert len(out) == 9


def test_min_gt_rate_with_burst():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 3, 1)])
    assert out[0] == "5"
    assert len(out) == 9
    assert (
        out[6] == "0"
    )  # sub burst 0 after consuming? Actually sub burst 3 remaining 0?
    # For this case burst 3, rate2, alloc5 -> burst consumed 3 -> remaining 0
    # Check burst lines
    assert out[5] == "0"  # group burst
    assert out[6] == "0"


def test_priority_tie_and_order():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 2, 1, 10, 0, 0, 1), (0, 1, 2, 1, 10, 0, 0, 1)],
    )
    assert out[0] == "2,1"
    out2 = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 5, 2, 1, 10, 0, 0, 1), (0, 5, 2, 1, 10, 0, 0, 1)],
    )
    assert out2[0] == "2,1"


def test_group_no_members():
    out = run_case(
        1, [10], [(0, 0, 5, 10, 0, 0), (0, 0, 5, 10, 0, 0)], [(0, 0, 0, 1, 5, 0, 0, 1)]
    )
    assert out[0] == "5"
    assert out[1] == "5,0"
    assert len(out) == 9


def test_invalid_gid():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 5, 0, 0, 1), (99, 0, 0, 1, 5, 0, 0, 1)],
    )
    assert out[0] == "5,0"
    assert len(out) == 9


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
    assert out[0] == "6,4,3,3"
    assert len(out) == 9


def test_large_numbers():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0, 1), (0, 0, 0, 1, 500000000000, 0, 0, 1)],
    )
    assert out[0] == "500000000000,500000000000"
    assert len(out) == 9


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
    assert out[0] == "500000000000,500000000000"
    assert len(out) == 9


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
    assert out[0] == "2,1"
    assert len(out) == 9


def test_rate_limiting():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 5, 0)],
        [(0, 0, 0, 1, 10, 2, 0, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
    )
    assert out[0] == "2,2"
    assert len(out) == 9


def test_rate_with_burst():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out[0] == "3,2"
    assert out[5] == "0"
    assert out[6] == "2,0"


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0, 0, 0)], [(0, 0, 0, 1, 0, 0, 0, 1)])
    assert out[0] == "0"
    assert len(out) == 9


def test_global_rebalancing():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0), (0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 1, 0, 0, 1), (0, 0, 0, 1, 1, 0, 0, 1), (1, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out[0] == "1,1,8"
    assert len(out) == 9
    assert out[1] == "2,8"


def test_dynamic_weight_and_credit():
    out = run_case(
        2,
        [5, 5],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 10, 1, 2, 10, 0, 0, 1), (0, 1, 1, 2, 10, 0, 0, 1)],
    )
    assert len(out) == 10


def test_final_totals_and_credits_consistency():
    out = run_case(
        2,
        [6, 6],
        [(0, 0, 4, 11, 0, 0), (0, 0, 1, 6, 0, 0)],
        [
            (0, 5, 0, 4, 11, 0, 0, 1),
            (0, 1, 0, 1, 6, 0, 0, 1),
            (1, 10, 0, 2, 5, 0, 0, 1),
        ],
    )
    assert len(out) == 10
    batch1 = [int(x) for x in out[0].split(",")]
    batch2 = [int(x) for x in out[1].split(",")]
    sub_totals = [int(x) for x in out[3].split(",")]
    assert sub_totals[0] == batch1[0] + batch2[0]
    assert sub_totals[1] == batch1[1] + batch2[1]
    assert sub_totals[2] == batch1[2] + batch2[2]


def test_negative_deallocation():
    out = run_case(
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0, 1), (0, 1, 0, 2, 10, 0, 0, 1)],
    )
    assert len(out) == 10
    b1 = [int(x) for x in out[0].split(",")]
    b2 = [int(x) for x in out[1].split(",")]
    assert all(v >= 0 for v in b1)
    assert all(v <= 0 for v in b2)
    sub_totals = [int(x) for x in out[3].split(",")]
    assert sub_totals[0] == b1[0] + b2[0]
    assert all(t >= 0 for t in sub_totals)


def test_burst_final_consistency():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert len(out) == 9
    assert out[5] == "0"
    assert out[6] == "2,0"


def test_cost_factor():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 20, 0, 0)],
        [(0, 0, 0, 1, 10, 0, 0, 2), (0, 0, 0, 1, 10, 0, 0, 5)],
    )
    assert len(out) == 9
    batch = [int(x) for x in out[0].split(",")]
    assert batch[0] * 2 <= 10
    assert batch[1] * 5 <= 10
    # sub totals at T+1 =2
    sub_totals = [int(x) for x in out[2].split(",")]
    assert sub_totals[0] == batch[0] * 2
    assert sub_totals[1] == batch[1] * 5


def test_priority_aging():
    out = run_case(
        3,
        [2, 2, 2],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 0, 1, 1, 0, 0, 1), (0, 1, 0, 1, 10, 0, 0, 1)],
    )
    assert len(out) == 11
    # T=3, sub weights at T+7=10
    sub_weights = [int(x) for x in out[10].split(",")]
    assert all(w >= 1 for w in sub_weights)
    group_weights = [int(x) for x in out[9].split(",")]
    assert all(w >= 1 for w in group_weights)


def test_zero_load_batch():
    out = run_case(
        2,
        [0, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 1, 10, 0, 0, 1), (0, 1, 1, 1, 10, 0, 0, 1)],
    )
    assert len(out) == 10
    assert out[0] == "0,0"


def test_cap_exhaustion_three_batches():
    out = run_case(
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 3, 10, 0, 0, 1), (0, 1, 1, 3, 10, 0, 0, 1)],
    )
    assert len(out) == 11
    assert out[2] == "0,0"


def test_deterministic():
    _rnd = random
    _rnd.seed(303)
    for _ in range(20):
        T = _rnd.randint(1, 3)
        loads = [_rnd.randint(-5, 20) for _ in range(T)]
        G = _rnd.randint(1, 3)
        groups = [
            (
                _rnd.randint(0, 5),
                _rnd.randint(0, 2),
                _rnd.randint(1, 4),
                _rnd.randint(0, 10),
                _rnd.choice([0, _rnd.randint(1, 5)]),
                _rnd.choice([0, _rnd.randint(1, 3)]),
            )
            for _ in range(G)
        ]
        S = _rnd.randint(1, 6)
        subs = [
            (
                _rnd.randint(0, G - 1),
                _rnd.randint(0, 5),
                _rnd.randint(0, 2),
                _rnd.randint(1, 4),
                _rnd.randint(0, 10),
                _rnd.choice([0, _rnd.randint(1, 5)]),
                _rnd.choice([0, _rnd.randint(1, 3)]),
                _rnd.choice([1, 2, 3]),
            )
            for _ in range(S)
        ]
        a = run_case(T, loads, groups, subs)
        b = run_case(T, loads, groups, subs)
        assert a == b, f"non-deterministic for {T},{loads},{groups},{subs}: {a} vs {b}"


def test_fuzz_invariants():
    _rnd = random
    _rnd.seed(2024)
    for _ in range(30):
        T = _rnd.randint(1, 3)
        loads = [_rnd.randint(-5, 30) for _ in range(T)]
        G = _rnd.randint(1, 3)
        groups = []
        for _ in range(G):
            p = _rnd.randint(0, 10)
            mn = _rnd.randint(0, 3)
            w = _rnd.randint(1, 8)
            c = _rnd.randint(0, 20)
            ra = _rnd.choice([0, 0, _rnd.randint(1, 10)])
            bu = _rnd.choice([0, 0, _rnd.randint(1, 5)])
            groups.append((p, mn, w, c, ra, bu))
        S = _rnd.randint(1, G * 4 + 2)
        subs = []
        for _ in range(S):
            gid = _rnd.randint(0, G - 1)
            p = _rnd.randint(0, 10)
            mn = _rnd.randint(0, 2)
            w = _rnd.randint(1, 6)
            c = _rnd.randint(0, 15)
            ra = _rnd.choice([0, 0, _rnd.randint(1, 10)])
            bu = _rnd.choice([0, 0, _rnd.randint(1, 5)])
            co = _rnd.choice([1, 2, 3])
            subs.append((gid, p, mn, w, c, ra, bu, co))
        out_lines = run_case(T, loads, groups, subs)
        assert len(out_lines) == T + 8
        batch_lines = out_lines[:T]
        caps = [c for _, _, _, c, _, _ in groups]
        sub_caps = [c for _, _, _, _, c, _, _, _ in subs]
        sub_costs = [co for _, _, _, _, _, _, _, co in subs]
        sub_total_cost = [0] * S
        group_total_cost = [0] * G
        for line in batch_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert sub_total_cost[i] + v * sub_costs[i] >= 0
                assert sub_total_cost[i] + v * sub_costs[i] <= sub_caps[i]
            for i, v in enumerate(parts):
                sub_total_cost[i] += v * sub_costs[i]
            for g in range(G):
                gs_cost = sum(
                    parts[i] * sub_costs[i] for i, s in enumerate(subs) if s[0] == g
                )
                group_total_cost[g] += gs_cost

        gt = [int(x) for x in out_lines[T].split(",")] if out_lines[T] else []
        st = [int(x) for x in out_lines[T + 1].split(",")] if out_lines[T + 1] else []
        assert gt == group_total_cost
        assert st == sub_total_cost
