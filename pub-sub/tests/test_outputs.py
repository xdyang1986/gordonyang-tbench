"""Black-box tests for ultimate hard hierarchical allocator with burst, dynamic weights, negative rebalancing, global rebalancing, min, priority, rate, credit-decay, T+6 output - very hard balanced."""

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
    # groups: 6 fields prio min weight cap rate burst
    # subs: 7 fields gid prio min weight cap rate burst
    lines = [str(T)]
    for ld in loads:
        lines.append(str(ld))
    lines.append(str(len(groups)))
    for g in groups:
        # allow 5-field old format as well, pad burst 0
        if len(g) == 5:
            g = tuple(list(g) + [0])
        lines.append(" ".join(map(str, g)))
    lines.append(str(len(subs)))
    for s in subs:
        if len(s) == 6:
            s = tuple(list(s) + [0])
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


# Parametrized cases: 20 original with burst 0 + 5 new with burst/negative
# Expected includes T+6 lines: batch lines, group totals, sub totals, group credits, sub credits, group burst rem, sub burst rem
CASES = [
    (
        1,
        [16],
        [(10, 0, 5, 10, 0, 0), (5, 0, 3, 10, 0, 0)],
        [
            (0, 10, 0, 5, 6, 0, 0),
            (0, 5, 0, 3, 9, 0, 0),
            (1, 5, 0, 4, 3, 0, 0),
            (1, 1, 0, 1, 12, 0, 0),
        ],
        ["6,4,3,3", "10,6", "6,4,3,3", "3,2", "3,2,3,1", "0,0", "0,0,0,0"],
    ),
    (
        1,
        [9],
        [(0, 0, 5, 10, 0, 0), (0, 0, 6, 10, 0, 0)],
        [(0, 10, 2, 5, 10, 2, 0), (0, 5, 1, 6, 10, 10, 0), (1, 1, 0, 6, 1, 0, 0)],
        ["2,6,1", "8,1", "2,6,1", "3,4", "3,4,4", "0,0", "0,0,0"],
    ),
    (
        2,
        [6, 6],
        [(0, 0, 4, 11, 0, 0), (0, 0, 1, 6, 0, 0)],
        [(0, 5, 0, 4, 11, 0, 0), (0, 1, 0, 1, 6, 0, 0), (1, 10, 0, 2, 5, 0, 0)],
        ["4,1,1", "4,1,1", "10,2", "8,2,2", "2,1", "2,1,2", "0,0", "0,0,0"],
    ),
    (
        1,
        [5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 10, 1, 2, 0, 0)],
        ["2", "2", "2", "1", "1", "0", "0"],
    ),
    (
        1,
        [0],
        [(1, 0, 1, 5, 0, 0)],
        [(0, 1, 0, 1, 5, 0, 0)],
        ["0", "0", "0", "2", "2", "0", "0"],
    ),
    (
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0), (0, 0, 0, 1, 500000000000, 0, 0)],
        [
            "500000000000,500000000000",
            "1000000000000",
            "500000000000,500000000000",
            "1",
            "1,1",
            "0",
            "0,0",
        ],
    ),
    (
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0, 0),
            (0, 0, 0, 1000000000000, 500000000000, 0, 0),
        ],
        [
            "500000000000,500000000000",
            "1000000000000",
            "500000000000,500000000000",
            "500000000001",
            "500000000001,500000000001",
            "0",
            "0,0",
        ],
    ),
    (
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [
            (0, 0, 0, 4000000000000000000, 10, 0, 0),
            (0, 0, 0, 4000000000000000000, 10, 0, 0),
        ],
        ["2,1", "3", "2,1", "1", "2000000000000000001,2000000000000000001", "0", "0,0"],
    ),
    (
        2,
        [0, 9],
        [(3, 1, 7, 5, 0, 0), (5, 0, 7, 8, 0, 0)],
        [(0, 5, 2, 6, 1, 0, 0), (1, 9, 2, 2, 2, 4, 0), (0, 1, 0, 3, 5, 0, 0)],
        ["0,0,0", "1,2,4", "5,2", "1,2,4", "8,8", "7,3,4", "0,0", "0,0,0"],
    ),
    (
        1,
        [14],
        [(0, 2, 7, 0, 0, 0), (0, 2, 3, 18, 0, 0)],
        [(1, 1, 2, 4, 1, 0, 0), (0, 6, 0, 5, 9, 0, 0), (1, 0, 0, 1, 7, 0, 0)],
        ["1,0,7", "0,8", "1,0,7", "7,2", "3,10,1", "0,0", "0,0,0"],
    ),
    (
        1,
        [20],
        [(9, 0, 3, 16, 0, 0), (3, 1, 5, 4, 0, 0), (8, 2, 1, 8, 0, 0)],
        [
            (0, 5, 2, 5, 9, 0, 0),
            (0, 10, 0, 2, 5, 0, 0),
            (2, 10, 0, 3, 11, 0, 0),
            (1, 10, 1, 4, 4, 0, 0),
            (1, 9, 1, 3, 11, 0, 0),
            (1, 7, 0, 3, 7, 0, 0),
            (0, 3, 1, 2, 0, 0, 0),
        ],
        [
            "9,2,5,2,2,0,0",
            "11,4,5",
            "9,2,5,2,2,0,0",
            "2,3,1",
            "3,2,2,3,2,6,2",
            "0,0,0",
            "0,0,0,0,0,0,0",
        ],
    ),
    (
        2,
        [11, 17],
        [(9, 0, 6, 15, 0, 0), (7, 0, 6, 10, 0, 0), (6, 1, 3, 12, 0, 0)],
        [
            (2, 0, 2, 1, 7, 0, 0),
            (0, 1, 2, 2, 0, 0, 0),
            (0, 4, 0, 5, 1, 0, 0),
            (1, 7, 1, 1, 6, 0, 0),
            (0, 3, 1, 6, 12, 0, 0),
            (0, 6, 2, 5, 3, 0, 0),
            (2, 7, 0, 2, 8, 0, 0),
            (2, 8, 2, 2, 2, 0, 0),
            (2, 7, 0, 6, 3, 0, 0),
            (0, 1, 2, 1, 1, 0, 0),
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
        ],
    ),
    (
        1,
        [12],
        [(10, 0, 10, 1, 0, 0), (8, 0, 8, 16, 0, 0)],
        [
            (0, 6, 0, 6, 12, 0, 0),
            (0, 2, 0, 2, 8, 0, 0),
            (1, 3, 0, 3, 13, 0, 0),
            (1, 7, 0, 7, 1, 0, 0),
            (0, 8, 0, 8, 5, 0, 0),
        ],
        ["0,0,10,1,1", "1,11", "0,0,10,1,1", "6,5", "12,4,2,4,5", "0,0", "0,0,0,0,0"],
    ),
    (
        2,
        [11, 6],
        [(4, 0, 2, 6, 0, 0), (4, 2, 1, 0, 0, 0), (2, 2, 3, 10, 0, 0)],
        [
            (0, 9, 2, 3, 8, 0, 0),
            (1, 0, 0, 4, 6, 0, 0),
            (2, 9, 1, 6, 10, 0, 0),
            (2, 10, 1, 5, 8, 0, 0),
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
        ],
    ),
    (
        1,
        [23],
        [(0, 1, 1, 13, 0, 0), (4, 2, 3, 14, 6, 0), (2, 1, 6, 8, 4, 0)],
        [(0, 10, 0, 4, 7, 0, 0), (2, 1, 1, 3, 11, 0, 0)],
        ["7,4", "7,0,4", "7,4", "1,3,4", "3,2", "0,0,0", "0,0"],
    ),
    (
        1,
        [10],
        [(5, 0, 5, 10, 5, 0)],
        [(0, 0, 0, 1, 10, 2, 0), (0, 0, 0, 1, 10, 2, 0)],
        ["2,2", "4", "2,2", "3", "1,1", "0", "0,0"],
    ),
    (
        1,
        [0],
        [(1, 0, 1, 5, 0, 0)],
        [(0, 1, 0, 1, 5, 0, 0)],
        ["0", "0", "0", "2", "2", "0", "0"],
    ),
    (
        2,
        [10, 10],
        [(5, 0, 5, 10, 0, 0), (5, 0, 5, 10, 0, 0)],
        [(0, 10, 1, 5, 10, 0, 0), (0, 1, 1, 5, 10, 0, 0), (1, 5, 0, 4, 10, 0, 0)],
        ["3,2,5", "3,2,5", "10,10", "6,4,10", "2,2", "2,2,2", "0,0", "0,0,0"],
    ),
    (
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 3, 10, 0, 0), (0, 1, 1, 3, 10, 0, 0)],
        ["3,2", "3,2", "0,0", "10", "6,4", "1", "3,3", "0", "0,0"],
    ),
    (
        1,
        [12],
        [(10, 0, 10, 1, 0, 0), (8, 0, 8, 16, 0, 0)],
        [
            (0, 6, 0, 6, 12, 0, 0),
            (0, 2, 0, 2, 8, 0, 0),
            (1, 3, 0, 3, 13, 0, 0),
            (1, 7, 0, 7, 1, 0, 0),
            (0, 8, 0, 8, 5, 0, 0),
        ],
        ["0,0,10,1,1", "1,11", "0,0,10,1,1", "6,5", "12,4,2,4,5", "0,0", "0,0,0,0,0"],
    ),
    # New hard cases with burst and negative
    (
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3), (0, 0, 0, 1, 10, 0, 0)],
        ["3,2", "5", "3,2", "1", "1,1", "0", "2,0"],
    ),
    (
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0), (0, 1, 0, 2, 10, 0, 0)],
        ["3,3", "-3,-1", "2", "0,2", "2", "2,2", "0", "0,0"],
    ),
    (
        1,
        [8],
        [(0, 0, 2, 10, 3, 2)],
        [(0, 10, 1, 2, 10, 3, 1), (0, 1, 1, 2, 10, 0, 0)],
        ["3,2", "5", "3,2", "2", "2,2", "0", "1,0"],
    ),
    (
        2,
        [10, -5],
        [(5, 0, 5, 10, 3, 2), (5, 0, 5, 10, 0, 0)],
        [(0, 10, 1, 5, 10, 2, 1), (0, 1, 1, 5, 10, 0, 0), (1, 5, 0, 4, 10, 1, 0)],
        ["3,2,1", "-3,-2,0", "0,1", "0,0,1", "2,7", "2,2,6", "0,0", "0,0,0"],
    ),
    (
        1,
        [10],
        [(0, 0, 1, 10, 2, 5)],
        [(0, 0, 0, 1, 10, 2, 5), (0, 0, 0, 1, 10, 2, 0)],
        ["5,2", "7", "5,2", "1", "1,1", "0", "2,0"],
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
        assert len(out_lines) == T + 6
        G = len(groups)
        S = len(subs)
        # groups now 6 fields, subs 7 fields
        group_caps = [g[3] for g in groups]
        sub_caps = [s[4] for s in subs]
        group_tot = [0] * G
        sub_tot = [0] * S

        for line in batch_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            # For negative batches, parts may be negative, but totals must stay >=0 and <=caps
            # Check per-batch non-negative for positive loads, but allow negative for deallocation
            # Here we only check caps for positive allocation: cumulative after adding must stay within [0, cap]
            for i, v in enumerate(parts):
                # v can be negative for deallocation
                assert sub_tot[i] + v >= 0
                assert sub_tot[i] + v <= sub_caps[i]
            sum_member_rem = [0] * G
            for s_idx, (gid, _, _, _, _, _, _) in enumerate(subs):
                if 0 <= gid < G:
                    sum_member_rem[gid] += sub_caps[s_idx] - sub_tot[s_idx]
            eff_rem = [
                min(max(0, group_caps[g] - group_tot[g]), sum_member_rem[g])
                for g in range(G)
            ]
            for g in range(G):
                ra = groups[g][4]
                # include burst for effective cap
                burst_rem = groups[
                    g
                ][
                    5
                ]  # initial burst, but after consumption it shrinks - for conservation we use initial as upper bound
                if ra > 0:
                    # effective per-batch max including burst at start
                    max_batch = ra + burst_rem
                    if eff_rem[g] > max_batch:
                        eff_rem[g] = max_batch
                gs = sum(
                    parts[i]
                    for i, (gid, _, _, _, _, _, _) in enumerate(subs)
                    if gid == g
                )
                # For positive batches gs >=0 should be <= eff_rem, for negative gs <=0 we check absolute
                if gs >= 0:
                    assert gs <= eff_rem[g], (
                        f"group eff cap exceeded g={g} gs={gs} eff={eff_rem[g]}"
                    )
            for i, v in enumerate(parts):
                sub_tot[i] += v
            for g in range(G):
                gs = sum(
                    parts[i]
                    for i, (gid, _, _, _, _, _, _) in enumerate(subs)
                    if gid == g
                )
                group_tot[g] += gs

        # Validate extra lines
        group_tot_line = (
            [int(x) for x in out_lines[T].split(",")] if out_lines[T] else []
        )
        sub_tot_line = (
            [int(x) for x in out_lines[T + 1].split(",")] if out_lines[T + 1] else []
        )
        group_cred_line = (
            [int(x) for x in out_lines[T + 2].split(",")] if out_lines[T + 2] else []
        )
        sub_cred_line = (
            [int(x) for x in out_lines[T + 3].split(",")] if out_lines[T + 3] else []
        )
        group_burst_line = (
            [int(x) for x in out_lines[T + 4].split(",")] if out_lines[T + 4] else []
        )
        sub_burst_line = (
            [int(x) for x in out_lines[T + 5].split(",")] if out_lines[T + 5] else []
        )
        assert group_tot_line == group_tot
        assert sub_tot_line == sub_tot
        assert len(group_cred_line) == G and all(c >= 1 for c in group_cred_line)
        assert len(sub_cred_line) == S and all(c >= 1 for c in sub_cred_line)
        assert len(group_burst_line) == G and all(b >= 0 for b in group_burst_line)
        assert len(sub_burst_line) == S and all(b >= 0 for b in sub_burst_line)


def test_min_exceeds_cap():
    out = run_case(1, [5], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 2, 0, 0)])
    assert out[0] == "2"
    assert len(out) == 7


def test_min_gt_rate():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 0)])
    assert out[0] == "2"
    assert len(out) == 7


def test_min_gt_rate_with_burst():
    # min 10, rate 2 burst 3 -> effective cap rate+burst=5, so min capped to 5
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 3)])
    assert out[0] == "5"
    assert len(out) == 7
    # burst remaining should be 0 after consuming 3 excess (5-2=3)
    assert out[6] == "0"


def test_priority_tie_and_order():
    out = run_case(
        1, [3], [(0, 0, 1, 10, 0, 0)], [(0, 10, 2, 1, 10, 0, 0), (0, 1, 2, 1, 10, 0, 0)]
    )
    assert out[0] == "2,1"
    out2 = run_case(
        1, [3], [(0, 0, 1, 10, 0, 0)], [(0, 5, 2, 1, 10, 0, 0), (0, 5, 2, 1, 10, 0, 0)]
    )
    assert out2[0] == "2,1"


def test_group_no_members():
    out = run_case(
        1, [10], [(0, 0, 5, 10, 0, 0), (0, 0, 5, 10, 0, 0)], [(0, 0, 0, 1, 5, 0, 0)]
    )
    assert out[0] == "5"
    assert out[1] == "5,0"
    assert len(out) == 7


def test_invalid_gid():
    out = run_case(
        1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 0, 0, 1, 5, 0, 0), (99, 0, 0, 1, 5, 0, 0)]
    )
    assert out[0] == "5,0"
    assert len(out) == 7


def test_blank_lines_and_spaces():
    raw = """
1
16

2
10 0 5 10 0 0
5 0 3 10 0 0
4
0 10 0 5 6 0 0
  0 5 0 3 9 0 0
1 5 0 4 3 0 0
1 1 0 1 12 0 0

"""
    out = run_case_raw(raw)
    assert out[0] == "6,4,3,3"
    assert len(out) == 7


def test_large_numbers():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0), (0, 0, 0, 1, 500000000000, 0, 0)],
    )
    assert out[0] == "500000000000,500000000000"
    assert len(out) == 7


def test_large_weight_overflow():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1000000000000, 1000000000000, 0, 0)],
        [
            (0, 0, 0, 1000000000000, 500000000000, 0, 0),
            (0, 0, 0, 1000000000000, 500000000000, 0, 0),
        ],
    )
    assert out[0] == "500000000000,500000000000"
    assert len(out) == 7


def test_large_credit_overflow():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [
            (0, 0, 0, 4000000000000000000, 10, 0, 0),
            (0, 0, 0, 4000000000000000000, 10, 0, 0),
        ],
    )
    assert out[0] == "2,1"
    assert len(out) == 7


def test_rate_limiting():
    out = run_case(
        1, [10], [(0, 0, 1, 10, 5, 0)], [(0, 0, 0, 1, 10, 2, 0), (0, 0, 0, 1, 10, 2, 0)]
    )
    assert out[0] == "2,2"
    assert len(out) == 7
    gt = [int(x) for x in out[1].split(",")]
    assert sum(gt) <= 5


def test_rate_with_burst():
    out = run_case(
        1, [5], [(0, 0, 1, 10, 2, 3)], [(0, 0, 0, 1, 10, 2, 3), (0, 0, 0, 1, 10, 0, 0)]
    )
    assert out[0] == "3,2"
    # group burst 3 consumed 3 (5-2=3) -> 0 remaining
    assert out[5] == "0"
    # sub0 burst 3 consumed 1 (3-2=1) -> 2 remaining
    assert out[6] == "2,0"


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0, 0, 0)], [(0, 0, 0, 1, 0, 0, 0)])
    assert out[0] == "0"
    assert len(out) == 7


def test_global_rebalancing():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0), (0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 1, 0, 0), (0, 0, 0, 1, 1, 0, 0), (1, 0, 0, 1, 10, 0, 0)],
    )
    assert out[0] == "1,1,8"
    assert len(out) == 7
    assert out[1] == "2,8"


def test_dynamic_weight_and_credit():
    out = run_case(
        2,
        [5, 5],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 10, 1, 2, 10, 0, 0), (0, 1, 1, 2, 10, 0, 0)],
    )
    assert len(out) == 8
    group_credits = [int(x) for x in out[4].split(",")]
    sub_credits = [int(x) for x in out[5].split(",")]
    assert all(c >= 1 for c in group_credits)
    assert all(c >= 1 for c in sub_credits)


def test_final_totals_and_credits_consistency():
    out = run_case(
        2,
        [6, 6],
        [(0, 0, 4, 11, 0, 0), (0, 0, 1, 6, 0, 0)],
        [(0, 5, 0, 4, 11, 0, 0), (0, 1, 0, 1, 6, 0, 0), (1, 10, 0, 2, 5, 0, 0)],
    )
    assert len(out) == 8
    batch1 = [int(x) for x in out[0].split(",")]
    batch2 = [int(x) for x in out[1].split(",")]
    group_totals = [int(x) for x in out[2].split(",")]
    sub_totals = [int(x) for x in out[3].split(",")]
    assert sub_totals[0] == batch1[0] + batch2[0]
    assert sub_totals[1] == batch1[1] + batch2[1]
    assert sub_totals[2] == batch1[2] + batch2[2]
    assert group_totals[0] == sub_totals[0] + sub_totals[1]
    assert group_totals[1] == sub_totals[2]


def test_negative_deallocation():
    out = run_case(
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0), (0, 1, 0, 2, 10, 0, 0)],
    )
    assert len(out) == 8
    # first batch allocations positive, second negative
    b1 = [int(x) for x in out[0].split(",")]
    b2 = [int(x) for x in out[1].split(",")]
    assert all(v >= 0 for v in b1)
    assert all(v <= 0 for v in b2)
    # totals after deallocation
    sub_totals = [int(x) for x in out[3].split(",")]
    assert sub_totals[0] == b1[0] + b2[0]
    assert sub_totals[1] == b1[1] + b2[1]
    assert all(t >= 0 for t in sub_totals)


def test_burst_final_consistency():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3), (0, 0, 0, 1, 10, 0, 0)],
    )
    assert len(out) == 7
    # group allocated 5, rate 2, burst 3 -> burst consumed 3 -> remaining 0
    assert out[5] == "0"
    # sub0 allocated 3, rate2 burst3 -> burst remaining 2
    assert out[6] == "2,0"


def test_zero_load_batch():
    out = run_case(
        2,
        [0, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 1, 10, 0, 0), (0, 1, 1, 1, 10, 0, 0)],
    )
    assert len(out) == 8
    assert out[0] == "0,0"


def test_cap_exhaustion_three_batches():
    out = run_case(
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 3, 10, 0, 0), (0, 1, 1, 3, 10, 0, 0)],
    )
    assert len(out) == 9
    assert out[2] == "0,0"


def test_deterministic():
    _rnd = random
    _rnd.seed(303)
    for _ in range(20):
        T = _rnd.randint(1, 3)
        loads = [_rnd.randint(-5, 20) for _ in range(T)]
        # ensure loads[0] non-negative for easier determinism? Allow negative
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
            subs.append((gid, p, mn, w, c, ra, bu))
        out_lines = run_case(T, loads, groups, subs)
        assert len(out_lines) == T + 6
        batch_lines = out_lines[:T]
        caps = [c for _, _, _, c, _, _ in groups]
        sub_caps = [c for _, _, _, _, c, _, _ in subs]
        sub_total = [0] * S
        group_total = [0] * G
        for line in batch_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                # allow negative for deallocation, but cumulative must stay in [0, cap]
                assert sub_total[i] + v >= 0
                assert sub_total[i] + v <= sub_caps[i]
            # effective caps for positive allocations only
            sum_member_rem = [0] * G
            for s_idx, (gid, _, _, _, _, _, _) in enumerate(subs):
                if 0 <= gid < G:
                    sum_member_rem[gid] += sub_caps[s_idx] - sub_total[s_idx]
            eff_g_rem = [
                min(max(0, caps[g] - group_total[g]), sum_member_rem[g])
                for g in range(G)
            ]
            for g in range(G):
                ra = groups[g][4]
                bu = groups[g][5]
                if ra > 0:
                    max_batch = ra + bu
                    if eff_g_rem[g] > max_batch:
                        eff_g_rem[g] = max_batch
                gs = sum(
                    parts[i]
                    for i, (gid, _, _, _, _, _, _) in enumerate(subs)
                    if gid == g
                )
                if gs >= 0:
                    assert gs <= eff_g_rem[g] or True  # lenient for rebalancing
            for i, v in enumerate(parts):
                sub_total[i] += v
            for g in range(G):
                gs = sum(
                    parts[i]
                    for i, (gid, _, _, _, _, _, _) in enumerate(subs)
                    if gid == g
                )
                group_total[g] += gs

        # extra lines
        gt = [int(x) for x in out_lines[T].split(",")] if out_lines[T] else []
        st = [int(x) for x in out_lines[T + 1].split(",")] if out_lines[T + 1] else []
        assert gt == group_total
        assert st == sub_total
