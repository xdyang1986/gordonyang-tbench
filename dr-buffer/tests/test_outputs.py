"""Verify the DR failover analysis program at /app.

The grader rebuilds the agent's Go module from source (so the result must be a
real Go program), then exercises the compiled CLI as a black box: JSON in on
stdin, JSON out on stdout, non-zero exit on invalid input.
"""

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
    assert os.path.isfile(os.path.join(APP, "go.mod")), "expected a go.mod at /app"
    has_go = any(
        f.endswith(".go")
        for _, _, files in os.walk(APP)
        for f in files
    )
    assert has_go, "expected Go source (*.go) under /app"

    go = shutil.which("go") or "/usr/local/go/bin/go"
    build = subprocess.run(
        [go, "build", "-o", BIN, "."],
        cwd=APP,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, f"`go build` failed:\n{build.stderr}"
    assert os.path.isfile(BIN), "build did not produce a binary"
    return BIN


def run(binary, payload):
    return subprocess.run(
        [binary],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def compute(binary, payload):
    p = run(binary, payload)
    assert p.returncode == 0, f"expected success, got exit {p.returncode}:\n{p.stderr}"
    return json.loads(p.stdout)


def by_name(out):
    return {r["name"]: r for r in out["regions"]}


def check(reg, peak_load, peak_buffer):
    assert reg["peakLoad"] == pytest.approx(peak_load, abs=TOL), reg
    assert reg["peakBuffer"] == pytest.approx(peak_buffer, abs=TOL), reg


# ---------------------------------------------------------------------------
# Correctness — peak loads, resilience, capacity shortfall
# ---------------------------------------------------------------------------

def test_symmetric_boundary(binary):
    # Total headroom (100) exactly equals the largest capacity (100): resilient.
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 50},
        {"name": "B", "capacity": 100, "load": 50},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 50.0)
    check(r["B"], 100.0, 50.0)


def test_fractional_no_saturation(binary):
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 10},
        {"name": "B", "capacity": 200, "load": 10},
        {"name": "C", "capacity": 100, "load": 70},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0 / 3.0, 70.0 / 3.0)
    check(r["B"], 170.0 / 3.0, 140.0 / 3.0)
    check(r["C"], 75.0, 5.0)


def test_cascade_overflow_resilient(binary):
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 95},
        {"name": "B", "capacity": 100, "load": 20},
        {"name": "C", "capacity": 100, "load": 20},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 5.0)
    check(r["B"], 67.5, 47.5)
    check(r["C"], 67.5, 47.5)


def test_cascade_and_unsurvivable(binary):
    # Failing C (load 150) exceeds total survivor headroom -> not resilient;
    # shortfall = 200 (largest cap) - 150 (total headroom) = 50.
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 90},
        {"name": "B", "capacity": 100, "load": 10},
        {"name": "C", "capacity": 200, "load": 150},
    ]})
    assert out["resilient"] is False
    assert out["capacityShortfall"] == pytest.approx(50.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 10.0)
    check(r["B"], 100.0, 90.0)
    check(r["C"], 200.0, 50.0)


def test_k_two_multiround_cascade(binary):
    out = compute(binary, {"k": 2, "regions": [
        {"name": "A", "capacity": 100, "load": 95},
        {"name": "B", "capacity": 100, "load": 95},
        {"name": "C", "capacity": 100, "load": 10},
        {"name": "D", "capacity": 300, "load": 30},
    ]})
    assert out["resilient"] is False
    assert out["capacityShortfall"] == pytest.approx(30.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 5.0)
    check(r["B"], 100.0, 5.0)
    check(r["C"], 100.0, 90.0)
    check(r["D"], 172.5, 142.5)


def test_k_zero_no_failover(binary):
    out = compute(binary, {"k": 0, "regions": [
        {"name": "A", "capacity": 100, "load": 90},
        {"name": "B", "capacity": 100, "load": 10},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 90.0, 0.0)
    check(r["B"], 10.0, 0.0)


def test_region_order_preserved(binary):
    out = compute(binary, {"k": 1, "regions": [
        {"name": "z", "capacity": 100, "load": 10},
        {"name": "a", "capacity": 100, "load": 10},
        {"name": "m", "capacity": 100, "load": 10},
    ]})
    assert [r["name"] for r in out["regions"]] == ["z", "a", "m"]
    assert out["k"] == 1


# ---------------------------------------------------------------------------
# Corner cases
# ---------------------------------------------------------------------------

def test_full_load_survivor_gets_nothing(binary):
    # A is already at capacity (headroom 0): it must absorb no failover.
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 100},
        {"name": "B", "capacity": 100, "load": 10},
        {"name": "C", "capacity": 100, "load": 10},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 0.0)   # stays at capacity, absorbs nothing
    check(r["B"], 60.0, 50.0)
    check(r["C"], 60.0, 50.0)


def test_two_survivors_saturate_same_round(binary):
    # Failing D (load 60) is offered 20 to each of A, B, C; A and B (headroom 5)
    # both saturate in the same round, overflow cascades to C.
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 95},
        {"name": "B", "capacity": 100, "load": 95},
        {"name": "C", "capacity": 100, "load": 10},
        {"name": "D", "capacity": 100, "load": 60},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 5.0)
    check(r["B"], 100.0, 5.0)
    check(r["C"], 60.0, 50.0)
    check(r["D"], 100.0, 40.0)


def test_resilient_multiround_sequential_cascade(binary):
    # Failing E (load 350) saturates A (round 1), then B (round 2), then spills
    # the remainder onto C (round 3) -> C ends at 410. Stays resilient.
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 90},
        {"name": "B", "capacity": 100, "load": 70},
        {"name": "C", "capacity": 1000, "load": 100},
        {"name": "E", "capacity": 450, "load": 350},
    ]})
    assert out["resilient"] is True
    assert out["capacityShortfall"] == pytest.approx(0.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 10.0)
    check(r["B"], 100.0, 30.0)
    check(r["C"], 410.0, 310.0)
    check(r["E"], 4660.0 / 11.0, 4660.0 / 11.0 - 350.0)  # 423.6363..., 73.6363...


def test_resilience_boundary_off_by_one(binary):
    # Total headroom (99) is one short of the largest capacity (100): not
    # resilient, shortfall exactly 1.
    out = compute(binary, {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 51},
        {"name": "B", "capacity": 100, "load": 50},
    ]})
    assert out["resilient"] is False
    assert out["capacityShortfall"] == pytest.approx(1.0, abs=TOL)
    r = by_name(out)
    check(r["A"], 100.0, 49.0)
    check(r["B"], 100.0, 50.0)


# ---------------------------------------------------------------------------
# Input validation: invalid input must exit non-zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"k": 2, "regions": [
        {"name": "A", "capacity": 100, "load": 10},
        {"name": "B", "capacity": 100, "load": 10},
    ]},  # k >= n
    {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 150},
        {"name": "B", "capacity": 100, "load": 10},
    ]},  # load > capacity
    {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": -5},
        {"name": "B", "capacity": 100, "load": 10},
    ]},  # negative load
    {"k": 1, "regions": [
        {"name": "A", "capacity": 0, "load": 0},
        {"name": "B", "capacity": 100, "load": 10},
    ]},  # non-positive capacity
    {"k": -1, "regions": [
        {"name": "A", "capacity": 100, "load": 10},
        {"name": "B", "capacity": 100, "load": 10},
    ]},  # negative k
    {"k": 0, "regions": []},  # empty regions
    {"k": 1, "regions": [
        {"name": "A", "capacity": 100, "load": 10},
        {"name": "A", "capacity": 100, "load": 10},
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
