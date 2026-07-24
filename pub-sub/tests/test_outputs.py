"""Ultimate hard tests for hierarchical allocator with burst, cost, dynamic weights, priority aging, negative deallocation, global rebalancing, T+8 output - very hard. Improved test quality: no vacuous, backward-compat, exact fuzz vs ref."""

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


# ---------- Python reference matching Go ----------
def allocate_batch_py(load, items):
    n = len(items)
    batch = [0] * n
    if n == 0 or load <= 0:
        return batch
    order = sorted(range(n), key=lambda i: (-items[i]["priority"], items[i]["idx"]))
    rem = load
    for oi in order:
        if rem == 0:
            break
        if items[oi]["cap"] <= 0:
            continue
        give = items[oi]["min"]
        if give > items[oi]["cap"]:
            give = items[oi]["cap"]
        if give > rem:
            give = rem
        batch[oi] += give
        rem -= give
    remCap = [
        items[i]["cap"] - batch[i] if items[i]["cap"] - batch[i] > 0 else 0
        for i in range(n)
    ]
    allocW = [0] * n
    creditTmp = [items[i]["credit"] for i in range(n)]
    remW = rem
    while remW > 0:
        active = [i for i in range(n) if allocW[i] < remCap[i]]
        if not active:
            break
        total = sum(creditTmp[i] for i in active)
        if total == 0:
            while remW > 0:
                curActive = [i for i in active if allocW[i] < remCap[i]]
                if not curActive:
                    break
                minRem = min(remCap[i] - allocW[i] for i in curActive)
                cycles = minRem
                if cycles > remW // len(curActive):
                    cycles = remW // len(curActive)
                if cycles > 0:
                    for i in curActive:
                        allocW[i] += cycles
                    remW -= cycles * len(curActive)
                made = False
                for i in curActive:
                    if remW == 0:
                        break
                    if allocW[i] < remCap[i]:
                        allocW[i] += 1
                        remW -= 1
                        made = True
                if not made:
                    break
            break
        delta = [0] * n
        used = 0
        for i in active:
            share = (remW * creditTmp[i]) // total
            if share > remCap[i] - allocW[i]:
                share = remCap[i] - allocW[i]
            allocW[i] += share
            delta[i] = share
            used += share
        if used == 0:
            best = active[0]
            for i in active[1:]:
                if creditTmp[i] > creditTmp[best] or (
                    creditTmp[i] == creditTmp[best]
                    and items[i]["idx"] < items[best]["idx"]
                ):
                    best = i
            allocW[best] += 1
            delta[best] = 1
            used = 1
        remW -= used
        for i in active:
            if delta[i] > 0:
                creditTmp[i] = creditTmp[i] // 2 + 1
            else:
                creditTmp[i] += items[i]["weight"]
    for i in range(n):
        batch[i] += allocW[i]
    return batch


def run_allocator_py(T, loads, groups, subs):
    G = len(groups)
    S = len(subs)
    groupPBase = [g[0] for g in groups]
    groupMin = [g[1] for g in groups]
    groupW = [g[2] for g in groups]
    groupC = [g[3] for g in groups]
    groupRate = [g[4] for g in groups]
    groupBurst = [g[5] for g in groups]
    groupCredit = [g[2] for g in groups]
    groupBurstRem = [g[5] for g in groups]
    groupStreak = [0] * G

    subG = [s[0] for s in subs]
    subPBase = [s[1] for s in subs]
    subMin = [s[2] for s in subs]
    subW = [s[3] for s in subs]
    subC = [s[4] for s in subs]
    subRate = [s[5] for s in subs]
    subBurst = [s[6] for s in subs]
    subCost = [s[7] for s in subs]
    subCredit = [s[3] for s in subs]
    subBurstRem = [s[6] for s in subs]
    subStreak = [0] * S

    groupTotalCost = [0] * G
    subTotalCost = [0] * S
    allBatches = []

    for t in range(T):
        load = loads[t]
        groupPEff = [groupPBase[g] + groupStreak[g] // 2 for g in range(G)]
        subPEff = [subPBase[s] + subStreak[s] // 2 for s in range(S)]

        gRemCostStart = [max(0, groupC[g] - groupTotalCost[g]) for g in range(G)]
        sRemCostStart = [max(0, subC[s] - subTotalCost[s]) for s in range(S)]
        sEffCountStart = []
        for s in range(S):
            cost = subCost[s] if subCost[s] > 0 else 1
            remCount = sRemCostStart[s] // cost
            ra = subRate[s]
            if ra > 0:
                maxBatch = ra + subBurstRem[s]
                if maxBatch < remCount:
                    remCount = maxBatch
            sEffCountStart.append(remCount)
        sumMemberEffStart = [0] * G
        minCostInGroup = [10**18] * G
        for s in range(S):
            gid = subG[s]
            if 0 <= gid < G:
                sumMemberEffStart[gid] += sEffCountStart[s]
                if subCost[s] < minCostInGroup[gid]:
                    minCostInGroup[gid] = subCost[s]
        effGCountStart = []
        for g in range(G):
            has = False
            for s in range(S):
                if subG[s] == g:
                    has = True
                    break
            if not has:
                effGCountStart.append(0)
                continue
            if minCostInGroup[g] == 10**18:
                gRemCount = 0
            else:
                gRemCount = (
                    gRemCostStart[g] // minCostInGroup[g]
                    if minCostInGroup[g] > 0
                    else gRemCostStart[g]
                )
            c = gRemCount
            if sumMemberEffStart[g] < c:
                c = sumMemberEffStart[g]
            ra = groupRate[g]
            if ra > 0:
                maxBatch = ra + groupBurstRem[g]
                if maxBatch < c:
                    c = maxBatch
            effGCountStart.append(c)

        groupBatchCount = [0] * G
        subBatchCount = [0] * S

        if load < 0:
            remaining = -load
            gOrder = sorted(range(G), key=lambda g: (-groupPEff[g], g))
            for g in gOrder:
                if remaining == 0:
                    break
                idxs = [s for s in range(S) if subG[s] == g]
                idxs = sorted(idxs, key=lambda s: (-subPEff[s], s))
                for sIdx in idxs:
                    if remaining == 0:
                        break
                    cost = subCost[sIdx] if subCost[sIdx] > 0 else 1
                    sCount = subTotalCost[sIdx] // cost
                    sPossible = sCount + subBatchCount[sIdx]
                    if sPossible <= 0:
                        continue
                    dealloc = sPossible
                    if dealloc > remaining:
                        dealloc = remaining
                    subBatchCount[sIdx] -= dealloc
                    groupBatchCount[g] -= dealloc
                    remaining -= dealloc
        else:
            remaining = load
            firstIter = True
            for _ in range(10):
                if remaining <= 0:
                    break
                gRemCostIter = []
                for g in range(G):
                    rem = groupC[g] - groupTotalCost[g]
                    costAllocated = sum(
                        subBatchCount[s] * subCost[s] for s in range(S) if subG[s] == g
                    )
                    rem -= costAllocated
                    if rem < 0:
                        rem = 0
                    gRemCostIter.append(rem)
                sRemCostIter = []
                for s in range(S):
                    rem = subC[s] - subTotalCost[s] - subBatchCount[s] * subCost[s]
                    if rem < 0:
                        rem = 0
                    sRemCostIter.append(rem)
                sEffCountIter = []
                for s in range(S):
                    cost = subCost[s] if subCost[s] > 0 else 1
                    remCount = sRemCostIter[s] // cost
                    ra = subRate[s]
                    if ra > 0:
                        rateRem = ra + subBurstRem[s] - subBatchCount[s]
                        if rateRem < 0:
                            rateRem = 0
                        if rateRem < remCount:
                            remCount = rateRem
                    sEffCountIter.append(max(0, remCount))
                sumMemberEffIter = [0] * G
                minCostIter = [10**18] * G
                for s in range(S):
                    gid = subG[s]
                    if 0 <= gid < G:
                        sumMemberEffIter[gid] += sEffCountIter[s]
                        if subCost[s] < minCostIter[gid]:
                            minCostIter[gid] = subCost[s]
                effGCountIter = []
                for g in range(G):
                    has = False
                    for s in range(S):
                        if subG[s] == g:
                            has = True
                            break
                    if not has:
                        effGCountIter.append(0)
                        continue
                    if minCostIter[g] == 10**18:
                        gRemCount = 0
                    else:
                        gRemCount = (
                            gRemCostIter[g] // minCostIter[g]
                            if minCostIter[g] > 0
                            else gRemCostIter[g]
                        )
                    c = gRemCount
                    if sumMemberEffIter[g] < c:
                        c = sumMemberEffIter[g]
                    ra = groupRate[g]
                    if ra > 0:
                        rateRem = ra + groupBurstRem[g] - groupBatchCount[g]
                        if rateRem < 0:
                            rateRem = 0
                        if rateRem < c:
                            c = rateRem
                    effGCountIter.append(c)
                if sum(effGCountIter) == 0:
                    break
                gItems = []
                for g in range(G):
                    minVal = groupMin[g] if firstIter else 0
                    gItems.append(
                        {
                            "idx": g,
                            "priority": groupPEff[g],
                            "min": minVal,
                            "weight": groupW[g],
                            "cap": effGCountIter[g],
                            "credit": groupCredit[g],
                        }
                    )
                groupIter = allocate_batch_py(remaining, gItems)
                totalThisIter = 0
                for g in range(G):
                    gl = groupIter[g]
                    if gl <= 0:
                        continue
                    idxs = [s for s in range(S) if subG[s] == g]
                    if not idxs:
                        continue
                    mItems = []
                    for sIdx in idxs:
                        minVal = subMin[sIdx] if firstIter else 0
                        mItems.append(
                            {
                                "idx": sIdx,
                                "priority": subPEff[sIdx],
                                "min": minVal,
                                "weight": subW[sIdx],
                                "cap": sEffCountIter[sIdx],
                                "credit": subCredit[sIdx],
                            }
                        )
                    allocInGroup = allocate_batch_py(gl, mItems)
                    sumAlloc = 0
                    for i, sIdx in enumerate(idxs):
                        sumAlloc += allocInGroup[i]
                        subBatchCount[sIdx] += allocInGroup[i]
                    groupBatchCount[g] += sumAlloc
                    totalThisIter += sumAlloc
                remaining -= totalThisIter
                firstIter = False
                if totalThisIter == 0:
                    break
            for g in range(G):
                if groupRate[g] > 0 and groupBatchCount[g] > groupRate[g]:
                    excess = groupBatchCount[g] - groupRate[g]
                    if excess > groupBurstRem[g]:
                        excess = groupBurstRem[g]
                    groupBurstRem[g] -= excess
            for s in range(S):
                if subRate[s] > 0 and subBatchCount[s] > subRate[s]:
                    excess = subBatchCount[s] - subRate[s]
                    if excess > subBurstRem[s]:
                        excess = subBurstRem[s]
                    subBurstRem[s] -= excess

        for g in range(G):
            cost = sum(subBatchCount[s] * subCost[s] for s in range(S) if subG[s] == g)
            groupTotalCost[g] += cost
        for s in range(S):
            subTotalCost[s] += subBatchCount[s] * subCost[s]

        for g in range(G):
            eligible = effGCountStart[g] > 0 or (load < 0 and gRemCostStart[g] > 0)
            if not eligible:
                continue
            wOld = groupW[g]
            if groupBatchCount[g] != 0:
                groupCredit[g] = groupCredit[g] // 2 + 1
                newW = wOld * 9 // 10
                if newW < 1:
                    newW = 1
                groupW[g] = newW
                groupStreak[g] = 0
            else:
                groupCredit[g] = groupCredit[g] + wOld
                groupW[g] = wOld + 1
                groupStreak[g] += 1
        for s in range(S):
            gid = subG[s]
            if gid < 0 or gid >= G:
                continue
            eligible = sEffCountStart[s] > 0 or (load < 0 and sRemCostStart[s] > 0)
            if not eligible:
                continue
            wOld = subW[s]
            if subBatchCount[s] != 0:
                subCredit[s] = subCredit[s] // 2 + 1
                newW = wOld * 9 // 10
                if newW < 1:
                    newW = 1
                subW[s] = newW
                subStreak[s] = 0
            else:
                subCredit[s] = subCredit[s] + wOld
                subW[s] = wOld + 1
                subStreak[s] += 1

        allBatches.append(subBatchCount[:])

    out = []
    for b in allBatches:
        out.append(",".join(map(str, b)))
    out.append(",".join(map(str, groupTotalCost)))
    out.append(",".join(map(str, subTotalCost)))
    out.append(",".join(map(str, groupCredit)))
    out.append(",".join(map(str, subCredit)))
    out.append(",".join(map(str, groupBurstRem)))
    out.append(",".join(map(str, subBurstRem)))
    out.append(",".join(map(str, groupW)))
    out.append(",".join(map(str, subW)))
    return out


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
        ["1,0,0,4,1,2,0,2,0,1", "2,0,1,2,7,1,1,0,3,0"],
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
        2,
        [11, 6],
        [(4, 0, 2, 6, 0, 0), (4, 2, 1, 0, 0, 0), (2, 2, 3, 10, 0, 0)],
        [
            (0, 9, 2, 3, 8, 0, 0, 1),
            (1, 0, 0, 4, 6, 0, 0, 1),
            (2, 9, 1, 6, 10, 0, 0, 1),
            (2, 10, 1, 5, 8, 0, 0, 1),
        ],
        ["4,0,4,3", "2,0,2,1"],
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
        2,
        [10, 10],
        [(5, 0, 5, 10, 0, 0), (5, 0, 5, 10, 0, 0)],
        [
            (0, 10, 1, 5, 10, 0, 0, 1),
            (0, 1, 1, 5, 10, 0, 0, 1),
            (1, 5, 0, 4, 10, 0, 0, 1),
        ],
        ["3,2,5", "3,2,5"],
    ),
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
    # Legacy formats: 5-field groups and 6-field subs (burst 0 cost 1 defaults) – tests raw parse branches
    (
        1,
        [10],
        [(0, 0, 1, 10, 0)],
        [(0, 10, 0, 1, 5, 0), (0, 1, 0, 1, 5, 0)],
        ["5,5"],
    ),
    # Legacy 7-field subs (burst given cost default 1)
    (
        1,
        [10],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 0, 1, 5, 0, 0), (0, 1, 0, 1, 5, 0, 0)],
        ["5,5"],
    ),
]


@pytest.mark.parametrize("T,loads,groups,subs,expected", CASES)
def test_allocation(T, loads, groups, subs, expected):
    out_lines = run_case(T, loads, groups, subs)
    batch_lines = out_lines[:T]
    assert len(batch_lines) == len(expected), (
        f"expected {len(expected)} batch lines got {batch_lines} full {out_lines}"
    )
    for got, exp in zip(batch_lines, expected):
        assert got == exp
    # Ensure T+8 output present for ultimate hard
    assert len(out_lines) == T + 8


def test_conservation():
    for T, loads, groups, subs, _ in CASES:
        out_lines = run_case(T, loads, groups, subs)
        batch_lines = out_lines[:T]
        assert len(out_lines) == T + 8
        G = len(groups)
        S = len(subs)
        group_caps = [g[3] for g in groups]
        sub_caps = [s[4] for s in subs]
        sub_costs = [s[7] if len(s) >= 8 else 1 for s in subs]
        group_tot = [0] * G
        sub_tot_cost = [0] * S
        for line in batch_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert sub_tot_cost[i] + v * sub_costs[i] >= 0
                assert sub_tot_cost[i] + v * sub_costs[i] <= sub_caps[i]
            for i, v in enumerate(parts):
                sub_tot_cost[i] += v * sub_costs[i]
            for g in range(G):
                gs_cost = sum(
                    parts[i] * sub_costs[i]
                    for i, s in enumerate(subs)
                    if (s[0] if len(s) > 0 else -1) == g
                )
                group_tot[g] += gs_cost
        group_tot_line = (
            [int(x) for x in out_lines[T].split(",")] if out_lines[T] else []
        )
        sub_tot_line = (
            [int(x) for x in out_lines[T + 1].split(",")] if out_lines[T + 1] else []
        )
        assert group_tot_line == group_tot
        assert sub_tot_line == sub_tot_cost
        assert len(out_lines[T + 2].split(",")) == G
        assert len(out_lines[T + 3].split(",")) == S
        assert len(out_lines[T + 4].split(",")) == G
        assert len(out_lines[T + 5].split(",")) == S
        assert len(out_lines[T + 6].split(",")) == G
        assert len(out_lines[T + 7].split(",")) == S
        assert all(int(x) >= 1 for x in out_lines[T + 2].split(","))
        assert all(int(x) >= 1 for x in out_lines[T + 3].split(","))
        assert all(int(x) >= 0 for x in out_lines[T + 4].split(","))
        assert all(int(x) >= 0 for x in out_lines[T + 5].split(","))
        assert all(int(x) >= 1 for x in out_lines[T + 6].split(","))
        assert all(int(x) >= 1 for x in out_lines[T + 7].split(","))


def test_min_exceeds_cap():
    out = run_case(1, [5], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 2, 0, 0, 1)])
    assert out[0] == "2"
    assert out[1] == "2"
    assert out[2] == "2"
    assert out[5] == "0"
    assert out[6] == "0"
    assert len(out) == 9


def test_min_gt_rate():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 0, 1)])
    assert out[0] == "2"
    assert len(out) == 9


def test_min_gt_rate_with_burst():
    out = run_case(1, [10], [(0, 0, 1, 10, 0, 0)], [(0, 10, 10, 1, 10, 2, 3, 1)])
    assert out[0] == "5"
    assert out[6] == "0"
    assert out[5] == "0"


def test_priority_tie_and_order():
    out = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 2, 1, 10, 0, 0, 1), (0, 1, 2, 1, 10, 0, 0, 1)],
    )
    assert out[0] == "2,1"
    out2 = run_case(
        1,
        [3],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 5, 2, 1, 10, 0, 0, 1), (0, 5, 2, 1, 10, 0, 0, 1)],
    )
    assert out2[0] == "2,1"


def test_group_no_members():
    out = run_case(
        1, [10], [(0, 0, 5, 10, 0, 0), (0, 0, 5, 10, 0, 0)], [(0, 0, 0, 1, 5, 0, 0, 1)]
    )
    assert out[0] == "5"
    assert out[1] == "5,0"
    assert len(out) == 9


def test_invalid_gid():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 5, 0, 0, 1), (99, 0, 0, 1, 5, 0, 0, 1)],
    )
    # Out-of-range gid sub gets 0 allocation, excluded from group totals AND credit/weight/streak/burst updates – final credit, weight, burst_rem equal initial values
    assert out == ["5,0", "5", "5,0", "1", "1,1", "0", "0,0", "1", "1,1"]


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
    assert out[0] == "6,4,3,3"
    assert len(out) == 9
    assert out[1] == "10,6"
    assert out[2] == "6,4,3,3"


def test_large_numbers():
    out = run_case(
        1,
        [1000000000000],
        [(0, 0, 1, 1000000000000, 0, 0)],
        [(0, 0, 0, 1, 500000000000, 0, 0, 1), (0, 0, 0, 1, 500000000000, 0, 0, 1)],
    )
    assert out == [
        "500000000000,500000000000",
        "1000000000000",
        "500000000000,500000000000",
        "1",
        "1,1",
        "0",
        "0,0",
        "1",
        "1,1",
    ]


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
    assert out == [
        "500000000000,500000000000",
        "1000000000000",
        "500000000000,500000000000",
        "500000000001",
        "500000000001,500000000001",
        "0",
        "0,0",
        "900000000000",
        "900000000000,900000000000",
    ]


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
    # With weight 4e18, credit start 4e18, after alloc credit = floor(4e18/2)+1 = 2000000000000000001, weight = floor(4e18*0.9) = 3600000000000000000 (overflow-safe via mulDiv)
    assert out == [
        "2,1",
        "3",
        "2,1",
        "1",
        "2000000000000000001,2000000000000000001",
        "0",
        "0,0",
        "1",
        "3600000000000000000,3600000000000000000",
    ]


def test_rate_limiting():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 5, 0)],
        [(0, 0, 0, 1, 10, 2, 0, 1), (0, 0, 0, 1, 10, 2, 0, 1)],
    )
    assert out[0] == "2,2"
    assert len(out) == 9


def test_rate_with_burst():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out[0] == "3,2"
    assert out[5] == "0"
    assert out[6] == "2,0"


def test_zero_caps():
    out = run_case(1, [10], [(0, 0, 1, 0, 0, 0)], [(0, 0, 0, 1, 0, 0, 0, 1)])
    assert out[0] == "0"
    assert len(out) == 9


def test_global_rebalancing():
    out = run_case(
        1,
        [10],
        [(0, 0, 1, 10, 0, 0), (0, 0, 1, 10, 0, 0)],
        [(0, 0, 0, 1, 1, 0, 0, 1), (0, 0, 0, 1, 1, 0, 0, 1), (1, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out[0] == "1,1,8"
    assert out[1] == "2,8"
    assert len(out) == 9


def test_dynamic_weight_and_credit():
    out = run_case(
        2,
        [5, 5],
        [(0, 0, 10, 20, 0, 0)],
        [(0, 10, 1, 10, 20, 0, 0, 1), (0, 1, 1, 10, 20, 0, 0, 1)],
    )
    assert len(out) == 10
    assert out[0] == "3,2"
    assert out[1] == "3,2"
    group_weights = [int(x) for x in out[8].split(",")]
    sub_weights = [int(x) for x in out[9].split(",")]
    assert group_weights == [8]
    assert sub_weights == [8, 8]
    assert out[2] == "10"
    assert out[3] == "6,4"
    py_expected = run_allocator_py(
        2,
        [5, 5],
        [(0, 0, 10, 20, 0, 0)],
        [(0, 10, 1, 10, 20, 0, 0, 1), (0, 1, 1, 10, 20, 0, 0, 1)],
    )
    assert out == py_expected


def test_final_totals_and_credits_consistency():
    out = run_case(
        2,
        [6, 6],
        [(0, 0, 4, 11, 0, 0), (0, 0, 1, 6, 0, 0)],
        [
            (0, 5, 0, 4, 11, 0, 0, 1),
            (0, 1, 0, 1, 6, 0, 0, 1),
            (1, 10, 0, 2, 5, 0, 0, 1),
        ],
    )
    assert len(out) == 10
    batch1 = [int(x) for x in out[0].split(",")]
    batch2 = [int(x) for x in out[1].split(",")]
    group_totals = [int(x) for x in out[2].split(",")]
    sub_totals = [int(x) for x in out[3].split(",")]
    assert sub_totals[0] == batch1[0] + batch2[0]
    assert sub_totals[1] == batch1[1] + batch2[1]
    assert sub_totals[2] == batch1[2] + batch2[2]
    assert group_totals[0] == sub_totals[0] + sub_totals[1]
    assert group_totals[1] == sub_totals[2]
    assert out == [
        "4,1,1",
        "4,1,1",
        "10,2",
        "8,2,2",
        "2,1",
        "2,1,2",
        "0,0",
        "0,0,0",
        "2,1",
        "2,1,1",
    ]


def test_negative_deallocation():
    out = run_case(
        2,
        [6, -4],
        [(0, 0, 2, 10, 0, 0)],
        [(0, 5, 0, 2, 10, 0, 0, 1), (0, 1, 0, 2, 10, 0, 0, 1)],
    )
    assert out == ["3,3", "-3,-1", "2", "0,2", "2", "2,2", "0", "0,0", "1", "1,1"]


def test_burst_final_consistency():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 10, 2, 3)],
        [(0, 0, 0, 1, 10, 2, 3, 1), (0, 0, 0, 1, 10, 0, 0, 1)],
    )
    assert out == ["3,2", "5", "3,2", "1", "1,1", "0", "2,0", "1", "1,1"]


def test_cost_factor():
    out = run_case(
        1,
        [5],
        [(0, 0, 1, 20, 0, 0)],
        [(0, 0, 0, 1, 10, 0, 0, 2), (0, 0, 0, 1, 10, 0, 0, 5)],
    )
    assert out == ["3,2", "16", "6,10", "1", "1,1", "0", "0,0", "1", "1,1"]


def test_cost_factor_exact():
    out = run_case(
        1,
        [8],
        [(0, 0, 2, 20, 0, 0)],
        [(0, 10, 1, 2, 20, 0, 0, 2), (0, 1, 1, 2, 20, 0, 0, 3)],
    )
    assert out == ["4,4", "20", "8,12", "2", "2,2", "0", "0,0", "1", "1,1"]


def test_priority_aging():
    out = run_case(
        3,
        [1, 1, 1],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 5, 1, 1, 10, 0, 0, 1), (0, 5, 1, 1, 10, 0, 0, 1)],
    )
    assert out == ["1,0", "1,0", "0,1", "3", "2,1", "1", "2,3", "0", "0,0", "1", "2,2"]


def test_zero_load_batch():
    out = run_case(
        2,
        [0, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 1, 10, 0, 0, 1), (0, 1, 1, 1, 10, 0, 0, 1)],
    )
    assert len(out) == 10
    assert out[0] == "0,0"
    assert out[1] == "3,2"


def test_cap_exhaustion_three_batches():
    out = run_case(
        3,
        [5, 5, 5],
        [(0, 0, 1, 10, 0, 0)],
        [(0, 10, 1, 3, 10, 0, 0, 1), (0, 1, 1, 3, 10, 0, 0, 1)],
    )
    assert out == ["3,2", "3,2", "0,0", "10", "6,4", "1", "3,3", "0", "0,0", "1", "2,2"]


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
    assert len(out_old) == 9
    assert out_old[0] == "5,5"


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
    assert out_7[0] == "5,5"


def test_deterministic():
    rnd = random
    rnd.seed(303)
    for _ in range(20):
        T = rnd.randint(1, 3)
        loads = [rnd.randint(-5, 20) for _ in range(T)]
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
        loads = [rnd.randint(-5, 30) for _ in range(T)]
        G = rnd.randint(1, 3)
        groups = []
        for _ in range(G):
            p = rnd.randint(0, 10)
            mn = rnd.randint(0, 3)
            w = rnd.randint(1, 8)
            c = rnd.randint(0, 20)
            ra = rnd.choice([0, 0, rnd.randint(1, 10)])
            bu = rnd.choice([0, 0, rnd.randint(1, 5)])
            groups.append((p, mn, w, c, ra, bu))
        S = rnd.randint(1, G * 4 + 2)
        subs = []
        for _ in range(S):
            gid = rnd.randint(0, G - 1)
            p = rnd.randint(0, 10)
            mn = rnd.randint(0, 2)
            w = rnd.randint(1, 6)
            c = rnd.randint(0, 15)
            ra = rnd.choice([0, 0, rnd.randint(1, 10)])
            bu = rnd.choice([0, 0, rnd.randint(1, 5)])
            co = rnd.choice([1, 2, 3])
            subs.append((gid, p, mn, w, c, ra, bu, co))
        out_lines = run_case(T, loads, groups, subs)
        assert len(out_lines) == T + 8
        batch_lines = out_lines[:T]
        caps = [c for _, _, _, c, _, _ in groups]
        sub_caps = [c for _, _, _, _, c, _, _, _ in subs]
        sub_costs = [co for _, _, _, _, _, _, _, co in subs]
        sub_total_cost = [0] * S
        group_total_cost = [0] * G
        for line in batch_lines:
            parts = [int(x) for x in line.split(",")] if line else []
            assert len(parts) == S
            for i, v in enumerate(parts):
                assert sub_total_cost[i] + v * sub_costs[i] >= 0
                assert sub_total_cost[i] + v * sub_costs[i] <= sub_caps[i]
            for i, v in enumerate(parts):
                sub_total_cost[i] += v * sub_costs[i]
            for g in range(G):
                gs_cost = sum(
                    parts[i] * sub_costs[i] for i, s in enumerate(subs) if s[0] == g
                )
                group_total_cost[g] += gs_cost
        gt = [int(x) for x in out_lines[T].split(",")] if out_lines[T] else []
        st = [int(x) for x in out_lines[T + 1].split(",")] if out_lines[T + 1] else []
        assert gt == group_total_cost
        assert st == sub_total_cost


def test_fuzz_exact_vs_reference():
    rnd = random
    rnd.seed(999)
    for _ in range(20):
        T = rnd.randint(1, 3)
        loads = [rnd.randint(-10, 30) for _ in range(T)]
        G = rnd.randint(1, 3)
        groups = []
        for _ in range(G):
            p = rnd.randint(0, 5)
            mn = rnd.randint(0, 2)
            w = rnd.randint(1, 4)
            c = rnd.randint(0, 20)
            ra = rnd.choice([0, rnd.randint(1, 6)])
            bu = rnd.choice([0, rnd.randint(1, 4)])
            groups.append((p, mn, w, c, ra, bu))
        S = rnd.randint(1, 5)
        subs = []
        for _ in range(S):
            gid = rnd.randint(0, G - 1)
            p = rnd.randint(0, 5)
            mn = rnd.randint(0, 1)
            w = rnd.randint(1, 4)
            c = rnd.randint(0, 15)
            ra = rnd.choice([0, rnd.randint(1, 6)])
            bu = rnd.choice([0, rnd.randint(1, 4)])
            co = rnd.choice([1, 2])
            subs.append((gid, p, mn, w, c, ra, bu, co))
        go_out = run_case(T, loads, groups, subs)
        py_out = run_allocator_py(T, loads, groups, subs)
        assert go_out == py_out, (
            f"Mismatch Go vs Py ref for T={T} loads={loads} groups={groups} subs={subs}\nGo: {go_out}\nPy: {py_out}"
        )
