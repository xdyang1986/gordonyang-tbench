"""
Verifier for the traffic-throttle task.

The candidate writes a Go throttler somewhere under /app. We do NOT assume a
package name or module path: we locate the package that declares `func New(`,
rename any of the candidate's own *_test.go files aside (so they can't break our
build), drop our internal grading test into that package, and build it once with
the race detector. Each behavior is then run as its own pytest for granular
reporting.
"""

import glob
import os
import re
import shutil
import subprocess
import tempfile

import pytest

APP = "/app"
GO = shutil.which("go") or "/usr/local/go/bin/go"
TMPL = os.path.join(os.path.dirname(__file__), "grade_test.go.tmpl")

_NEW_RE = re.compile(r"func\s+New\w*\s*[\[(]")
_THROTTLER_RE = re.compile(r"type\s+Throttler\b")
_PKG_RE = re.compile(r"^\s*package\s+(\w+)", re.M)
_SKIP_DIRS = {".git", "vendor", "node_modules", "testdata", ".cache"}


def _go_env():
    env = dict(os.environ)
    base = tempfile.mkdtemp(prefix="gohome_")
    env.setdefault("HOME", base)
    env["GOCACHE"] = os.path.join(base, "cache")
    env["GOPATH"] = os.path.join(base, "path")
    env["GOFLAGS"] = "-mod=mod"
    return env


def _scan_app():
    """Map each package dir under /app to what throttler API markers it declares."""
    info = {}
    for root, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(".go") or name.endswith("_test.go"):
                continue
            path = os.path.join(root, name)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            m = _PKG_RE.search(text)
            if not m:
                continue
            d = info.setdefault(
                root, {"pkg": m.group(1), "new": False, "throttler": False}
            )
            d["pkg"] = m.group(1)
            if _NEW_RE.search(text):
                d["new"] = True
            if _THROTTLER_RE.search(text):
                d["throttler"] = True
    return info


def _find_package():
    """Locate the candidate's throttler package.

    Anchors on either the central `Throttler` type or a `New*` constructor, so
    the package is found regardless of file layout, package name, or module path.
    Prefers a package that declares both.
    """
    info = _scan_app()
    rank = lambda v: (v["new"] and v["throttler"], v["new"], v["throttler"])
    best = None
    for root, v in info.items():
        if not (v["new"] or v["throttler"]):
            continue
        if best is None or rank(v) > rank(info[best]):
            best = root
    return (best, info[best]["pkg"]) if best is not None else None


def _diagnostics():
    """Dump every .go file head under /app to explain a detection failure."""
    out = ["--- .go files under /app ---"]
    for root, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(".go"):
                continue
            path = os.path.join(root, name)
            try:
                head = "\n".join(open(path, errors="ignore").read().splitlines()[:10])
            except OSError:
                head = "<unreadable>"
            out.append(f"\n### {path}\n{head}")
    if len(out) == 1:
        out.append("(no .go files found under /app)")
    return "\n".join(out)


def _has_go_mod(pkgdir):
    d = pkgdir
    while True:
        if os.path.exists(os.path.join(d, "go.mod")):
            return True
        if os.path.normpath(d) in (os.path.normpath(APP), "/"):
            return False
        d = os.path.dirname(d)


@pytest.fixture(scope="session")
def grade_bin():
    info = _find_package()
    assert info is not None, (
        "Could not locate the throttler package under /app (no .go file declaring "
        "`func New` or `type Throttler`). Implement the package as specified.\n"
        + _diagnostics()
    )
    pkgdir, pkgname = info
    env = _go_env()

    # Keep the candidate's own tests from interfering with our build.
    for tf in glob.glob(os.path.join(pkgdir, "*_test.go")):
        os.rename(tf, tf + ".bak")

    if not _has_go_mod(pkgdir):
        r = subprocess.run(
            [GO, "mod", "init", pkgname],
            cwd=pkgdir,
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"`go mod init` failed:\n{r.stdout}\n{r.stderr}"

    test_src = open(TMPL, encoding="utf-8").read().replace("__PKG__", pkgname)
    open(os.path.join(pkgdir, "zz_grade_test.go"), "w", encoding="utf-8").write(
        test_src
    )

    binpath = os.path.join(tempfile.mkdtemp(prefix="gradebin_"), "grade.test")
    res = subprocess.run(
        [GO, "test", "-race", "-c", "-o", binpath, "."],
        cwd=pkgdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert res.returncode == 0, (
        "The candidate's code failed to compile against the grading tests. "
        "Check that every required type, field, and method exists with the "
        "names and signatures given in the instructions.\n"
        f"--- go test -c output ---\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    return binpath, pkgdir, env


def _run(grade_bin, name):
    binpath, pkgdir, env = grade_bin
    res = subprocess.run(
        [binpath, "-test.run", f"^{name}$", "-test.v", "-test.timeout=120s"],
        cwd=pkgdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert res.returncode == 0, f"{name} failed:\n{res.stdout}\n{res.stderr}"


def test_compiles(grade_bin):
    assert os.path.exists(grade_bin[0])


def test_burst_tolerance(grade_bin):
    _run(grade_bin, "TestGradeBurstAllow")


def test_separate_read_write_limits(grade_bin):
    _run(grade_bin, "TestGradeSeparateReadWrite")


def test_health_scales_refill(grade_bin):
    _run(grade_bin, "TestGradeHealthScaling")


def test_health_zero_blocks_and_ctx(grade_bin):
    _run(grade_bin, "TestGradeHealthZeroAllowFalse")


def test_wait_succeeds_after_refill(grade_bin):
    _run(grade_bin, "TestGradeWaitSucceedsWithRefill")


def test_unconfigured_allowed(grade_bin):
    _run(grade_bin, "TestGradeNoConfigAllowed")


def test_register_at_runtime(grade_bin):
    _run(grade_bin, "TestGradeRegisterRuntime")


def test_concurrent_allow_exact(grade_bin):
    _run(grade_bin, "TestGradeConcurrentAllow")


def test_concurrent_register_and_allow(grade_bin):
    _run(grade_bin, "TestGradeConcurrentRegisterAndAllow")


def test_poller_and_close(grade_bin):
    _run(grade_bin, "TestGradePollerAndClose")


def test_services_independent(grade_bin):
    _run(grade_bin, "TestGradeServicesIndependent")


def test_burst_cap_after_idle(grade_bin):
    _run(grade_bin, "TestGradeBurstCap")


def test_wait_cancel_when_starved(grade_bin):
    _run(grade_bin, "TestGradeWaitCancelWhenStarved")


def test_refresh_updates_cached_health(grade_bin):
    _run(grade_bin, "TestGradeRefreshUpdatesCachedHealth")


def test_aggregate_cap(grade_bin):
    _run(grade_bin, "TestGradeAggregateCap")


def test_aggregate_rollback(grade_bin):
    _run(grade_bin, "TestGradeAggregateRollback")


def test_aggregate_concurrent(grade_bin):
    _run(grade_bin, "TestGradeAggregateConcurrent")
