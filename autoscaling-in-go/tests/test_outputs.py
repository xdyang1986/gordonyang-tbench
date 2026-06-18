"""Verifier for the autoscaling-in-go task.

The agent's job is to create autoscaler/algorithm.go so the package compiles and
behaves per the documented policy. To grade ONLY the agent's algorithm.go and
prevent tampering with the rest of the repo or the tests, this verifier:

  1. Restores pristine copies of every non-implementation file (engine.go,
     types.go, clock.go, cmd/autoscalesim/main.go, go.mod) over /app.
  2. Removes any *_test.go the agent may have left in the package, then copies in
     the hidden unit + behavioral suites.
  3. Runs `go build`, the unit suite, the closed-loop behavioral suite, and the
     end-to-end simulation binary.

Anything the agent wrote outside autoscaler/algorithm.go (and any new non-test
helper files) is discarded or overwritten, so the only thing that can make these
tests pass is a correct algorithm.go.
"""

import os
import shutil
import subprocess

import pytest

APP = "/app"
TESTS = "/tests"
PRISTINE = os.path.join(TESTS, "pristine")
PKG_DIR = os.path.join(APP, "autoscaler")

UNIT_TESTS = "|".join(
    [
        "TestScaleUpOnSpike",
        "TestTransientDropHeld",
        "TestSustainedDropScalesDown",
        "TestRawDesiredNoFPOverprovision",
        "TestLimitScaleDownRate",
        "TestGradualScaleDown",
        "TestToleranceDeadBand",
        "TestRespectsMaxReplicas",
        "TestRespectsMinReplicas",
        "TestTickPropagatesMetricError",
        "TestConfigValidation",
        "TestRunLoop",
    ]
)

GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}


def _go(*args, timeout=240):
    """Run a `go` command in /app and return the CompletedProcess."""
    return subprocess.run(
        ["go", *args],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session", autouse=True)
def prepare_repo():
    """Restore pristine files and inject the hidden test suites."""
    # 1. Restore every non-implementation file from pristine copies. This leaves
    #    the agent's autoscaler/algorithm.go (and any extra helper files) intact
    #    but reverts engine.go/types.go/clock.go/main.go/go.mod to originals.
    for root, _dirs, files in os.walk(PRISTINE):
        rel = os.path.relpath(root, PRISTINE)
        dest_dir = APP if rel == "." else os.path.join(APP, rel)
        os.makedirs(dest_dir, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(dest_dir, f))

    # 2. Drop any *_test.go the agent might have added (avoids symbol clashes),
    #    then copy in the authoritative hidden suites.
    for d in (PKG_DIR, os.path.join(APP, "cmd", "autoscalesim")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith("_test.go"):
                    os.remove(os.path.join(d, f))
    os.makedirs(PKG_DIR, exist_ok=True)
    shutil.copy2(
        os.path.join(TESTS, "autoscaler_test.go"),
        os.path.join(PKG_DIR, "autoscaler_test.go"),
    )
    shutil.copy2(
        os.path.join(TESTS, "behavior_test.go"),
        os.path.join(PKG_DIR, "behavior_test.go"),
    )
    shutil.copy2(
        os.path.join(TESTS, "predictive_test.go"),
        os.path.join(PKG_DIR, "predictive_test.go"),
    )
    yield


def test_package_builds():
    """The agent's algorithm.go must make the whole module compile."""
    r = _go("build", "./...")
    assert (
        r.returncode == 0
    ), f"`go build ./...` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"


def test_unit_suite():
    """The hidden unit suite (algorithm + engine correctness) must pass."""
    r = _go("test", "-race", "-count=1", "-run", UNIT_TESTS, "./autoscaler/", "-v")
    assert (
        r.returncode == 0
    ), f"unit suite failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"


def test_behavioral_simulation():
    """The hidden closed-loop behavioral suite must pass."""
    r = _go("test", "-count=1", "-run", "TestClosedLoopWorkload", "./autoscaler/", "-v")
    assert (
        r.returncode == 0
    ), f"behavioral suite failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"


def test_predictive_suite():
    """The hidden predictive (seasonal) scale-up suite must pass — unit tests
    for predictiveFloor plus behavioral pre-scaling / off-by-default /
    never-scales-down checks."""
    r = _go(
        "test", "-race", "-count=1", "-run", "TestPredictive", "./autoscaler/", "-v"
    )
    assert (
        r.returncode == 0
    ), f"predictive suite failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"


def test_simulation_binary_runs():
    """End-to-end: the simulation binary must run and exhibit the policy —
    a scale-up, a held transient dip, and a scale-down."""
    r = _go("run", "./cmd/autoscalesim", timeout=120)
    assert (
        r.returncode == 0
    ), f"`go run ./cmd/autoscalesim` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    out = r.stdout
    assert "▲ up" in out, f"simulation never scaled up:\n{out}"
    assert "▼ down" in out, f"simulation never scaled down:\n{out}"
    assert (
        "hold (dip absorbed)" in out
    ), f"simulation did not absorb the transient dip:\n{out}"
