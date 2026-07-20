"""Black-box tests for the broker message allocator (Go).

Builds the Go program under /app and drives it via stdin/stdout. The program is
shipped with a subtle defect; these tests assert the correct allocations. The
reference solution passes all of them.
"""

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
        subprocess.run(["go", "mod", "init", "allocator"], cwd=APP, env=GO_ENV,
                       capture_output=True, text=True)

    def _build(pkg):
        return subprocess.run(["go", "build", "-o", BIN, pkg], cwd=APP, env=GO_ENV,
                              capture_output=True, text=True, timeout=240)

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run(load, subs):
    inp = f"{load}\n" + "\n".join(f"{w} {c}" for w, c in subs) + "\n"
    proc = subprocess.run([BIN], input=inp, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}"
    return proc.stdout.strip()


# (load, subscribers[(weight, cap)], expected_csv)
CASES = [
    # multi-round decay cases (the shipped defect gets these wrong)
    (16, [(5, 6), (3, 9), (4, 3), (1, 12)], "6,5,3,2"),
    (11, [(6, 4), (2, 12), (4, 9)], "4,3,4"),
    (6, [(1, 12), (4, 4), (3, 9)], "0,4,2"),
    (23, [(4, 7), (1, 6), (6, 4), (4, 12)], "7,3,4,9"),
    (9, [(1, 8), (3, 1), (5, 11)], "2,1,6"),
    (15, [(1, 6), (5, 5), (2, 6), (5, 5)], "2,5,3,5"),
    (13, [(2, 7), (2, 9), (5, 11), (5, 11)], "1,1,6,5"),
    (17, [(6, 1), (3, 11), (5, 11)], "1,6,10"),
    (5, [(6, 1), (1, 10), (3, 4), (5, 11)], "1,0,2,2"),
    (6, [(4, 11), (1, 6), (2, 5)], "4,0,2"),
    (9, [(5, 10), (6, 10), (6, 1)], "3,5,1"),
    (26, [(1, 12), (2, 7), (6, 10), (5, 12)], "2,4,10,10"),
    # normal / edge coverage
    (10, [(3, 100), (1, 100)], "8,2"),
    (100, [(5, 20), (3, 20), (1, 20)], "20,20,20"),
    (0, [(1, 5)], "0"),
    (7, [(1, 2), (1, 2), (1, 2)], "2,2,2"),
    (1, [(5, 10), (1, 10)], "1,0"),
    (2, [(1, 10), (1, 10), (1, 10)], "1,1,0"),
    (50, [(10, 5), (1, 100), (1, 100)], "5,23,22"),
    (1000, [(7, 50), (3, 50), (2, 200), (1, 400)], "50,50,200,400"),
    (4, [(1, 1), (1, 1), (1, 1), (1, 1)], "1,1,1,1"),
    (30, [(2, 3), (2, 3), (2, 3), (100, 100)], "0,0,0,30"),
]


@pytest.mark.parametrize("load,subs,expected", CASES)
def test_allocation(load, subs, expected):
    assert run(load, subs) == expected


def test_conserves_load_or_fills_capacity():
    # every allocation is within capacity and totals min(load, total_cap)
    for load, subs, _ in CASES:
        out = run(load, subs)
        parts = [int(x) for x in out.split(",")] if out else []
        assert len(parts) == len(subs)
        total_cap = sum(c for _, c in subs)
        for got, (_, cap) in zip(parts, subs):
            assert 0 <= got <= cap
        assert sum(parts) == min(load, total_cap)


def test_single_subscriber():
    assert run(9, [(1, 5)]) == "5"       # capped
    assert run(3, [(4, 10)]) == "3"      # all load


def test_deterministic():
    a = run(23, [(4, 7), (1, 6), (6, 4), (4, 12)])
    b = run(23, [(4, 7), (1, 6), (6, 4), (4, 12)])
    assert a == b == "7,3,4,9"
