"""Verify the DR-buffer program at /app.

Capacity-capped single-region failure model: when a region fails, its demand is
absorbed by the survivors via capacity-proportional water-filling; no survivor
may be driven above its usable capacity (90% of installed), and any overflow
re-spills to survivors that still have headroom. If the survivors cannot absorb
the whole demand within usable capacity, that failure overflows. The grader
rebuilds the agent's Go solution from source (module or loose .go files) and
drives the compiled CLI as a black box.
"""

import glob
import json
import os
import shutil
import subprocess

import pytest

APP = "/app"
BIN = "/tmp/drbuffer_grade"
TOL = 1e-6


@pytest.fixture(scope="session")
def binary():
    go = shutil.which("go") or "/usr/local/go/bin/go"
    go_files = sorted(
        f for f in glob.glob(os.path.join(APP, "*.go"))
        if not os.path.basename(f).endswith("_test.go")
    )
    assert os.path.isfile(os.path.join(APP, "go.mod")) or go_files, (
        "expected a Go solution at /app (a go.mod module or .go source files)"
    )
    if os.path.isfile(os.path.join(APP, "go.mod")):
        cmd = [go, "build", "-o", BIN, "."]
    else:
        cmd = [go, "build", "-o", BIN] + go_files
    build = subprocess.run(cmd, cwd=APP, capture_output=True, text=True)
    assert build.returncode == 0, f"`go build` failed:\n{build.stderr}"
    assert os.path.isfile(BIN), "build did not produce a binary"
    return BIN


def run(binary, payload):
    return subprocess.run(
        [binary], input=json.dumps(payload), capture_output=True, text=True, timeout=30
    )


def compute(binary, payload):
    p = run(binary, payload)
    assert p.returncode == 0, f"expected success, got exit {p.returncode}:\n{p.stderr}"
    return json.loads(p.stdout)


def by_name(out):
    return {r["name"]: r for r in out["regions"]}


def check(reg, worst_incoming, util_pct, violates, dr_buffer, overflow):
    assert reg["worstIncoming"] == pytest.approx(worst_incoming, abs=TOL), reg
    assert reg["utilizationPct"] == pytest.approx(util_pct, abs=TOL), reg
    assert reg["violates"] is violates, reg
    assert reg["drBuffer"] == pytest.approx(dr_buffer, abs=TOL), reg
    assert reg["overflowOnFailure"] is overflow, reg


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------

def test_two_regions_borderline_90(binary):
    # Failing B dumps its 40 onto A -> A reaches exactly 90 (90% utilization),
    # which is NOT a violation (threshold is strict). No cap binds, so the
    # capped model coincides with plain proportional redistribution here.
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": 100, "demand": 50},
        {"name": "B", "capacity": 200, "demand": 40},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is False
    r = by_name(out)
    check(r["A"], 90.0, 90.0, False, 50.0, False)
    check(r["B"], 90.0, 45.0, False, 60.0, False)


def test_uncapped_symmetric(binary):
    # Large headroom everywhere: losing one region spreads its 100 evenly over
    # the two survivors (+50 each); no cap ever binds.
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": 1000, "demand": 100},
        {"name": "B", "capacity": 1000, "demand": 100},
        {"name": "C", "capacity": 1000, "demand": 100},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is False
    r = by_name(out)
    for name in ("A", "B", "C"):
        check(r[name], 150.0, 15.0, False, 150.0 / 0.9 - 100, False)


def test_respill_and_overflow(binary):
    # A and B are nearly full (usable headroom 5 each); C is large.
    #  - C fails (demand 100): A and B cap at +5 each (reach 90), leaving 90
    #    that nobody can absorb -> C's failure OVERFLOWS.
    #  - A fails (demand 85): B caps at +5 (reaches 90), the remaining 80
    #    re-spills entirely onto C -> C reaches 180. Feasible. (B symmetric.)
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": 100, "demand": 85},
        {"name": "B", "capacity": 100, "demand": 85},
        {"name": "C", "capacity": 1000, "demand": 100},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is True
    r = by_name(out)
    check(r["A"], 90.0, 90.0, False, 90.0 / 0.9 - 85, False)
    check(r["B"], 90.0, 90.0, False, 90.0 / 0.9 - 85, False)
    check(r["C"], 180.0, 18.0, False, 180.0 / 0.9 - 100, True)


def test_zero_demand_region_with_respill(binary):
    # B fails (demand 80): C can only take 10 (caps at 90), the remaining 70
    # re-spills onto the empty region A -> A reaches 70. (C symmetric.)
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": 100, "demand": 0},
        {"name": "B", "capacity": 100, "demand": 80},
        {"name": "C", "capacity": 100, "demand": 80},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is False
    r = by_name(out)
    check(r["A"], 70.0, 70.0, False, 70.0 / 0.9 - 0, False)
    check(r["B"], 90.0, 90.0, False, 90.0 / 0.9 - 80, False)
    check(r["C"], 90.0, 90.0, False, 90.0 / 0.9 - 80, False)


def test_baseline_over_threshold_violates(binary):
    # A starts above 90% utilization at steady state (95/100), so it cannot
    # absorb any load and it violates on its own baseline demand. When A fails,
    # its 95 spreads evenly over B and C (+47.5 each); B/C stay well under 90%.
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": 100, "demand": 95},
        {"name": "B", "capacity": 100, "demand": 10},
        {"name": "C", "capacity": 100, "demand": 10},
    ]})
    assert out["anyViolation"] is True
    assert out["anyOverflow"] is False
    r = by_name(out)
    check(r["A"], 95.0, 95.0, True, 95.0 / 0.9 - 95, False)
    check(r["B"], 57.5, 57.5, False, 57.5 / 0.9 - 10, False)
    check(r["C"], 57.5, 57.5, False, 57.5 / 0.9 - 10, False)


def test_order_independent(binary):
    # The water-filling allocation is unique; reordering the input must not
    # change any region's result (only the output order follows the input).
    regions = [
        {"name": "C", "capacity": 1000, "demand": 100},
        {"name": "A", "capacity": 100, "demand": 85},
        {"name": "B", "capacity": 100, "demand": 85},
    ]
    out = compute(binary, {"regions": regions})
    assert [r["name"] for r in out["regions"]] == ["C", "A", "B"]
    r = by_name(out)
    check(r["A"], 90.0, 90.0, False, 90.0 / 0.9 - 85, False)
    check(r["B"], 90.0, 90.0, False, 90.0 / 0.9 - 85, False)
    check(r["C"], 180.0, 18.0, False, 180.0 / 0.9 - 100, True)


def test_region_order_preserved(binary):
    out = compute(binary, {"regions": [
        {"name": "z", "capacity": 100, "demand": 10},
        {"name": "a", "capacity": 100, "demand": 10},
        {"name": "m", "capacity": 100, "demand": 10},
    ]})
    assert [r["name"] for r in out["regions"]] == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# Input validation: invalid input must exit non-zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"regions": [{"name": "A", "capacity": 100, "demand": 50}]},  # only one region
    {"regions": []},  # empty
    {"regions": [
        {"name": "A", "capacity": 100, "demand": 150},
        {"name": "B", "capacity": 100, "demand": 10},
    ]},  # demand > capacity
    {"regions": [
        {"name": "A", "capacity": 100, "demand": -5},
        {"name": "B", "capacity": 100, "demand": 10},
    ]},  # negative demand
    {"regions": [
        {"name": "A", "capacity": 0, "demand": 0},
        {"name": "B", "capacity": 100, "demand": 10},
    ]},  # non-positive capacity
    {"regions": [
        {"name": "A", "capacity": 100, "demand": 10},
        {"name": "A", "capacity": 100, "demand": 10},
    ]},  # duplicate name
])
def test_invalid_input_exits_nonzero(binary, payload):
    p = run(binary, payload)
    assert p.returncode != 0, f"expected non-zero exit for {payload}"


def test_malformed_json_exits_nonzero(binary):
    p = subprocess.run(
        [binary], input="not json", capture_output=True, text=True, timeout=30
    )
    assert p.returncode != 0
