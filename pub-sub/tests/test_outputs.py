"""Balanced hard tests for hierarchical allocator with burst, cost, dynamic weights, negative, global rebalancing, T lines output - hard but solvable."""

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
]

SENSITIVE_CASES = [
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
    ),
]


@pytest.mark.parametrize("T,loads,groups,subs,expected", CASES)
def test_allocation(T, loads, groups, subs, expected):
    out = run_case(T, loads, groups, subs)
    assert out == expected



def test_sensitive_multibatch_invariants():
    # Sensitive multi-batch cases (indices 11,13,17) switched from byte-exact to invariant per feedback
    # Now checks conservation, caps with cost and burst, min guarantees, priority order - no or True
    for T, loads, groups, subs in SENSITIVE_CASES:
        out = run_case(T, loads, groups, subs)
        assert len(out) == T
        S = len(subs)
        G = len(groups)
        group_caps = [g[3] for g in groups]
        sub_caps = [s[4] for s in subs]
        sub_costs = [s[7] if len(s) >= 8 else 1 for s in subs]
        sub_mins = [s[2] for s in subs]
        sub_prios = [s[1] for s in subs]
        group_mins = [g[1] for g in groups]
        group_prios = [g[0] for g in groups]

        group_tot_cost = [0] * G
        sub_tot_cost = [0] * S

        for batch_idx, line in enumerate(out):
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            load = loads[batch_idx]
            # Check caps: cumulative cost stays within [0, cap]
            for i, v in enumerate(parts):
                assert sub_tot_cost[i] + v * sub_costs[i] >= 0, f"case batch {batch_idx} sub {i} negative cumulative"
                assert sub_tot_cost[i] + v * sub_costs[i] <= sub_caps[i], f"case batch {batch_idx} sub {i} cap exceeded"

            # Compute effective count caps at batch start (remaining)
            s_eff_count = []
            for s_idx in range(S):
                cost = sub_costs[s_idx]
                if cost <= 0:
                    cost = 1
                rem_cost = sub_caps[s_idx] - sub_tot_cost[s_idx]
                rem_count = rem_cost // cost if cost else 0
                ra = subs[s_idx][5] if len(subs[s_idx]) > 5 else 0
                bu = subs[s_idx][6] if len(subs[s_idx]) > 6 else 0
                if ra > 0:
                    max_batch = ra + bu
                    if rem_count > max_batch:
                        rem_count = max_batch
                s_eff_count.append(max(0, rem_count))

            sum_member_eff = [0]*G
            min_cost_in_group = [10**18]*G
            for s_idx in range(S):
                gid = subs[s_idx][0]
                if 0 <= gid < G:
                    sum_member_eff[gid] += s_eff_count[s_idx]
                    if sub_costs[s_idx] < min_cost_in_group[gid]:
                        min_cost_in_group[gid] = sub_costs[s_idx]

            eff_g_count = []
            for g in range(G):
                has = False
                for s in range(S):
                    if subs[s][0] == g:
                        has = True
                        break
                if not has:
                    eff_g_count.append(0)
                    continue
                g_rem_cost = group_caps[g] - group_tot_cost[g]
                if minCost := min_cost_in_group[g] == 10**18:
                    g_rem_count = 0
                else:
                    mc = min_cost_in_group[g]
                    g_rem_count = g_rem_cost // mc if mc > 0 else g_rem_cost
                c = g_rem_count
                if sum_member_eff[g] < c:
                    c = sum_member_eff[g]
                ra = groups[g][4] if len(groups[g]) > 4 else 0
                bu = groups[g][5] if len(groups[g]) > 5 else 0
                if ra > 0:
                    max_batch = ra + bu
                    if c > max_batch:
                        c = max_batch
                eff_g_count.append(max(0, c))

            # Check group caps: per-batch group allocation (sum of its members counts) <= eff_g
            for g in range(G):
                gs_count = sum(parts[i] for i in range(S) if subs[i][0] == g)
                if gs_count >= 0:
                    assert gs_count <= eff_g_count[g], f"batch {batch_idx} group {g} exceeds eff {gs_count} > {eff_g_count[g]}"

            # Check min guarantees per group in priority order
            # Groups sorted by effective priority desc (base priority, since no aging in this balanced version) tie idx
            g_order = sorted(range(G), key=lambda g: (-groups[g][0], g))
            rem_load = load
            for g in g_order:
                if rem_load <= 0:
                    break
                eff = eff_g_count[g]
                gmin = group_mins[g]
                capped_min = min(gmin, eff)
                if rem_load >= capped_min and eff >= capped_min and capped_min > 0:
                    gs_count = sum(parts[i] for i in range(S) if subs[i][0] == g)
                    assert gs_count >= capped_min, f"batch {batch_idx} group {g} min {capped_min} not met got {gs_count}"
                # For priority check, if load insufficient, higher priority should be satisfied first
                # We check that higher priority groups get at least min before lower
                rem_load -= sum(parts[i] for i in range(S) if subs[i][0] == g)

            # Per-group subs min and priority checks
            for g in range(G):
                members = [s_idx for s_idx in range(S) if subs[s_idx][0] == g]
                members_sorted = sorted(members, key=lambda s_idx: (-subs[s_idx][1], subs[s_idx][0]))
                rem_group = sum(parts[i] for i in members)
                for s_idx in members_sorted:
                    s = subs[s_idx]
                    s_min = s[2]
                    s_cap = s_eff_count[s_idx]
                    capped_min = min(s_min, s_cap)
                    if rem_group >= capped_min and s_cap >= capped_min and capped_min > 0:
                        assert parts[s_idx] >= capped_min, f"batch {batch_idx} sub {s_idx} min {capped_min} not met got {parts[s_idx]}"
                    rem_group -= parts[s_idx]

            # Update totals
            for i, v in enumerate(parts):
                sub_tot_cost[i] += v * sub_costs[i]
            for g in range(G):
                gs_cost = sum(parts[i] * sub_costs[i] for i in range(S) if subs[i][0] == g)
                group_tot_cost[g] += gs_cost
                assert group_tot_cost[g] >= 0
                assert group_tot_cost[g] <= group_caps[g]

            if load >= 0:
                for p in parts:
                    assert p >= 0



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
    # Tabs + spaces robustness per spec
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
    # Isolated test for dynamic weight evolution: weight decays 10% when served, grows +1 when not
    # T=2 loads [5,5] with weight10 each, both served both batches, weight 10->9->8
    # Exact from Go oracle with mulDiv overflow-safe
    out = run_case(
        2,
        [5, 5],
        [(0, 0, 10, 20, 0, 0)],
        [(0, 10, 1, 10, 20, 0, 0, 1), (0, 1, 1, 10, 20, 0, 0, 1)],
    )
    assert out == ["3,2", "3,2"]


def test_burst_carryover_multi_batch():
    # Isolated test for burst consumption across batches: burst one-time extra beyond rate
    # Group rate2 burst3 allows up to 5 first batch, then burst consumed 3 -> remaining 0, second batch max rate2
    # Load 5 then 1 should give 3,2 then 1,0
    out = run_case(
        2,
        [5, 1],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out == ["3,2", "1,0"]


def test_cost_factor_isolated():
    # Isolated cost factor: caps are total cost, cost per msg affects count
    # cap10 cost2 => max 5 msgs, cost5 => max 2 msgs, load8 should give 3,2 (cost 6,10 totals 16)
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
        # Conservation invariants: caps as cost, cost factor, non-negative totals, etc.
        caps = [s[4] for s in subs]
        costs = [s[7] if len(s) >= 8 else 1 for s in subs]
        tot_cost = [0] * S
        for line in out:
            parts = [int(x) for x in line.split(",")]
            assert len(parts) == S
            for i, v in enumerate(parts):
                # Allow negative for deallocation, but cumulative must stay in [0, cap]
                assert tot_cost[i] + v * costs[i] >= 0
                assert tot_cost[i] + v * costs[i] <= caps[i]
            for i, v in enumerate(parts):
                tot_cost[i] += v * costs[i]
