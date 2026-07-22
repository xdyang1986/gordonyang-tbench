"""Black-box tests for hierarchical multi-batch allocator with min, priority, rate limits, credit-decay - balanced hard (20 main + 8 corners)."""

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
    for p, mn, w, c, ra in groups:
        lines.append(f"{p} {mn} {w} {c} {ra}")
    lines.append(str(len(subs)))
    for gid, p, mn, w, c, ra in subs:
        lines.append(f"{gid} {p} {mn} {w} {c} {ra}")
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
        [(10, 0, 5, 10, 0), (5, 0, 3, 10, 0)],
        [
            (0, 10, 0, 5, 6, 0),
            (0, 5, 0, 3, 9, 0),
            (1, 5, 0, 4, 3, 0),
            (1, 1, 0, 1, 12, 0),
        ],
        ["6,4,3,3"],
    ),
    (
        1,
        [9],
        [(0, 0, 5, 10, 0), (0, 0, 6, 10, 0)],
        [(0, 10, 2, 5, 10, 2), (0, 5, 1, 6, 10, 10), (1, 1, 0, 6, 1, 0)],
        ["2,6,1"],
    ),
    (1, [5], [(0, 0, 1, 10, 0)], [(0, 10, 10, 1, 2, 0)], ["2"]),
    (
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0)],
        [(0, 0, 0, 1, 500000000000, 0), (0, 0, 0, 1, 500000000000, 0)],
        ["500000000000,500000000000"],
    ),
    (
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0),
            (0, 0, 0, 1000000000000, 500000000000, 0),
        ],
        ["500000000000,500000000000"],
    ),
    (
        1,
        [3],
        [(0, 0, 1, 10, 0)],
        [(0, 0, 0, 4000000000000000000, 10, 0), (0, 0, 0, 4000000000000000000, 10, 0)],
        ["2,1"],
    ),
    (
        3,
        [19, 7, 17],
        [(6, 1, 7, 5, 0)],
        [(0, 8, 2, 5, 11, 5), (0, 6, 1, 5, 11, 0)],
        ["3,2", "0,0", "0,0"],
    ),
    (
        3,
        [16, 17, 23],
        [(2, 0, 3, 0, 0), (1, 2, 1, 20, 0)],
        [(0, 4, 0, 2, 2, 4)],
        ["0", "0", "0"],
    ),
    (2, [18, 9], [(8, 0, 6, 4, 6)], [(0, 7, 1, 1, 11, 0)], ["4", "0"]),
    (
        3,
        [7, 23, 21],
        [(4, 0, 2, 1, 7), (8, 0, 6, 6, 0)],
        [(1, 7, 1, 1, 9, 2), (0, 2, 2, 1, 0, 0)],
        ["2,0", "2,0", "2,0"],
    ),
    (1, [1], [(3, 1, 7, 10, 10)], [(0, 4, 1, 2, 0, 0), (0, 10, 1, 6, 6, 7)], ["0,1"]),
    (
        1,
        [0],
        [(10, 1, 3, 19, 0)],
        [
            (0, 1, 0, 6, 13, 6),
            (0, 8, 2, 6, 14, 0),
            (0, 7, 1, 1, 10, 7),
            (0, 1, 2, 5, 8, 0),
            (0, 5, 2, 4, 2, 0),
        ],
        ["0,0,0,0,0"],
    ),
    (
        3,
        [13, 11, 3],
        [(4, 1, 5, 14, 0)],
        [(0, 1, 1, 2, 4, 8), (0, 7, 2, 5, 15, 0)],
        ["3,10", "0,1", "0,0"],
    ),
    (2, [21, 19], [(2, 2, 2, 10, 9)], [(0, 5, 1, 2, 7, 5)], ["5", "2"]),
    (
        2,
        [9, 6],
        [(7, 0, 1, 7, 0), (6, 1, 6, 16, 0), (10, 2, 2, 4, 0)],
        [
            (1, 10, 1, 6, 4, 1),
            (1, 0, 2, 2, 15, 4),
            (2, 9, 0, 2, 9, 4),
            (1, 9, 2, 4, 3, 0),
            (0, 10, 0, 2, 5, 0),
            (1, 9, 2, 2, 9, 5),
            (2, 8, 2, 5, 8, 0),
            (2, 5, 2, 3, 11, 4),
            (1, 0, 1, 5, 13, 0),
            (1, 4, 0, 1, 0, 0),
        ],
        ["1,1,0,2,0,2,2,1,0,0", "1,0,0,1,1,2,1,0,0,0"],
    ),
    (
        2,
        [3, 9],
        [(4, 0, 2, 17, 0), (3, 2, 2, 19, 1)],
        [
            (0, 0, 0, 4, 12, 0),
            (0, 1, 0, 6, 13, 0),
            (0, 4, 1, 1, 1, 0),
            (0, 5, 2, 1, 15, 2),
            (0, 8, 0, 5, 13, 0),
            (1, 7, 0, 3, 6, 0),
        ],
        ["0,0,0,2,0,1", "1,3,1,2,1,1"],
    ),
    (1, [18], [(1, 1, 4, 6, 5), (8, 1, 3, 3, 3)], [(1, 5, 0, 2, 11, 0)], ["3"]),
    (
        2,
        [7, 3],
        [(4, 0, 4, 15, 0)],
        [
            (0, 2, 2, 4, 13, 4),
            (0, 8, 1, 2, 4, 2),
            (0, 3, 1, 2, 5, 5),
            (0, 9, 0, 5, 0, 4),
            (0, 7, 1, 6, 12, 0),
        ],
        ["3,1,1,0,2", "0,1,1,0,1"],
    ),
    (
        2,
        [7, 24],
        [(3, 0, 2, 20, 0), (4, 1, 8, 13, 10)],
        [
            (0, 4, 0, 5, 10, 0),
            (0, 0, 1, 2, 7, 6),
            (0, 6, 1, 1, 13, 0),
            (1, 10, 2, 1, 5, 0),
            (1, 9, 0, 4, 1, 0),
            (1, 9, 2, 4, 10, 6),
            (1, 2, 0, 5, 0, 0),
        ],
        ["0,0,1,2,1,3,0", "10,5,2,3,0,4,0"],
    ),
    (
        1,
        [23],
        [(0, 1, 1, 13, 0), (4, 2, 3, 14, 6), (2, 1, 6, 8, 4)],
        [(0, 10, 0, 4, 7, 0), (2, 1, 1, 3, 11, 0)],
        ["7,4"],
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
        # groups: (prio, min, weight, cap, rate) 5 fields, subs: (gid, prio, min, weight, cap, rate) 6 fields
        group_caps = [g[3] for g in groups]
        sub_caps = [s[4] for s in subs]
        group_tot = [0] * G
        sub_tot = [0] * S
        for line in out_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert 0 <= v
                assert sub_tot[i] + v <= sub_caps[i]
            sum_member_rem = [0] * G
            for s_idx, (gid, _, _, _, _, _) in enumerate(subs):
                if 0 <= gid < G:
                    sum_member_rem[gid] += sub_caps[s_idx] - sub_tot[s_idx]
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _, _) in enumerate(subs) if gid == g
                )
                assert gs <= max(0, group_caps[g] - group_tot[g])
            for i, v in enumerate(parts):
                sub_tot[i] += v
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _, _) in enumerate(subs) if gid == g
                )
                group_tot[g] += gs


def test_min_exceeds_cap():
    out = run_case(1, [5], [(0, 0, 1, 10, 0)], [(0, 10, 10, 1, 2, 0)])
    assert out == ["2"]


def test_group_no_members():
    out = run_case(1, [10], [(0, 0, 5, 10, 0), (0, 0, 5, 10, 0)], [(0, 0, 0, 1, 5, 0)])
    assert out == ["5"]


def test_invalid_gid():
    out = run_case(
        1, [10], [(0, 0, 1, 10, 0)], [(0, 0, 0, 1, 5, 0), (99, 0, 0, 1, 5, 0)]
    )
    assert out == ["5,0"]


def test_blank_lines_and_spaces():
    raw = """
1
16

2
10 0 5 10 0
5 0 3 10 0
4
0 10 0 5 6 0
  0 5 0 3 9 0
1 5 0 4 3 0
1 1 0 1 12 0

"""
    out = run_case_raw(raw)
    assert out == ["6,4,3,3"]


def test_large_numbers():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0)],
        [(0, 0, 0, 1, 500000000000, 0), (0, 0, 0, 1, 500000000000, 0)],
    )
    assert out == ["500000000000,500000000000"]


def test_large_weight_overflow():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0),
            (0, 0, 0, 1000000000000, 500000000000, 0),
        ],
    )
    assert out == ["500000000000,500000000000"]


def test_large_credit_overflow():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0)],
        [(0, 0, 0, 4000000000000000000, 10, 0), (0, 0, 0, 4000000000000000000, 10, 0)],
    )
    assert out == ["2,1"]


def test_rate_limiting():
    # per-batch rate limiting: group rate 5, sub rate 2, load 10 in group with 2 subs cap 10 each, rate 2 each, sum 4 but group rate 5 -> group gets 5, subs limited to 2 each => 2,2 and 1 leftover? Actually with rate 2 each, sum member eff per batch =4, group rate 5, eff group rem = min(10,4,5)=4, so group gets 4, subs 2,2
    out = run_case(
        1, [10], [(0, 0, 1, 10, 5)], [(0, 0, 0, 1, 10, 2), (0, 0, 0, 1, 10, 2)]
    )
    assert out == ["2,2"]


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0, 0)], [(0, 0, 0, 1, 0, 0)])
    assert out == ["0"]


def test_deterministic():
    a = run_case(
        1,
        [16],
        [(10, 0, 5, 10, 0), (5, 0, 3, 10, 0)],
        [
            (0, 10, 0, 5, 6, 0),
            (0, 5, 0, 3, 9, 0),
            (1, 5, 0, 4, 3, 0),
            (1, 1, 0, 1, 12, 0),
        ],
    )
    b = run_case(
        1,
        [16],
        [(10, 0, 5, 10, 0), (5, 0, 3, 10, 0)],
        [
            (0, 10, 0, 5, 6, 0),
            (0, 5, 0, 3, 9, 0),
            (1, 5, 0, 4, 3, 0),
            (1, 1, 0, 1, 12, 0),
        ],
    )
    assert a == b == ["6,4,3,3"]
