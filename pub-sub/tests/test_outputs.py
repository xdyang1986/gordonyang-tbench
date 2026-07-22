"""Black-box tests for hierarchical allocator with min, priority, credit-decay - balanced hard (20 main + 6 corners)."""

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


def run_case(load, groups, subs):
    lines = [str(load), str(len(groups))]
    for p, mn, w, c in groups:
        lines.append(f"{p} {mn} {w} {c}")
    lines.append(str(len(subs)))
    for gid, p, mn, w, c in subs:
        lines.append(f"{gid} {p} {mn} {w} {c}")
    inp = "\n".join(lines) + "\n"
    proc = subprocess.run([BIN], input=inp, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\ninput:\n{inp}"
    return proc.stdout.strip()


def run_case_raw(raw):
    proc = subprocess.run([BIN], input=raw, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}\nraw:\n{raw}"
    return proc.stdout.strip()


CASES = [
    (
        16,
        [(10, 0, 5, 10), (5, 0, 3, 10)],
        [(0, 10, 0, 5, 6), (0, 5, 0, 3, 9), (1, 5, 0, 4, 3), (1, 1, 0, 1, 12)],
        "6,4,3,3",
    ),
    (
        9,
        [(0, 0, 5, 10), (0, 0, 6, 10)],
        [(0, 10, 2, 5, 10), (0, 5, 1, 6, 10), (1, 1, 0, 6, 1)],
        "4,4,1",
    ),
    (
        6,
        [(0, 0, 4, 11), (0, 0, 1, 6)],
        [(0, 5, 0, 4, 11), (0, 1, 0, 1, 6), (1, 10, 0, 2, 5)],
        "4,1,1",
    ),
    (5, [(0, 0, 1, 10)], [(0, 10, 10, 1, 2)], "2"),
    (10, [(1, 0, 3, 100)], [(0, 5, 0, 3, 100), (0, 1, 0, 1, 100)], "8,2"),
    (7, [(0, 0, 1, 10)], [(0, 1, 2, 1, 2), (0, 1, 2, 1, 2), (0, 1, 2, 1, 2)], "2,2,2"),
    (
        11,
        [(10, 3, 7, 6), (3, 3, 1, 13), (7, 3, 1, 16), (5, 1, 4, 15)],
        [
            (1, 6, 1, 4, 2),
            (3, 7, 2, 6, 11),
            (0, 8, 0, 3, 7),
            (3, 7, 1, 1, 2),
            (0, 0, 1, 3, 0),
            (1, 3, 0, 1, 5),
            (1, 3, 2, 6, 3),
            (3, 3, 1, 6, 5),
            (1, 4, 1, 3, 7),
            (1, 5, 0, 5, 3),
            (3, 6, 2, 5, 5),
        ],
        "1,2,6,0,0,0,1,0,1,0,0",
    ),
    (
        2,
        [(2, 1, 6, 11), (1, 1, 6, 0), (1, 3, 6, 3), (4, 1, 5, 19)],
        [
            (3, 4, 2, 6, 2),
            (1, 10, 2, 4, 2),
            (3, 0, 0, 2, 5),
            (3, 7, 2, 5, 2),
            (0, 7, 0, 2, 9),
            (1, 10, 0, 1, 12),
            (0, 7, 1, 1, 5),
            (1, 8, 2, 6, 12),
            (0, 3, 1, 4, 0),
            (2, 6, 1, 2, 12),
            (1, 10, 1, 5, 2),
        ],
        "0,0,0,1,0,0,1,0,0,0,0",
    ),
    (
        19,
        [(7, 3, 4, 2), (8, 1, 1, 1)],
        [
            (0, 10, 1, 5, 10),
            (1, 6, 2, 4, 5),
            (1, 2, 0, 2, 1),
            (1, 7, 1, 5, 8),
            (0, 4, 0, 6, 4),
            (1, 8, 0, 5, 11),
            (1, 3, 1, 5, 6),
        ],
        "1,0,0,1,1,0,0",
    ),
    (
        1,
        [(2, 0, 4, 15), (5, 2, 4, 19), (9, 1, 4, 2), (5, 2, 4, 6)],
        [
            (1, 2, 1, 1, 11),
            (0, 5, 1, 3, 3),
            (1, 2, 2, 4, 0),
            (0, 0, 1, 6, 6),
            (2, 2, 2, 1, 6),
        ],
        "0,0,0,0,1",
    ),
    (
        12,
        [(5, 1, 5, 8), (8, 3, 6, 5), (3, 2, 4, 13), (10, 2, 8, 20)],
        [
            (1, 4, 0, 1, 10),
            (3, 3, 0, 5, 0),
            (2, 5, 2, 2, 1),
            (1, 5, 1, 2, 5),
            (2, 2, 1, 5, 12),
            (3, 5, 1, 4, 9),
            (2, 8, 2, 2, 1),
            (0, 5, 0, 6, 12),
            (1, 10, 2, 2, 11),
            (0, 3, 1, 5, 4),
            (3, 3, 0, 1, 1),
            (3, 1, 0, 6, 5),
        ],
        "0,0,1,2,0,2,1,1,2,1,0,2",
    ),
    (
        19,
        [(8, 2, 4, 8), (0, 2, 4, 1), (3, 2, 7, 16)],
        [(2, 5, 0, 2, 5), (2, 10, 1, 1, 8), (0, 0, 0, 2, 9), (0, 6, 1, 5, 9)],
        "5,7,1,6",
    ),
    (13, [(10, 2, 5, 15), (4, 1, 1, 10), (5, 1, 6, 15)], [(1, 0, 2, 5, 8)], "8"),
    (
        30,
        [(7, 1, 4, 4), (6, 2, 3, 16), (5, 0, 2, 8), (6, 0, 6, 5)],
        [(1, 8, 2, 6, 1), (0, 5, 2, 1, 1), (0, 8, 1, 6, 6)],
        "1,1,3",
    ),
    (
        0,
        [(6, 1, 2, 13), (10, 2, 1, 0), (8, 3, 4, 12)],
        [(0, 9, 0, 1, 8), (1, 6, 0, 1, 5), (1, 8, 2, 2, 9), (1, 4, 0, 3, 11)],
        "0,0,0,0",
    ),
    (
        19,
        [(4, 0, 4, 18), (5, 3, 4, 3)],
        [
            (0, 1, 1, 5, 6),
            (1, 0, 1, 5, 4),
            (0, 10, 0, 3, 10),
            (0, 0, 0, 5, 6),
            (0, 4, 1, 3, 4),
        ],
        "6,3,2,5,3",
    ),
    (
        26,
        [(6, 0, 8, 1), (6, 0, 3, 8), (2, 2, 7, 14), (6, 2, 6, 5)],
        [
            (3, 0, 1, 6, 10),
            (1, 6, 2, 5, 1),
            (0, 4, 2, 4, 3),
            (0, 0, 2, 2, 5),
            (3, 9, 2, 2, 12),
            (3, 1, 0, 2, 9),
            (2, 3, 2, 5, 2),
            (3, 6, 0, 5, 6),
            (3, 6, 0, 6, 12),
            (0, 4, 1, 6, 8),
            (3, 0, 2, 5, 11),
            (3, 6, 1, 1, 11),
            (3, 0, 2, 1, 9),
        ],
        "1,1,1,0,2,0,2,0,0,0,1,1,0",
    ),
    (
        24,
        [(9, 1, 1, 15), (8, 3, 3, 14), (1, 3, 2, 13), (0, 1, 8, 9)],
        [
            (2, 10, 1, 6, 3),
            (2, 9, 0, 3, 3),
            (1, 5, 0, 4, 6),
            (1, 10, 0, 5, 2),
            (3, 1, 1, 3, 12),
            (1, 6, 0, 1, 5),
            (2, 4, 0, 5, 12),
            (1, 2, 2, 6, 12),
            (2, 9, 2, 2, 9),
        ],
        "3,1,1,2,9,0,1,5,2",
    ),
    (
        8,
        [(0, 2, 3, 0), (3, 3, 1, 9), (6, 0, 7, 17)],
        [
            (2, 0, 0, 4, 12),
            (2, 4, 2, 5, 2),
            (2, 7, 1, 3, 0),
            (2, 4, 2, 6, 12),
            (1, 10, 0, 4, 5),
            (0, 8, 2, 5, 0),
            (0, 1, 0, 6, 10),
            (0, 9, 0, 5, 7),
        ],
        "0,2,0,3,3,0,0,0",
    ),
    (
        30,
        [(4, 1, 7, 2), (1, 1, 4, 20)],
        [(1, 3, 2, 4, 2), (0, 5, 0, 2, 3), (1, 9, 0, 2, 4), (0, 0, 0, 3, 8)],
        "2,1,4,1",
    ),
]


@pytest.mark.parametrize("load,groups,subs,expected", CASES)
def test_allocation(load, groups, subs, expected):
    assert run_case(load, groups, subs) == expected


def test_conservation():
    for load, groups, subs, _ in CASES:
        out = run_case(load, groups, subs)
        parts = [int(x) for x in out.split(",")] if out else []
        assert len(parts) == len(subs)
        G = len(groups)
        group_caps = [c for _, _, _, c in groups]
        sub_caps = [c for _, _, _, _, c in subs]
        sub_tot = [0] * len(subs)
        # just check per-sub cap and per-group effective cap for single batch
        # since single batch, remaining caps = caps
        sum_member_caps = [0] * G
        for s, (gid, _, _, _, _) in enumerate(subs):
            if 0 <= gid < G:
                sum_member_caps[gid] += sub_caps[s]
        eff_cap = [min(group_caps[g], sum_member_caps[g]) for g in range(G)]
        assert sum(parts) == min(load, sum(eff_cap))
        for i, v in enumerate(parts):
            assert 0 <= v <= sub_caps[i]
        for g in range(G):
            gs = sum(parts[i] for i, (gid, _, _, _, _) in enumerate(subs) if gid == g)
            assert gs <= eff_cap[g]


def test_min_exceeds_cap():
    out = run_case(5, [(0, 0, 1, 10)], [(0, 10, 10, 1, 2)])
    assert out == "2"


def test_priority_tie_and_order():
    out = run_case(3, [(0, 0, 1, 10)], [(0, 10, 2, 1, 10), (0, 1, 2, 1, 10)])
    assert out == "2,1"
    out2 = run_case(3, [(0, 0, 1, 10)], [(0, 5, 2, 1, 10), (0, 5, 2, 1, 10)])
    assert out2 == "2,1"


def test_group_no_members():
    out = run_case(10, [(0, 0, 5, 10), (0, 0, 5, 10)], [(0, 0, 0, 1, 5)])
    assert out == "5"


def test_invalid_gid():
    out = run_case(10, [(0, 0, 1, 10)], [(0, 0, 0, 1, 5), (99, 0, 0, 1, 5)])
    assert out == "5,0"


def test_blank_lines_and_spaces():
    raw = """
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
    assert out == "6,4,3,3"


def test_large_numbers():
    out = run_case(
        1000000000000,
        [(0, 0, 1, 1000000000000)],
        [(0, 0, 0, 1, 500000000000), (0, 0, 0, 1, 500000000000)],
    )
    assert out == "500000000000,500000000000"


def test_large_weight_overflow():
    # remaining * credit would overflow int64: rem=1e12, credit=1e12 => product 1e24 > 2^63-1
    # This tests 64-bit safety requirement (R06) - must use 128-bit or safe decomposition
    out = run_case(
        1000000000000,
        [(0, 0, 1000000000000, 1000000000000)],
        [
            (0, 0, 0, 1000000000000, 500000000000),
            (0, 0, 0, 1000000000000, 500000000000),
        ],
    )
    assert out == "500000000000,500000000000"


def test_large_credit_overflow():
    # credit grows when idle: weight large, not served first round, credit becomes weight*2 etc.
    # After first round, one sub idle, credit grows to 2e12, then second round rem*credit = 1e12*2e12=2e24 overflow
    # Input: load 1000000000000, 3 subs in same group, caps small for one to force idle? Actually to force idle need share=0
    # Simpler: load 3, caps 10 each, weights 4000000000000000000 (4e18) which is >2^62, product 3*4e18=1.2e19 > 9e18 overflow
    # Use weights 4000000000000000000 (4e18) with load 3
    out = run_case(
        3,
        [(0, 0, 1, 10)],
        [(0, 0, 0, 4000000000000000000, 10), (0, 0, 0, 4000000000000000000, 10)],
    )
    # With equal weights, proportional share: 3*4e18 / 8e18 = 1 (floor), remaining 1, etc. Should still allocate 3 total
    # Exact output with credit decay: first round share 1,1 used2 rem1 best first gets 1 => 2,1
    assert out == "2,1"


def test_zero_caps():
    out = run_case(10, [(0, 0, 1, 0)], [(0, 0, 0, 1, 0)])
    assert out == "0"


def test_deterministic():
    a = run_case(
        16,
        [(10, 0, 5, 10), (5, 0, 3, 10)],
        [(0, 10, 0, 5, 6), (0, 5, 0, 3, 9), (1, 5, 0, 4, 3), (1, 1, 0, 1, 12)],
    )
    b = run_case(
        16,
        [(10, 0, 5, 10), (5, 0, 3, 10)],
        [(0, 10, 0, 5, 6), (0, 5, 0, 3, 9), (1, 5, 0, 4, 3), (1, 1, 0, 1, 12)],
    )
    assert a == b == "6,4,3,3"
