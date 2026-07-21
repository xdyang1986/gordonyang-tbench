"""Black-box tests for hierarchical broker allocator (Go)."""

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


def run_hier(load, groups, subs):
    G = len(groups)
    S = len(subs)
    lines = [str(load), str(G)]
    for w, c in groups:
        lines.append(f"{w} {c}")
    lines.append(str(S))
    for gid, w, c in subs:
        lines.append(f"{gid} {w} {c}")
    inp = "\n".join(lines) + "\n"
    proc = subprocess.run([BIN], input=inp, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr} input:\n{inp}"
    return proc.stdout.strip()


# (load, groups[(weight, cap)], subs[(gid, weight, cap)], expected_csv)
CASES = [
    (16, [(5, 10), (3, 10)], [(0, 5, 6), (0, 3, 9), (1, 4, 3), (1, 1, 12)], "6,4,3,3"),
    (9, [(5, 10), (6, 10)], [(0, 5, 10), (0, 6, 10), (1, 6, 1)], "3,5,1"),
    (6, [(4, 11), (1, 6)], [(0, 4, 11), (0, 1, 6), (1, 2, 5)], "4,1,1"),
    (9, [(1, 10)], [(0, 1, 5)], "5"),
    (3, [(4, 10)], [(0, 4, 10)], "3"),
    (10, [(3, 100)], [(0, 3, 100), (0, 1, 100)], "8,2"),
    (7, [(1, 10)], [(0, 1, 2), (0, 1, 2), (0, 1, 2)], "2,2,2"),
    (1, [(1, 10)], [(0, 5, 10), (0, 1, 10)], "1,0"),
    (100, [(5, 20), (3, 20)], [(0, 5, 20), (0, 3, 20), (1, 1, 20)], "13,7,20"),
    (50, [(10, 5), (1, 100)], [(0, 10, 5), (1, 1, 100), (1, 1, 100)], "5,23,22"),
    (
        30,
        [(2, 3), (100, 100)],
        [(0, 2, 3), (0, 2, 3), (0, 2, 3), (1, 100, 100)],
        "0,0,0,30",
    ),
    (20, [(10, 5)], [(0, 1, 2), (0, 1, 2)], "2,2"),
    (20, [(10, 100)], [(0, 1, 2), (0, 1, 2)], "2,2"),
    (11, [(6, 4), (2, 12), (4, 9)], [(0, 1, 4), (1, 1, 12), (2, 1, 9)], "4,3,4"),
    (
        23,
        [(4, 7), (1, 6), (6, 4)],
        [(0, 1, 7), (1, 1, 6), (2, 1, 4), (0, 1, 12)],
        "4,6,4,3",
    ),
    (
        26,
        [(1, 12), (2, 7), (6, 10)],
        [(0, 1, 12), (1, 2, 7), (2, 6, 10), (2, 5, 12)],
        "9,7,6,4",
    ),
    (
        15,
        [(1, 6), (5, 5), (2, 6)],
        [(0, 1, 6), (1, 5, 5), (2, 2, 6), (1, 5, 5)],
        "4,3,6,2",
    ),
    (0, [(1, 5)], [(0, 1, 5)], "0"),
    (0, [(1, 5), (1, 5)], [(0, 1, 5), (1, 1, 5)], "0,0"),
    (10, [(1, 5), (1, 5), (1, 5)], [(0, 1, 5), (1, 1, 5), (2, 1, 5)], "4,3,3"),
    (10, [(5, 2)], [(0, 2, 1), (0, 6, 10), (0, 3, 4), (0, 6, 7)], "0,1,0,1"),
    (
        30,
        [(7, 2)],
        [(0, 2, 0), (0, 8, 3), (0, 2, 4), (0, 1, 9), (0, 8, 8)],
        "0,1,0,0,1",
    ),
    (
        21,
        [(5, 10)],
        [(0, 4, 10), (0, 7, 13), (0, 7, 11), (0, 3, 2), (0, 5, 5)],
        "1,3,3,1,2",
    ),
    (
        24,
        [(9, 12), (2, 14), (7, 18)],
        [(0, 4, 2), (2, 6, 0), (1, 4, 12), (2, 1, 9), (1, 4, 15), (0, 3, 14)],
        "2,0,2,9,1,10",
    ),
    (
        9,
        [(1, 0), (3, 14), (4, 0), (5, 11)],
        [
            (2, 7, 13),
            (0, 2, 2),
            (2, 2, 2),
            (3, 1, 12),
            (0, 7, 0),
            (2, 5, 15),
            (0, 6, 13),
            (2, 1, 5),
            (3, 2, 4),
            (2, 4, 2),
            (0, 7, 9),
        ],
        "0,0,0,5,0,0,0,0,4,0,0",
    ),
    (29, [(7, 11), (3, 17), (6, 16)], [(2, 3, 12), (1, 1, 4)], "12,4"),
    (17, [(6, 14)], [(0, 7, 0), (0, 6, 4), (0, 2, 11), (0, 6, 2)], "0,4,8,2"),
    (48, [(4, 10), (5, 12)], [(0, 4, 6)], "6"),
    (
        14,
        [(4, 8)],
        [(0, 2, 10), (0, 6, 4), (0, 8, 15), (0, 1, 8), (0, 6, 12)],
        "0,3,3,0,2",
    ),
    (
        7,
        [(10, 20), (1, 19)],
        [
            (1, 2, 1),
            (1, 8, 11),
            (1, 6, 11),
            (0, 8, 10),
            (0, 3, 12),
            (0, 7, 12),
            (1, 3, 12),
            (1, 2, 9),
        ],
        "0,0,0,4,1,2,0,0",
    ),
    (
        23,
        [(9, 19), (6, 1), (7, 1)],
        [
            (1, 5, 3),
            (1, 6, 7),
            (2, 8, 8),
            (1, 6, 7),
            (2, 8, 8),
            (1, 1, 5),
            (0, 1, 13),
            (2, 8, 4),
            (1, 1, 15),
            (2, 3, 10),
        ],
        "0,1,1,0,0,0,13,0,0,0",
    ),
    (
        12,
        [(10, 1), (8, 16)],
        [(0, 6, 12), (0, 2, 8), (1, 3, 13), (1, 7, 1), (0, 8, 5)],
        "0,0,10,1,1",
    ),
    (33, [(5, 16)], [(0, 5, 15), (0, 4, 9)], "9,7"),
    (
        13,
        [(2, 14)],
        [(0, 4, 2), (0, 7, 14), (0, 1, 13), (0, 6, 4), (0, 3, 6)],
        "2,5,0,4,2",
    ),
    (
        14,
        [(1, 7), (4, 2)],
        [(0, 1, 13), (1, 1, 1), (0, 7, 9), (0, 5, 3), (1, 6, 6), (0, 6, 4), (1, 4, 13)],
        "0,0,3,1,1,3,1",
    ),
]


@pytest.mark.parametrize("load,groups,subs,expected", CASES)
def test_allocation(load, groups, subs, expected):
    assert run_hier(load, groups, subs) == expected


def test_conservation():
    for load, groups, subs, expected in CASES:
        out = run_hier(load, groups, subs)
        parts = [int(x) for x in out.split(",")] if out else []
        assert len(parts) == len(subs)
        G = len(groups)
        sum_member_caps = [0] * G
        for gid, _, c in subs:
            if 0 <= gid < G:
                sum_member_caps[gid] += c
        eff_cap = [min(groups[g][1], sum_member_caps[g]) for g in range(G)]
        total_eff = sum(eff_cap)
        assert sum(parts) == min(load, total_eff)
        for got, (_, _, cap_) in zip(parts, subs):
            assert 0 <= got <= cap_
        for g in range(G):
            s = sum(parts[i] for i, (gid, _, _) in enumerate(subs) if gid == g)
            assert s <= eff_cap[g]


def test_deterministic():
    a = run_hier(16, [(5, 10), (3, 10)], [(0, 5, 6), (0, 3, 9), (1, 4, 3), (1, 1, 12)])
    b = run_hier(16, [(5, 10), (3, 10)], [(0, 5, 6), (0, 3, 9), (1, 4, 3), (1, 1, 12)])
    assert a == b == "6,4,3,3"
