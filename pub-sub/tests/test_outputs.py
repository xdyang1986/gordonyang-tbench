"""Black-box tests for hierarchical multi-batch allocator with min, priority, credit-decay."""

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
    # input format
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


# (T, loads, groups[(prio,min,w,cap)], subs[(gid,prio,min,w,cap)], expected_lines[List[str]])
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
        1,
        [6],
        [(0, 0, 4, 11), (0, 0, 1, 6)],
        [(0, 5, 0, 4, 11), (0, 1, 0, 1, 6), (1, 10, 0, 2, 5)],
        ["4,1,1"],
    ),
    (
        2,
        [6, 6],
        [(0, 0, 4, 11), (0, 0, 1, 6)],
        [(0, 5, 0, 4, 11), (0, 1, 0, 1, 6), (1, 10, 0, 2, 5)],
        ["4,1,1", "4,1,1"],
    ),
    (1, [9], [(1, 0, 1, 10)], [(0, 5, 0, 1, 5)], ["5"]),
    (1, [10], [(1, 0, 3, 100)], [(0, 5, 0, 3, 100), (0, 1, 0, 1, 100)], ["8,2"]),
    (
        3,
        [14, 14, 13],
        [(10, 0, 5, 16), (1, 2, 4, 3)],
        [(1, 7, 0, 3, 2), (0, 3, 0, 4, 8)],
        ["2,8", "0,0", "0,0"],
    ),
    (
        3,
        [17, 20, 18],
        [(10, 3, 3, 2), (9, 0, 3, 9), (10, 3, 2, 11)],
        [(0, 7, 0, 3, 5), (0, 5, 1, 2, 11), (2, 6, 0, 4, 7)],
        ["1,1,7", "0,0,0", "0,0,0"],
    ),
    (
        3,
        [17, 8, 14],
        [(1, 1, 5, 19), (8, 2, 6, 0)],
        [
            (0, 9, 2, 2, 1),
            (1, 6, 1, 2, 2),
            (1, 4, 0, 4, 7),
            (0, 2, 1, 4, 5),
            (0, 10, 1, 3, 1),
            (0, 9, 2, 1, 2),
            (1, 6, 1, 4, 2),
            (0, 4, 0, 1, 3),
        ],
        ["1,0,0,5,1,2,0,3", "0,0,0,0,0,0,0,0", "0,0,0,0,0,0,0,0"],
    ),
    (
        3,
        [0, 12, 20],
        [(3, 0, 1, 1)],
        [(0, 2, 1, 5, 0), (0, 3, 0, 5, 6), (0, 3, 2, 5, 7), (0, 5, 0, 4, 12)],
        ["0,0,0,0", "0,0,1,0", "0,0,0,0"],
    ),
    (
        3,
        [11, 5, 2],
        [(0, 2, 4, 1), (6, 1, 6, 7), (1, 3, 5, 16)],
        [
            (2, 7, 1, 4, 3),
            (2, 0, 1, 5, 7),
            (1, 4, 1, 3, 8),
            (1, 1, 1, 1, 10),
            (1, 2, 0, 5, 0),
            (0, 9, 0, 1, 6),
            (1, 4, 2, 5, 1),
            (2, 5, 1, 3, 12),
            (2, 9, 0, 1, 2),
            (1, 3, 2, 4, 10),
            (0, 10, 1, 4, 12),
        ],
        ["2,2,1,1,0,0,1,1,0,2,1", "1,1,1,0,0,0,0,1,0,1,0", "0,1,0,0,0,0,0,1,0,0,0"],
    ),
    (
        2,
        [11, 11],
        [(6, 1, 3, 13), (0, 0, 6, 13), (7, 1, 1, 16)],
        [
            (2, 6, 2, 5, 8),
            (0, 4, 0, 4, 10),
            (1, 5, 2, 1, 9),
            (1, 5, 0, 2, 11),
            (1, 7, 0, 5, 8),
            (1, 4, 2, 4, 12),
            (1, 0, 1, 1, 9),
            (2, 0, 2, 3, 2),
            (0, 4, 1, 3, 10),
            (1, 0, 2, 2, 12),
            (0, 0, 0, 1, 10),
        ],
        ["1,2,2,0,0,2,1,0,2,1,0", "2,1,2,0,0,2,1,0,2,1,0"],
    ),
    (
        1,
        [11],
        [(1, 2, 1, 16), (7, 3, 5, 20), (5, 3, 6, 15)],
        [(2, 1, 1, 4, 5), (2, 3, 2, 2, 6), (0, 2, 1, 3, 1), (0, 10, 2, 2, 2)],
        ["5,4,0,2"],
    ),
    (
        2,
        [15, 5],
        [(8, 2, 6, 16), (3, 2, 1, 4), (9, 3, 1, 2)],
        [
            (0, 9, 0, 2, 9),
            (0, 2, 2, 3, 8),
            (0, 2, 1, 4, 1),
            (2, 8, 0, 3, 2),
            (1, 10, 2, 4, 10),
            (0, 2, 0, 2, 2),
        ],
        ["2,5,1,2,3,2", "1,3,0,0,1,0"],
    ),
    (
        1,
        [0],
        [(6, 0, 6, 6)],
        [(0, 4, 1, 1, 7), (0, 10, 0, 2, 2), (0, 7, 0, 5, 11), (0, 0, 0, 3, 5)],
        ["0,0,0,0"],
    ),
    (
        3,
        [2, 12, 20],
        [(9, 3, 6, 19)],
        [(0, 7, 2, 5, 12), (0, 6, 2, 1, 11)],
        ["2,0", "7,5", "3,2"],
    ),
    (
        3,
        [10, 16, 18],
        [(7, 0, 5, 4)],
        [(0, 4, 2, 4, 7), (0, 3, 1, 2, 10), (0, 4, 2, 3, 6), (0, 0, 1, 4, 8)],
        ["2,0,2,0", "0,0,0,0", "0,0,0,0"],
    ),
    (
        3,
        [18, 4, 15],
        [(4, 2, 3, 19), (7, 2, 1, 10)],
        [
            (0, 2, 1, 4, 5),
            (0, 6, 0, 1, 2),
            (1, 3, 0, 5, 5),
            (1, 0, 0, 2, 8),
            (0, 2, 0, 4, 4),
            (0, 4, 2, 3, 3),
            (0, 10, 2, 1, 10),
        ],
        ["5,0,4,1,3,3,2", "0,0,1,1,0,0,2", "0,1,0,3,1,0,2"],
    ),
    (
        2,
        [20, 7],
        [(0, 0, 1, 17), (5, 2, 5, 14), (5, 2, 6, 20)],
        [
            (1, 7, 0, 5, 9),
            (1, 6, 1, 1, 3),
            (1, 2, 0, 4, 7),
            (1, 5, 1, 1, 11),
            (1, 2, 1, 5, 10),
        ],
        ["4,1,3,1,5", "0,0,0,0,0"],
    ),
    (
        3,
        [16, 15, 10],
        [(7, 3, 6, 0), (1, 3, 6, 0)],
        [(1, 6, 1, 4, 7), (1, 2, 2, 4, 5)],
        ["0,0", "0,0", "0,0"],
    ),
    (
        1,
        [18],
        [(2, 0, 4, 6), (5, 2, 3, 18), (5, 1, 1, 15)],
        [
            (0, 7, 1, 2, 0),
            (1, 7, 0, 4, 11),
            (0, 4, 0, 3, 0),
            (0, 7, 2, 3, 3),
            (0, 5, 1, 2, 11),
            (2, 4, 0, 5, 3),
            (1, 0, 1, 2, 1),
            (0, 10, 2, 3, 10),
            (2, 6, 2, 1, 11),
            (0, 2, 1, 3, 1),
            (0, 5, 1, 2, 5),
        ],
        ["0,8,0,2,1,1,1,2,2,0,1"],
    ),
    (
        1,
        [5],
        [(2, 2, 4, 5)],
        [(0, 1, 2, 1, 3), (0, 10, 0, 1, 3), (0, 6, 2, 3, 2)],
        ["3,0,2"],
    ),
    (
        3,
        [8, 15, 2],
        [(5, 2, 6, 18), (3, 0, 5, 12)],
        [
            (1, 6, 0, 4, 1),
            (0, 7, 0, 1, 6),
            (0, 7, 2, 3, 9),
            (0, 0, 0, 5, 6),
            (0, 5, 1, 5, 5),
            (1, 6, 0, 2, 0),
            (1, 4, 2, 4, 9),
        ],
        ["0,0,3,1,2,0,2", "1,2,3,2,3,0,4", "0,0,2,0,0,0,0"],
    ),
    (
        1,
        [14],
        [(0, 1, 2, 11)],
        [(0, 10, 1, 5, 0), (0, 0, 0, 2, 3), (0, 9, 0, 4, 3), (0, 2, 1, 3, 0)],
        ["0,3,3,0"],
    ),
    (
        2,
        [14, 19],
        [(8, 2, 3, 18), (9, 0, 5, 9)],
        [
            (1, 4, 1, 2, 8),
            (1, 9, 2, 4, 6),
            (1, 2, 2, 1, 0),
            (0, 7, 0, 1, 8),
            (0, 9, 2, 4, 0),
        ],
        ["2,6,0,6,0", "1,0,0,2,0"],
    ),
    (
        1,
        [20],
        [(2, 0, 1, 17), (6, 0, 3, 14)],
        [(1, 5, 2, 4, 6), (1, 9, 1, 4, 8), (0, 2, 1, 1, 2)],
        ["6,8,2"],
    ),
    (
        1,
        [3],
        [(3, 3, 1, 7), (9, 3, 5, 10), (0, 2, 4, 14)],
        [
            (0, 8, 2, 2, 8),
            (1, 9, 0, 4, 12),
            (0, 9, 2, 4, 6),
            (0, 2, 1, 1, 1),
            (1, 9, 0, 3, 3),
            (0, 2, 1, 3, 4),
            (1, 8, 0, 5, 6),
            (0, 7, 1, 4, 1),
            (2, 5, 0, 4, 8),
            (2, 1, 2, 4, 9),
        ],
        ["0,1,0,0,1,0,1,0,0,0"],
    ),
    (
        1,
        [19],
        [(4, 3, 2, 8), (3, 3, 5, 11), (1, 0, 4, 15)],
        [
            (1, 4, 1, 1, 2),
            (0, 3, 0, 5, 4),
            (0, 7, 1, 3, 12),
            (2, 0, 0, 1, 4),
            (1, 0, 0, 3, 12),
            (0, 0, 0, 1, 8),
            (2, 6, 2, 4, 8),
            (1, 8, 2, 3, 4),
        ],
        ["2,3,2,0,3,0,5,4"],
    ),
    (
        2,
        [17, 10],
        [(7, 3, 2, 13), (8, 0, 4, 10)],
        [(1, 10, 0, 4, 6), (1, 6, 1, 4, 10), (1, 0, 2, 2, 11)],
        ["3,4,3", "0,0,0"],
    ),
    (
        3,
        [5, 17, 19],
        [(3, 0, 1, 17), (6, 1, 2, 10), (5, 0, 3, 8)],
        [(0, 4, 1, 3, 11), (0, 8, 0, 4, 10)],
        ["2,3", "6,6", "0,0"],
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
    for T, loads, groups, subs, expected in CASES:
        out_lines = run_case(T, loads, groups, subs)
        # simulate totals to check caps
        G = len(groups)
        S = len(subs)
        group_caps = [c for _, _, _, c in groups]
        sub_caps = [c for _, _, _, _, c in subs]
        # cumulative totals
        group_tot = [0] * G
        sub_tot = [0] * S
        for batch_idx, line in enumerate(out_lines):
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            # per sub cap
            for i, v in enumerate(parts):
                assert 0 <= v
                assert sub_tot[i] + v <= sub_caps[i]
            # per group effective cap
            sum_member_rem = [0] * G
            for s_idx, (gid, _, _, _, _) in enumerate(subs):
                if 0 <= gid < G:
                    sum_member_rem[gid] += sub_caps[s_idx] - sub_tot[s_idx]
            eff_rem = [
                min(group_caps[g] - group_tot[g], sum_member_rem[g]) for g in range(G)
            ]
            # group batch sum
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _) in enumerate(subs) if gid == g
                )
                assert gs <= eff_rem[g]
            # update totals
            for i, v in enumerate(parts):
                sub_tot[i] += v
            # group totals
            # need to recompute group batch from subs
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _) in enumerate(subs) if gid == g
                )
                group_tot[g] += gs


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
