"""Black-box tests for hierarchical multi-batch allocator - harder balanced with fuzz and implicit corners."""

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


def test_priority_tie_and_order():
    out = run_case(1, [3], [(0, 0, 1, 10)], [(0, 10, 2, 1, 10), (0, 1, 2, 1, 10)])
    assert out == ["2,1"]
    out2 = run_case(1, [3], [(0, 0, 1, 10)], [(0, 5, 2, 1, 10), (0, 5, 2, 1, 10)])
    assert out2 == ["2,1"]


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
    # remaining * credit = 1e12 * 1e12 = 1e24 > 2^63-1, must use 128-bit
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


def test_fuzz_random():
    # Fuzz with Python reference - hard, catches subtle integration bugs
    import random as _rnd

    def allocate_batch(load, prio, mins, weights, caps, credits):
        n = len(weights)
        batch = [0] * n
        if n == 0 or load <= 0:
            for i in range(n):
                if caps[i] > 0:
                    if batch[i] > 0:
                        credits[i] = credits[i] // 2 + 1
                    else:
                        credits[i] += weights[i]
            return batch
        order = sorted(range(n), key=lambda i: (-prio[i], i))
        rem = load
        for oi in order:
            if rem == 0:
                break
            if caps[oi] <= 0:
                continue
            give = mins[oi]
            if give > caps[oi]:
                give = caps[oi]
            if give > rem:
                give = rem
            batch[oi] += give
            rem -= give
        rem_cap = [caps[i] - batch[i] for i in range(n)]
        alloc_w = [0] * n
        credit_tmp = credits[:]
        rem_w = rem
        while rem_w > 0:
            active = [i for i in range(n) if alloc_w[i] < rem_cap[i]]
            if not active:
                break
            total = sum(credit_tmp[i] for i in active)
            if total == 0:
                while rem_w > 0:
                    cur_active = [i for i in active if alloc_w[i] < rem_cap[i]]
                    if not cur_active:
                        break
                    min_rem = min(rem_cap[i] - alloc_w[i] for i in cur_active)
                    cycles = min(min_rem, rem_w // len(cur_active))
                    if cycles > 0:
                        for i in cur_active:
                            alloc_w[i] += cycles
                        rem_w -= cycles * len(cur_active)
                    made = False
                    for i in cur_active:
                        if rem_w == 0:
                            break
                        if alloc_w[i] < rem_cap[i]:
                            alloc_w[i] += 1
                            rem_w -= 1
                            made = True
                    if not made:
                        break
                break
            delta = [0] * n
            used = 0
            for i in active:
                share = (rem_w * credit_tmp[i]) // total
                if share > rem_cap[i] - alloc_w[i]:
                    share = rem_cap[i] - alloc_w[i]
                alloc_w[i] += share
                delta[i] = share
                used += share
            if used == 0:
                best = active[0]
                for i in active[1:]:
                    if credit_tmp[i] > credit_tmp[best]:
                        best = i
                alloc_w[best] += 1
                delta[best] = 1
                used = 1
            rem_w -= used
            for i in active:
                if delta[i] > 0:
                    credit_tmp[i] = credit_tmp[i] // 2 + 1
                else:
                    credit_tmp[i] += weights[i]
        for i in range(n):
            batch[i] += alloc_w[i]
        for i in range(n):
            if caps[i] > 0:
                if batch[i] > 0:
                    credits[i] = credits[i] // 2 + 1
                else:
                    credits[i] += weights[i]
        return batch

    def hier_multi(T, loads, groups, subs):
        G = len(groups)
        S = len(subs)
        gp = [g[0] for g in groups]
        gmin = [g[1] for g in groups]
        gw = [g[2] for g in groups]
        gc = [g[3] for g in groups]
        sg = [s[0] for s in subs]
        sp = [s[1] for s in subs]
        smin = [s[2] for s in subs]
        sw = [s[3] for s in subs]
        sc = [s[4] for s in subs]
        gtot = [0] * G
        stot = [0] * S
        gcred = [w for w in gw]
        scred = [w for w in sw]
        all_batches = []
        for t in range(T):
            L = loads[t]
            grem = [max(0, gc[g] - gtot[g]) for g in range(G)]
            srem = [max(0, sc[s] - stot[s]) for s in range(S)]
            sum_mem = [0] * G
            for s in range(S):
                gid = sg[s]
                if 0 <= gid < G:
                    sum_mem[gid] += s_rem[s] if (s_rem := srem)[s] else 0
            eff = [
                min(grem[g], sum_member_rem[g])
                for g, sum_member_rem in enumerate([sum_mem])
                for g in range(G)
            ][0:G]  # placeholder to keep same as reference, but we need correct
            # Actually recompute correctly
            sum_mem = [0] * G
            for s in range(S):
                gid = sg[s]
                if 0 <= gid < G:
                    sum_mem[gid] += s_rem[s]
            eff = [min(grem[g], sum_mem[g]) for g in range(G)]
            g_batch = allocate_batch(L, gp, gmin, gw, eff, gcred)
            for g in range(G):
                gtot[g] += g_batch[g]
            sub_batch = [0] * S
            for g in range(G):
                idxs = [i for i in range(S) if sg[i] == g]
                if not idxs:
                    continue
                gl = g_batch[g]
                if gl <= 0:
                    continue
                mp = [sp[i] for i in idxs]
                mmin = [smin[i] for i in idxs]
                mw = [sw[i] for i in idxs]
                mcap = [s_rem[i] for i in idxs]
                mcred = [scred[i] for i in idxs]
                alloc_in = allocate_batch(
                    gl, mp, mmin, mw, m_cap := m_cap, m_credit := m_credit
                )
                for lp, gi in enumerate(idxs):
                    scred[gi] = m_credit[lp]
                    sub_batch[gi] = alloc_in[lp]
            for s in range(S):
                stot[s] += sub_batch[s]
            all_batches.append(sub_batch[:])
        return all_batches

    # Use same reference as before but simplified for fuzz - reuse hierarchical_multi_batch from earlier gen
    # For fuzz we will use Python reference that matches Go solution exactly (overflow not needed as Python big ints)
    # We'll implement hierarchical_multi_batch again correctly

    def hier_ref(T, loads, groups, subs):
        G = len(groups)
        S = len(subs)
        gp = [g[0] for g in groups]
        gmin = [g[1] for g in groups]
        gw = [g[2] for g in groups]
        gc = [g[3] for g in groups]
        sg = [s[0] for s in subs]
        sp = [s[1] for s in subs]
        smin = [s[2] for s in subs]
        sw = [s[3] for s in subs]
        sc = [s[4] for s in subs]
        gtot = [0] * G
        stot = [0] * S
        gcred = [w for w in gw]
        scred = [w for w in sw]
        out = []
        for t in range(T):
            L = loads[t]
            grem = [max(0, gc[g] - gtot[g]) for g in range(G)]
            srem = [max(0, sc[s] - stot[s]) for s in range(S)]
            sum_mem = [0] * G
            for s in range(S):
                if 0 <= sg[s] < G:
                    sum_mem[sg[s]] += srem[s]
            eff = [min(grem[g], sum_mem[g]) for g in range(G)]
            g_batch = allocate_batch(L, gp, gmin, gw, eff, gcred)
            for g in range(G):
                gtot[g] += g_batch[g]
            sub_batch = [0] * S
            for g in range(G):
                idxs = [i for i in range(S) if sg[i] == g]
                if not idxs or g_batch[g] <= 0:
                    continue
                mp = [sp[i] for i in idxs]
                mmin = [smin[i] for i in idxs]
                mw = [sw[i] for i in idxs]
                mcap = [srem[i] for i in idxs]
                mcred = [scred[i] for i in idxs]
                alloc_in = allocate_batch(g_batch[g], mp, mmin, mw, mcap, mcred)
                for lp, gi in enumerate(idxs):
                    scred[gi] = mcred[lp]
                    sub_batch[gi] = alloc_in[lp]
            for s in range(S):
                stot[s] += sub_batch[s]
            out.append(sub_batch[:])
        return out

    _rnd.seed(999)
    for _ in range(20):
        T = _rnd.randint(1, 3)
        loads = [_rnd.randint(0, 30) for _ in range(T)]
        G = _rnd.randint(1, 3)
        groups = []
        for _ in range(G):
            p = _rnd.randint(0, 10)
            mn = _rnd.randint(0, 3)
            w = _rnd.randint(1, 8)
            c = _rnd.randint(0, 20)
            groups.append((p, mn, w, c))
        S = _rnd.randint(1, G * 3 + 2)
        subs = []
        for _ in range(S):
            gid = _rnd.randint(0, G - 1)
            p = _rnd.randint(0, 10)
            mn = _rnd.randint(0, 2)
            w = _rnd.randint(1, 6)
            c = _rnd.randint(0, 12)
            subs.append((gid, p, mn, w, c))
        expected_batches = hier_ref(T, loads, groups, subs)
        expected_lines = [",".join(map(str, b)) for b in expected_batches]
        got_lines = run_case(T, loads, groups, subs)
        assert got_lines == expected_lines, (
            f"fuzz mismatch T={T} loads={loads} groups={groups} subs={subs}\nGot {got_lines}\nExp {expected_lines}"
        )
