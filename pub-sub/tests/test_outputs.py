"""Black-box tests for hierarchical multi-batch allocator with min, priority, rate limits, credit-decay - balanced hard with explicit deterministic spec and invariant fuzz."""

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
    (
        2,
        [6, 6],
        [(0, 0, 4, 11, 0), (0, 0, 1, 6, 0)],
        [(0, 5, 0, 4, 11, 0), (0, 1, 0, 1, 6, 0), (1, 10, 0, 2, 5, 0)],
        ["4,1,1", "4,1,1"],
    ),
    (1, [5], [(0, 0, 1, 10, 0)], [(0, 10, 10, 1, 2, 0)], ["2"]),
    (1, [0], [(1, 0, 1, 5, 0)], [(0, 1, 0, 1, 5, 0)], ["0"]),
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
        2,
        [0, 9],
        [(3, 1, 7, 5, 0), (5, 0, 7, 8, 0)],
        [(0, 5, 2, 6, 1, 0), (1, 9, 2, 2, 2, 4), (0, 1, 0, 3, 5, 0)],
        ["0,0,0", "1,2,4"],
    ),
    (
        1,
        [14],
        [(0, 2, 7, 0, 0), (0, 2, 3, 18, 0)],
        [(1, 1, 2, 4, 1, 0), (0, 6, 0, 5, 9, 0), (1, 0, 0, 1, 7, 0)],
        ["1,0,7"],
    ),
    (
        1,
        [20],
        [(9, 0, 3, 16, 0), (3, 1, 5, 4, 0), (8, 2, 1, 8, 0)],
        [
            (0, 5, 2, 5, 9, 0),
            (0, 10, 0, 2, 5, 0),
            (2, 10, 0, 3, 11, 0),
            (1, 10, 1, 4, 4, 0),
            (1, 9, 1, 3, 11, 0),
            (1, 7, 0, 3, 7, 0),
            (0, 3, 1, 2, 0, 0),
        ],
        ["9,2,5,2,2,0,0"],
    ),
    (
        2,
        [11, 17],
        [(9, 0, 6, 15, 0), (7, 0, 6, 10, 0), (6, 1, 3, 12, 0)],
        [
            (2, 0, 2, 1, 7, 0),
            (0, 1, 2, 2, 0, 0),
            (0, 4, 0, 5, 1, 0),
            (1, 7, 1, 1, 6, 0),
            (0, 3, 1, 6, 12, 0),
            (0, 6, 2, 5, 3, 0),
            (2, 7, 0, 2, 8, 0),
            (2, 8, 2, 2, 2, 0),
            (2, 7, 0, 6, 3, 0),
            (0, 1, 2, 1, 1, 0),
        ],
        ["1,0,0,4,1,2,0,2,0,1", "2,0,1,2,7,1,1,0,3,0"],
    ),
    (
        1,
        [12],
        [(10, 0, 10, 1, 0), (8, 0, 8, 16, 0)],
        [
            (0, 6, 0, 6, 12, 0),
            (0, 2, 0, 2, 8, 0),
            (1, 3, 0, 3, 13, 0),
            (1, 7, 0, 7, 1, 0),
            (0, 8, 0, 8, 5, 0),
        ],
        ["0,0,10,1,1"],
    ),
    (
        2,
        [11, 6],
        [(4, 0, 2, 6, 0), (4, 2, 1, 0, 0), (2, 2, 3, 10, 0)],
        [
            (0, 9, 2, 3, 8, 0),
            (1, 0, 0, 4, 6, 0),
            (2, 9, 1, 6, 10, 0),
            (2, 10, 1, 5, 8, 0),
        ],
        ["4,0,4,3", "2,0,2,1"],
    ),
    (
        1,
        [23],
        [(0, 1, 1, 13, 0), (4, 2, 3, 14, 6), (2, 1, 6, 8, 4)],
        [(0, 10, 0, 4, 7, 0), (2, 1, 1, 3, 11, 0)],
        ["7,4"],
    ),
    (1, [10], [(5, 0, 5, 10, 5)], [(0, 0, 0, 1, 10, 2), (0, 0, 0, 1, 10, 2)], ["2,2"]),
    (1, [0], [(1, 0, 1, 5, 0)], [(0, 1, 0, 1, 5, 0)], ["0"]),
    (
        2,
        [10, 10],
        [(5, 0, 5, 10, 0), (5, 0, 5, 10, 0)],
        [(0, 10, 1, 5, 10, 0), (0, 1, 1, 5, 10, 0), (1, 5, 0, 4, 10, 0)],
        ["1,5,4", "1,3,6"],
    ),
    (
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0)],
        [(0, 10, 1, 3, 10, 0), (0, 1, 1, 3, 10, 0)],
        ["3,2", "3,2", "0,0"],
    ),
    (
        1,
        [12],
        [(10, 0, 10, 1, 0), (8, 0, 8, 16, 0)],
        [
            (0, 6, 0, 6, 12, 0),
            (0, 2, 0, 2, 8, 0),
            (1, 3, 0, 3, 13, 0),
            (1, 7, 0, 7, 1, 0),
            (0, 8, 0, 8, 5, 0),
        ],
        ["0,0,10,1,1"],
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
        G = len(groups)
        S = len(subs)
        group_caps = [c for _, _, _, c, _ in groups]
        sub_caps = (
            [c for _, _, _, _, _, c in subs]
            if subs and len(subs[0]) == 6
            else [c for _, _, _, c, _ in subs]
        )
        # Actually subs are (gid, prio, min, weight, cap, rate) 6 fields, cap at index 4
        sub_caps = [s[4] for s in subs]
        group_caps = [g[3] for g in groups]
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
            eff_rem = [
                min(max(0, group_caps[g] - group_tot[g]), sum_member_rem[g])
                for g in range(G)
            ]
            # apply rate limits
            for g in range(G):
                ra = groups[g][4] if len(groups[g]) > 4 else 0
                if ra > 0 and eff_rem[g] > ra:
                    eff_rem[g] = ra
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _, _) in enumerate(subs) if gid == g
                )
                assert gs <= eff_rem[g]
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


def test_priority_tie_and_order():
    out = run_case(
        1, [3], [(0, 0, 1, 10, 0)], [(0, 10, 2, 1, 10, 0), (0, 1, 2, 1, 10, 0)]
    )
    assert out == ["2,1"]
    out2 = run_case(
        1, [3], [(0, 0, 1, 10, 0)], [(0, 5, 2, 1, 10, 0), (0, 5, 2, 1, 10, 0)]
    )
    assert out2 == ["2,1"]


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
    out = run_case(
        1, [10], [(0, 0, 1, 10, 5)], [(0, 0, 0, 1, 10, 2), (0, 0, 0, 1, 10, 2)]
    )
    assert out == ["2,2"]


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0, 0)], [(0, 0, 0, 1, 0, 0)])
    assert out == ["0"]


def test_deterministic():
    # More meaningful: many random inputs, each run twice must be identical
    import random as _rnd

    _rnd.seed(303)
    for _ in range(20):
        T = _rnd.randint(1, 2)
        loads = [_rnd.randint(0, 20) for _ in range(T)]
        G = _rnd.randint(1, 3)
        groups = [
            (
                _rnd.randint(0, 5),
                _rnd.randint(0, 2),
                _rnd.randint(1, 4),
                _rnd.randint(0, 10),
                _rnd.choice([0, _rnd.randint(1, 5)]),
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
            )
            for _ in range(S)
        ]
        a = run_case(T, loads, groups, subs)
        b = run_case(T, loads, groups, subs)
        assert a == b, f"non-deterministic for {T},{loads},{groups},{subs}: {a} vs {b}"


def test_fuzz_invariants():
    # Fuzz with invariants, not exact-match against specific reference port including unspecified choices
    # This makes grading less restrictive: any correct alternative respecting invariants passes
    import random as _rnd

    _rnd.seed(2024)
    for _ in range(30):
        T = _rnd.randint(1, 3)
        loads = [_rnd.randint(0, 30) for _ in range(T)]
        G = _rnd.randint(1, 3)
        groups = []
        for _ in range(G):
            p = _rnd.randint(0, 10)
            mn = _rnd.randint(0, 3)
            w = _rnd.randint(1, 8)
            c = _rnd.randint(0, 20)
            ra = _rnd.choice([0, 0, _rnd.randint(1, 10)])
            groups.append((p, mn, w, c, ra))
        S = _rnd.randint(1, G * 4 + 2)
        subs = []
        for _ in range(S):
            gid = _rnd.randint(0, G - 1)
            p = _rnd.randint(0, 10)
            mn = _rnd.randint(0, 2)
            w = _rnd.randint(1, 6)
            c = _rnd.randint(0, 15)
            ra = _rnd.choice([0, 0, _rnd.randint(1, 10)])
            subs.append((gid, p, mn, w, c, ra))
        out_lines = run_case(T, loads, groups, subs)
        assert len(out_lines) == T
        caps = [c for _, _, _, c, _ in groups]
        sub_caps = [c for _, _, _, _, c, _ in subs]
        sub_total = [0] * S
        group_total = [0] * G
        for line in out_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert v >= 0
                assert sub_total[i] + v <= sub_caps[i]
            sum_member_rem = [0] * G
            for s_idx, (gid, _, _, _, _, _) in enumerate(subs):
                if 0 <= gid < G:
                    sum_member_rem[gid] += sub_caps[s_idx] - sub_total[s_idx]
            eff_g_rem = [
                min(max(0, caps[g] - group_total[g]), sum_member_rem[g])
                for g in range(G)
            ]
            for g in range(G):
                ra = groups[g][4]
                if ra > 0 and eff_g_rem[g] > ra:
                    eff_g_rem[g] = ra
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _, _) in enumerate(subs) if gid == g
                )
                assert gs <= eff_g_rem[g]
            for i, v in enumerate(parts):
                sub_total[i] += v
            for g in range(G):
                gs = sum(
                    parts[i] for i, (gid, _, _, _, _, _) in enumerate(subs) if gid == g
                )
                group_total[g] += gs
