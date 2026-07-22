"""Black-box tests for hierarchical multi-batch allocator with min, priority, credit-decay - balanced hard (20 main + 10 corners)."""

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


def run_case(T, loads, groups, subs):
    lines = [str(T)]
    for ld in loads:
        lines.append(str(ld))
    lines.append(str(len(groups)))
    for p, mn, w, c in groups:
        lines.append(f"{p} {mn} {w} {c}")
    lines.append(str(len(subs)))
    for gid, p, mn, w, c in subs:
        lines.append(f"{gid} {p} {mn} {w} {c}")
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
    return [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""]


CASES = [
    (
        1,
        [16],
        [(10, 0, 5, 10), (5, 0, 3, 10)],
        [(0, 10, 0, 5, 6), (0, 5, 0, 3, 9), (1, 5, 0, 4, 3), (1, 1, 0, 1, 12)],
        ["6,4,3,3"],
    ),
    (
        1,
        [9],
        [(0, 0, 5, 10), (0, 0, 6, 10)],
        [(0, 10, 2, 5, 10), (0, 5, 1, 6, 10), (1, 1, 0, 6, 1)],
        ["4,4,1"],
    ),
    (
        2,
        [6, 6],
        [(0, 0, 4, 11), (0, 0, 1, 6)],
        [(0, 5, 0, 4, 11), (0, 1, 0, 1, 6), (1, 10, 0, 2, 5)],
        ["4,1,1", "4,1,1"],
    ),
    (1, [5], [(0, 0, 1, 10)], [(0, 10, 10, 1, 2)], ["2"]),
    (1, [0], [(1, 0, 1, 5)], [(0, 1, 0, 1, 5)], ["0"]),
    (
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000)],
        [(0, 0, 0, 1, 500000000000), (0, 0, 0, 1, 500000000000)],
        ["500000000000,500000000000"],
    ),
    (
        2,
        [0, 9],
        [(3, 1, 7, 5), (5, 0, 7, 8)],
        [(0, 5, 2, 6, 1), (1, 9, 2, 2, 2), (0, 1, 0, 3, 5)],
        ["0,0,0", "1,2,4"],
    ),
    (
        1,
        [14],
        [(0, 2, 7, 0), (0, 2, 3, 18)],
        [(1, 1, 2, 4, 1), (0, 6, 0, 5, 9), (1, 0, 0, 1, 7)],
        ["1,0,7"],
    ),
    (
        1,
        [17],
        [(2, 1, 7, 16), (2, 0, 3, 9), (0, 0, 4, 11)],
        [
            (1, 2, 1, 3, 0),
            (0, 0, 2, 4, 1),
            (1, 2, 0, 6, 5),
            (0, 10, 2, 1, 7),
            (2, 0, 0, 6, 7),
            (1, 9, 1, 5, 3),
            (1, 10, 2, 1, 8),
            (0, 1, 1, 2, 5),
            (0, 3, 0, 2, 4),
        ],
        ["0,1,0,3,4,1,2,4,2"],
    ),
    (1, [17], [(8, 2, 1, 4)], [(0, 2, 2, 1, 4)], ["4"]),
    (
        3,
        [12, 2, 14],
        [(2, 0, 3, 0), (5, 1, 3, 12)],
        [(0, 9, 2, 5, 3), (1, 8, 2, 4, 5), (0, 1, 2, 4, 11)],
        ["0,5,0", "0,0,0", "0,0,0"],
    ),
    (
        1,
        [20],
        [(9, 0, 3, 16), (3, 1, 5, 4), (8, 2, 1, 8)],
        [
            (0, 5, 2, 5, 9),
            (0, 10, 0, 2, 5),
            (2, 10, 0, 3, 11),
            (1, 10, 1, 4, 4),
            (1, 9, 1, 3, 11),
            (1, 7, 0, 3, 7),
            (0, 3, 1, 2, 0),
        ],
        ["9,2,5,2,2,0,0"],
    ),
    (
        3,
        [20, 2, 0],
        [(9, 0, 7, 15)],
        [(0, 5, 0, 3, 11), (0, 6, 0, 5, 4)],
        ["11,4", "0,0", "0,0"],
    ),
    (2, [1, 13], [(2, 0, 3, 9)], [(0, 6, 0, 1, 2), (0, 8, 0, 3, 6)], ["0,1", "2,5"]),
    (
        2,
        [1, 7],
        [(7, 0, 4, 8)],
        [
            (0, 2, 2, 3, 0),
            (0, 1, 2, 2, 8),
            (0, 8, 0, 5, 2),
            (0, 9, 0, 4, 6),
            (0, 0, 1, 6, 0),
        ],
        ["0,1,0,0,0", "0,2,2,3,0"],
    ),
    (
        2,
        [11, 17],
        [(9, 0, 6, 15), (7, 0, 6, 10), (6, 1, 3, 12)],
        [
            (2, 0, 2, 1, 7),
            (0, 1, 2, 2, 0),
            (0, 4, 0, 5, 1),
            (1, 7, 1, 1, 6),
            (0, 3, 1, 6, 12),
            (0, 6, 2, 5, 3),
            (2, 7, 0, 2, 8),
            (2, 8, 2, 2, 2),
            (2, 7, 0, 6, 3),
            (0, 1, 2, 1, 1),
        ],
        ["1,0,0,4,1,2,0,2,0,1", "2,0,1,2,7,1,1,0,3,0"],
    ),
    (
        2,
        [18, 7],
        [(4, 0, 8, 11)],
        [(0, 8, 0, 6, 7), (0, 8, 0, 5, 8), (0, 3, 1, 4, 10), (0, 6, 1, 1, 5)],
        ["4,3,3,1", "0,0,0,0"],
    ),
    (
        1,
        [14],
        [(6, 2, 2, 5), (3, 2, 7, 8)],
        [(0, 8, 2, 6, 4), (0, 7, 1, 6, 10), (0, 10, 1, 1, 11)],
        ["3,1,1"],
    ),
    (
        2,
        [11, 6],
        [(4, 0, 2, 6), (4, 2, 1, 0), (2, 2, 3, 10)],
        [(0, 9, 2, 3, 8), (1, 0, 0, 4, 6), (2, 9, 1, 6, 10), (2, 10, 1, 5, 8)],
        ["4,0,4,3", "2,0,2,1"],
    ),
    (
        2,
        [16, 9],
        [(1, 2, 8, 15), (10, 0, 7, 1), (1, 2, 2, 8)],
        [
            (1, 6, 2, 4, 7),
            (0, 6, 2, 6, 1),
            (2, 6, 0, 5, 0),
            (1, 2, 2, 1, 5),
            (0, 10, 1, 2, 6),
            (0, 4, 1, 6, 9),
            (1, 6, 0, 3, 3),
            (2, 9, 1, 1, 5),
            (2, 6, 1, 2, 9),
            (1, 9, 1, 5, 2),
            (1, 7, 2, 1, 7),
        ],
        ["0,1,0,0,3,7,0,2,2,1,0", "0,0,0,0,2,2,0,2,2,0,0"],
    ),
]


@pytest.mark.parametrize("T,loads,groups,subs,expected", CASES)
def test_allocation(T, loads, groups, subs, expected):
    out_lines = run_case(T, loads, groups, subs)
    assert len(out_lines) == len(expected), (
        f"expected {len(expected)} lines, got {len(out_lines)}: {out_lines}"
    )
    for got, exp in zip(out_lines, expected):
        assert got == exp


def test_conservation():
    for T, loads, groups, subs, _ in CASES:
        out_lines = run_case(T, loads, groups, subs)
        G = len(groups)
        S = len(subs)
        group_caps = [c for _, _, _, c in groups]
        sub_caps = [c for _, _, _, _, c in subs]
        group_tot = [0] * G
        sub_tot = [0] * S
        for line in out_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert 0 <= v
                assert sub_tot[i] + v <= sub_caps[i]
            sum_member_rem = [0] * G
            for s_idx, (gid, _, _, _, _) in enumerate(subs):
                if 0 <= gid < G:
                    sum_member_rem[gid] += sub_caps[s_idx] - sub_tot[s_idx]
            eff_rem = [
                min(group_caps[g] - group_tot[g], sum_member_rem[g]) for g in range(G)
            ]
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _) in enumerate(subs) if gid == g
                )
                assert gs <= eff_rem[g]
            for i, v in enumerate(parts):
                sub_tot[i] += v
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _) in enumerate(subs) if gid == g
                )
                group_tot[g] += gs


def test_min_exceeds_cap():
    out = run_case(1, [5], [(0, 0, 1, 10)], [(0, 10, 10, 1, 2)])
    assert out == ["2"]


def test_group_no_members():
    out = run_case(1, [10], [(0, 0, 5, 10), (0, 0, 5, 10)], [(0, 0, 0, 1, 5)])
    assert out == ["5"]


def test_invalid_gid():
    out = run_case(1, [10], [(0, 0, 1, 10)], [(0, 0, 0, 1, 5), (99, 0, 0, 1, 5)])
    assert out == ["5,0"]


def test_blank_lines_and_spaces():
    raw = """
1
16

2
10 0 5 10
5 0 3 10
4
0 10 0 5 6
  0 5 0 3 9
1 5 0 4 3
1 1 0 1 12

"""
    out = run_case_raw(raw)
    assert out == ["6,4,3,3"]


def test_large_numbers():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000)],
        [(0, 0, 0, 1, 500000000000), (0, 0, 0, 1, 500000000000)],
    )
    assert out == ["500000000000,500000000000"]


def test_large_weight_overflow():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000)],
        [
            (0, 0, 0, 1000000000000, 500000000000),
            (0, 0, 0, 1000000000000, 500000000000),
        ],
    )
    assert out == ["500000000000,500000000000"]


def test_large_credit_overflow():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10)],
        [(0, 0, 0, 4000000000000000000, 10), (0, 0, 0, 4000000000000000000, 10)],
    )
    assert out == ["2,1"]


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0)], [(0, 0, 0, 1, 0)])
    assert out == ["0"]


def test_deterministic():
    a = run_case(
        1,
        [16],
        [(10, 0, 5, 10), (5, 0, 3, 10)],
        [(0, 10, 0, 5, 6), (0, 5, 0, 3, 9), (1, 5, 0, 4, 3), (1, 1, 0, 1, 12)],
    )
    b = run_case(
        1,
        [16],
        [(10, 0, 5, 10), (5, 0, 3, 10)],
        [(0, 10, 0, 5, 6), (0, 5, 0, 3, 9), (1, 5, 0, 4, 3), (1, 1, 0, 1, 12)],
    )
    assert a == b == ["6,4,3,3"]
