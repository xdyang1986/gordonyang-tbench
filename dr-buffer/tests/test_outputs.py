"""Verify the DR-buffer program at /app.

The specification lives in /app/docs/policy.md (units, three per-field input
encodings, the maxFailures envelope, the 90% usable-capacity limit, uncapped
proportional redistribution with cascading failures, the output contract, and
validation) and /app/docs/incident.md (a worked cascade example). At runtime the
tool reads {"maxFailures": K, "regions": [...]} whose per-region capacity/demand
each use one of three encodings and mixed units (rps / kqps=1000 rps / rpm=rps/60),
normalizes to rps, then over every failing set of size 1..K reports each region's
worst immediate (pre-cascade) load and whether it is overwhelmed, the fleet's
resilience and capacity shortfall, and the worst cascade scenario.

The grader rebuilds the agent's Go source and drives the compiled CLI as a black
box on mixed-unit inputs.
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


def reg(r, capacity, demand, worst_incoming, violates):
    assert r["capacity"] == pytest.approx(capacity, abs=TOL), r
    assert r["demand"] == pytest.approx(demand, abs=TOL), r
    assert r["worstIncoming"] == pytest.approx(worst_incoming, abs=TOL), r
    assert r["utilizationPct"] == pytest.approx(100 * worst_incoming / capacity, abs=TOL), r
    assert r["violates"] is violates, r
    assert r["drBuffer"] == pytest.approx(worst_incoming / 0.9 - demand, abs=TOL), r


# ---------------------------------------------------------------------------
# Single-failure cascade (K=1)
# ---------------------------------------------------------------------------

def test_single_failure_cascade(binary):
    # A,B are hot (80/100), C,D cold (10/100). Losing A dumps 80 across B,C,D
    # (proportional): B -> 80 + 80*100/300 = 106.67 > 90, so B is overwhelmed and
    # cascades. After A,B are gone, C,D each carry 10 + 160*100/200 = 90 (exactly
    # usable -> safe). So the worst single failure collapses two regions.
    out = compute(binary, {"maxFailures": 1, "regions": [
        {"name": "A", "capacity_rps": 100, "demand_rps": 80},
        {"name": "B", "capacity_rps": 100, "demand_rps": 80},
        {"name": "C", "capacity_rps": 100, "demand_rps": 10},
        {"name": "D", "capacity_rps": 100, "demand_rps": 10},
    ]})
    assert out["maxFailures"] == 1
    assert out["resilient"] is False
    hot = 80 + 80 * 100 / 300  # 106.666... A/B worst immediate load
    assert out["capacityShortfall"] == pytest.approx(2 * (hot / 0.9 - 100), abs=TOL)
    assert out["worstScenario"] == {
        "failed": ["A"], "collapsed": ["A", "B"], "cascadeRounds": 1}


def test_single_failure_cascade_values(binary):
    out = compute(binary, {"maxFailures": 1, "regions": [
        {"name": "A", "capacity_rps": 100, "demand_rps": 80},
        {"name": "B", "capacity_rps": 100, "demand_rps": 80},
        {"name": "C", "capacity_rps": 100, "demand_rps": 10},
        {"name": "D", "capacity_rps": 100, "demand_rps": 10},
    ]})
    r = by_name(out)
    hot = 80 + 80 * 100 / 300  # 106.666...
    cold = 10 + 80 * 100 / 300  # 36.666...
    reg(r["A"], 100, 80, hot, True)
    reg(r["B"], 100, 80, hot, True)
    reg(r["C"], 100, 10, cold, False)
    reg(r["D"], 100, 10, cold, False)


# ---------------------------------------------------------------------------
# Multi-failure envelope (K=2) with full collapse
# ---------------------------------------------------------------------------

def test_two_failures_full_collapse(binary):
    # Same fleet, K=2. Failing {A,C} (80+10) overwhelms B (80 + 90*100/200 = 125),
    # then the remaining region falls too -> total collapse in 2 cascade rounds.
    # Among all size-2 sets that fully collapse, {A,C} is lexicographically first.
    out = compute(binary, {"maxFailures": 2, "regions": [
        {"name": "A", "capacity_rps": 100, "demand_rps": 80},
        {"name": "B", "capacity_rps": 100, "demand_rps": 80},
        {"name": "C", "capacity_rps": 100, "demand_rps": 10},
        {"name": "D", "capacity_rps": 100, "demand_rps": 10},
    ]})
    assert out["maxFailures"] == 2
    assert out["resilient"] is False
    assert out["worstScenario"] == {
        "failed": ["A", "C"],
        "collapsed": ["A", "B", "C", "D"],
        "cascadeRounds": 2,
    }
    r = by_name(out)
    # A's worst immediate load: fail {B,C} or {B,D} -> 80 + 90*100/200 = 125.
    reg(r["A"], 100, 80, 125.0, True)
    reg(r["B"], 100, 80, 125.0, True)
    # C's worst immediate load: fail {A,B} -> 10 + 160*100/200 = 90 (not a violation).
    reg(r["C"], 100, 10, 90.0, False)
    reg(r["D"], 100, 10, 90.0, False)


# ---------------------------------------------------------------------------
# Resilient fleet (no violation, no cascade, zero shortfall)
# ---------------------------------------------------------------------------

def test_resilient_fleet_mixed_units(binary):
    # Three big regions expressed in three different units, all = 1000 rps / 100.
    # Losing any one adds +50 to each survivor; nobody is overwhelmed.
    out = compute(binary, {"maxFailures": 1, "regions": [
        {"name": "A", "capacity": "1 kqps", "demand_rps": 100},
        {"name": "B", "capacity_kqps": 1, "demand": "6000 rpm"},
        {"name": "C", "capacity": {"value": 60000, "unit": "rpm"},
         "demand": {"value": 0.1, "unit": "kqps"}},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    assert out["worstScenario"] == {"failed": [], "collapsed": [], "cascadeRounds": 0}
    r = by_name(out)
    for name in ("A", "B", "C"):
        reg(r[name], 1000.0, 100.0, 150.0, False)


def test_mixed_encodings_and_per_field_units(binary):
    # capacity/demand of the same region differ in unit; K=1.
    # Normalized: A 100/85, B 100/85, C 1000/100.
    out = compute(binary, {"maxFailures": 1, "regions": [
        {"name": "A", "capacity": "6000 rpm", "demand": {"value": 0.085, "unit": "kqps"}},
        {"name": "B", "capacity_rps": 100, "demand_rps": 85},
        {"name": "C", "capacity": {"value": 1, "unit": "kqps"}, "demand": "6000 rpm"},
    ]})
    r = by_name(out)
    # Fail C (100 rps): survivors are A,B (capAlive 200), so A -> 85 + 100*100/200
    # = 135 > 90 -> violates.
    a_worst = 85 + 100 * 100 / 200
    reg(r["A"], 100.0, 85.0, a_worst, True)
    reg(r["B"], 100.0, 85.0, a_worst, True)
    # C's worst: fail A or B (85 rps) -> 100 + 85*1000/1100 = 177.27 (17.7% util).
    c_worst = 100 + 85 * 1000 / 1100
    reg(r["C"], 1000.0, 100.0, c_worst, False)
    assert out["resilient"] is False


def test_region_order_preserved(binary):
    out = compute(binary, {"maxFailures": 1, "regions": [
        {"name": "z", "capacity_rps": 100, "demand_rps": 10},
        {"name": "a", "capacity_rps": 100, "demand_rps": 10},
        {"name": "m", "capacity_rps": 100, "demand_rps": 10},
    ]})
    assert [r["name"] for r in out["regions"]] == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# Invalid input: must exit non-zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"regions": [  # missing maxFailures
        {"name": "A", "capacity_rps": 100, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 2, "regions": [  # maxFailures > regions-1
        {"name": "A", "capacity_rps": 100, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 0, "regions": [  # maxFailures < 1
        {"name": "A", "capacity_rps": 100, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # one region
        {"name": "A", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # demand > capacity after normalizing
        {"name": "A", "capacity_rps": 100, "demand_kqps": 0.15},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # negative demand
        {"name": "A", "capacity_rps": 100, "demand_rps": -5},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # non-positive capacity
        {"name": "A", "capacity_rps": 0, "demand_rps": 0},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # duplicate name
        {"name": "A", "capacity_rps": 100, "demand_rps": 10},
        {"name": "A", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # unrecognized unit
        {"name": "A", "capacity": "100 foo", "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # bare number, no unit
        {"name": "A", "capacity": 100, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # object missing unit
        {"name": "A", "capacity": {"value": 100}, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
    {"maxFailures": 1, "regions": [  # capacity specified twice
        {"name": "A", "capacity_rps": 100, "capacity_kqps": 0.1, "demand_rps": 10},
        {"name": "B", "capacity_rps": 100, "demand_rps": 10}]},
])
def test_invalid_input_exits_nonzero(binary, payload):
    p = run(binary, payload)
    assert p.returncode != 0, f"expected non-zero exit for {payload}"


def test_malformed_json_exits_nonzero(binary):
    p = subprocess.run(
        [binary], input="not json", capture_output=True, text=True, timeout=30
    )
    assert p.returncode != 0
