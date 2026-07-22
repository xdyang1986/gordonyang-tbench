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
    out_lines = [
        ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip() != ""
    ]
    return out_lines


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


def test_fuzz_random():
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
        gr = [g[4] for g in groups]
        sg = [s[0] for s in subs]
        sp = [s[1] for s in subs]
        smin = [s[2] for s in subs]
        sw = [s[3] for s in subs]
        sc = [s[4] for s in subs]
        sr = [s[5] for s in subs]
        gtot = [0] * G
        stot = [0] * S
        gcred = [w for w in gw]
        scred = [w for w in sw]
        out = []
        for t in range(T):
            L = loads[t]
            grem = [max(0, gc[g] - gtot[g]) for g in range(G)]
            srem = [max(0, sc[s] - stot[s]) for s in range(S)]
            seff = [srem[s] if sr[s] == 0 else min(srem[s], sr[s]) for s in range(S)]
            sum_mem = [0] * G
            for s in range(S):
                if 0 <= sg[s] < G:
                    sum_mem[sg[s]] += seff[s]
            eff_g = [min(grem[g], sum_mem[g]) for g in range(G)]
            for g in range(G):
                if gr[g] > 0 and gr[g] < eff_g[g]:
                    eff_g[g] = gr[g]
            g_batch = allocate_batch(L, gp, gmin, gw, eff_g, gcred)
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
                mcap = [seff[i] for i in idxs]
                mcred = [scred[i] for i in idxs]
                alloc_in = allocate_batch(g_batch[g], mp, mmin, mw, mcap, mcred)
                for lp, gi in enumerate(idxs):
                    scred[gi] = mcred[lp]
                    sub_batch[gi] = alloc_in[lp]
            for s in range(S):
                stot[s] += sub_batch[s]
            out.append(sub_batch[:])
        return out

    _rnd.seed(2024)
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
            ra = _rnd.choice([0, 0, _rnd.randint(1, 8)])
            subs.append((gid, p, mn, w, c, ra))
        expected_batches = hier_multi(T, loads, groups, subs)
        expected_lines = [",".join(map(str, b)) for b in expected_batches]
        got_lines = run_case(T, loads, groups, subs)
        assert got_lines == expected_lines, (
            f"fuzz mismatch T={T} loads={loads} groups={groups} subs={subs}\nGot {got_lines}\nExp {expected_lines}"
        )
