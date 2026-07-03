"""Verify the DR-buffer program at /app.

The specification lives in /app/docs/policy.md (units, three per-field input
encodings, the 90% usable-capacity limit, capacity-capped water-filling
redistribution, the output contract, validation) and /app/docs/incident.md (a
worked example). At runtime the tool reads a JSON report whose per-region
capacity/demand may each be a unit-suffixed number, a {value,unit} object, or a
"<number> <unit>" string (units rps / kqps=1000 rps / rpm=rps/60), normalizes to
rps, applies single-region water-filling, and prints per-region results in rps.

The grader rebuilds the agent's Go source and drives the compiled CLI as a black
box. Test inputs use mixed units/encodings so a solution must actually normalize.
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


def check(reg, capacity, demand, worst_incoming, util_pct, violates, dr_buffer, overflow):
    # capacity/demand must be reported normalized to rps
    assert reg["capacity"] == pytest.approx(capacity, abs=TOL), reg
    assert reg["demand"] == pytest.approx(demand, abs=TOL), reg
    assert reg["worstIncoming"] == pytest.approx(worst_incoming, abs=TOL), reg
    assert reg["utilizationPct"] == pytest.approx(util_pct, abs=TOL), reg
    assert reg["violates"] is violates, reg
    assert reg["drBuffer"] == pytest.approx(dr_buffer, abs=TOL), reg
    assert reg["overflowOnFailure"] is overflow, reg


# ---------------------------------------------------------------------------
# Unit normalization + format reconciliation
# ---------------------------------------------------------------------------

def test_units_normalized_to_rps(binary):
    # Same three regions expressed three different ways; all normalize to
    # capacity 1000 rps, demand 100 rps. Losing one spreads +50 to each other.
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": "1 kqps", "demand_rps": 100},
        {"name": "B", "capacity_kqps": 1, "demand": "6000 rpm"},
        {"name": "C", "capacity": {"value": 60000, "unit": "rpm"},
         "demand": {"value": 0.1, "unit": "kqps"}},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is False
    r = by_name(out)
    for name in ("A", "B", "C"):
        check(r[name], 1000.0, 100.0, 150.0, 15.0, False, 150.0 / 0.9 - 100, False)


def test_borderline_mixed_encodings(binary):
    # A: 100 rps cap, 3000 rpm = 50 rps demand ; B: 0.2 kqps = 200 cap, 40 rps.
    # Losing B pushes A to exactly 90 (90% -> not a violation).
    out = compute(binary, {"regions": [
        {"name": "A", "capacity_rps": 100, "demand": {"value": 3000, "unit": "rpm"}},
        {"name": "B", "capacity": "0.2 kqps", "demand_rps": 40},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is False
    r = by_name(out)
    check(r["A"], 100.0, 50.0, 90.0, 90.0, False, 50.0, False)
    check(r["B"], 200.0, 40.0, 90.0, 45.0, False, 60.0, False)


def test_per_field_unit_mismatch_respill_and_overflow(binary):
    # capacity and demand of the same region differ in unit. Normalized:
    # A 100/85, B 100/85, C 1000/100.
    #  - C fails: A,B cap at +5 (reach 90), 90 left over -> C's failure overflows.
    #  - A fails: B caps at +5, remaining 80 re-spills onto C -> C reaches 180.
    out = compute(binary, {"regions": [
        {"name": "A", "capacity": "6000 rpm", "demand": {"value": 0.085, "unit": "kqps"}},
        {"name": "B", "capacity_rps": 100, "demand_rps": 85},
        {"name": "C", "capacity": {"value": 1, "unit": "kqps"}, "demand": "6000 rpm"},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is True
    r = by_name(out)
    check(r["A"], 100.0, 85.0, 90.0, 90.0, False, 90.0 / 0.9 - 85, False)
    check(r["B"], 100.0, 85.0, 90.0, 90.0, False, 90.0 / 0.9 - 85, False)
    check(r["C"], 1000.0, 100.0, 180.0, 18.0, False, 180.0 / 0.9 - 100, True)


def test_zero_demand_with_respill(binary):
    # A 100/0, B 100/80, C 100/80 (mixed encodings).
    out = compute(binary, {"regions": [
        {"name": "A", "capacity_rps": 100, "demand_rps": 0},
        {"name": "B", "capacity": "6000 rpm", "demand": {"value": 0.08, "unit": "kqps"}},
        {"name": "C", "capacity_kqps": 0.1, "demand": "4800 rpm"},
    ]})
    assert out["anyViolation"] is False
    assert out["anyOverflow"] is False
    r = by_name(out)
    check(r["A"], 100.0, 0.0, 70.0, 70.0, False, 70.0 / 0.9 - 0, False)
    check(r["B"], 100.0, 80.0, 90.0, 90.0, False, 90.0 / 0.9 - 80, False)
    check(r["C"], 100.0, 80.0, 90.0, 90.0, False, 90.0 / 0.9 - 80, False)


def test_baseline_over_threshold_violates(binary):
    # A starts above 90% (95/100) -> violates on its own demand; when A fails its
    # 95 spreads evenly over B and C (+47.5 each).
    out = compute(binary, {"regions": [
        {"name": "A", "capacity_rps": 100, "demand": "5700 rpm"},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
        {"name": "C", "capacity_rps": 100, "demand_kqps": 0.01},
    ]})
    assert out["anyViolation"] is True
    assert out["anyOverflow"] is False
    r = by_name(out)
    check(r["A"], 100.0, 95.0, 95.0, 95.0, True, 95.0 / 0.9 - 95, False)
    check(r["B"], 100.0, 10.0, 57.5, 57.5, False, 57.5 / 0.9 - 10, False)
    check(r["C"], 100.0, 10.0, 57.5, 57.5, False, 57.5 / 0.9 - 10, False)


def test_region_order_preserved(binary):
    out = compute(binary, {"regions": [
        {"name": "z", "capacity_rps": 100, "demand_rps": 10},
        {"name": "a", "capacity_rps": 100, "demand_rps": 10},
        {"name": "m", "capacity_rps": 100, "demand_rps": 10},
    ]})
    assert [r["name"] for r in out["regions"]] == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# Invalid input: must exit non-zero (validation happens AFTER unit conversion)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"regions": [{"name": "A", "capacity_rps": 100, "demand_rps": 50}]},  # one region
    {"regions": []},  # empty
    # demand > capacity only after normalizing (0.15 kqps = 150 > 100 rps)
    {"regions": [
        {"name": "A", "capacity_rps": 100, "demand_kqps": 0.15},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},
    {"regions": [
        {"name": "A", "capacity_rps": 100, "demand_rps": -5},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # negative demand
    {"regions": [
        {"name": "A", "capacity_rps": 0, "demand_rps": 0},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # non-positive capacity
    {"regions": [
        {"name": "A", "capacity_rps": 100, "demand_rps": 10},
        {"name": "A", "capacity_rps": 100, "demand_rps": 10},
    ]},  # duplicate name
    {"regions": [
        {"name": "A", "capacity": "100 foo", "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # unrecognized unit
    {"regions": [
        {"name": "A", "capacity": 100, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # bare number, no unit
    {"regions": [
        {"name": "A", "capacity": {"value": 100}, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # object missing unit
    {"regions": [
        {"name": "A", "capacity_rps": 100},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # missing demand
    {"regions": [
        {"name": "A", "capacity_rps": 100, "capacity_kqps": 0.1, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10},
    ]},  # capacity specified twice
])
def test_invalid_input_exits_nonzero(binary, payload):
    p = run(binary, payload)
    assert p.returncode != 0, f"expected non-zero exit for {payload}"


def test_malformed_json_exits_nonzero(binary):
    p = subprocess.run(
        [binary], input="not json", capture_output=True, text=True, timeout=30
    )
    assert p.returncode != 0
