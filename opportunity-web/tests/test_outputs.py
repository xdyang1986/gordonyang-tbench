"""Grade the agent's from-scratch Ads Opportunity Board app.

The container ships only a scaffold (build tooling, shared types, a large fixed
dataset). The agent builds the whole app — src/App.tsx (the root component main.tsx
imports) plus whatever components/hooks/lib it needs. We grade by (a) requiring the
project to type-check and build (`npm run build`) and (b) running a hidden Playwright
suite against the built app in a REAL headless Chromium, so both pagination and
virtualization render correctly (jsdom cannot run virtualization). The grading
script lives under /tests/grading and is copied into /app/__grading__/ only at
verify time, so the agent never sees it during its run. Each Playwright assertion
surfaces as its own pytest case.
"""

import json
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

APP = Path("/app")
AGENT_APP = APP / "src/App.tsx"
HIDDEN_TESTS_DIR = Path("/tests/grading")
GRADE_DIR = APP / "__grading__"
TSX_BIN = APP / "node_modules/.bin/tsx"
PW_PKG = APP / "node_modules/playwright-core"
PW_RESULTS = Path("/tmp/pw-results.json")
PORT = 4173
BASE_URL = f"http://127.0.0.1:{PORT}"

# Test-only tooling (baked into the image; reinstalled here if the agent pruned
# node_modules, e.g. via `npm ci`). The Chromium binary lives at $PLAYWRIGHT_BROWSERS_PATH
# (outside node_modules), so it survives and no re-download is needed.
GRADING_DEPS = ["playwright-core@1.49.1", "tsx@4.19.2"]

# The canonical Playwright test titles the agent's app must satisfy.
EXPECTED_TESTS = [
    "summary shows the full opportunity count",
    "table renders a bounded window for large datasets",
    "summary shows total uplift formatted as USD",
    "formats currency amounts in a row",
    "search filters by customer name",
    "search matches all terms regardless of order",
    "search filters by rationale text",
    "search is case-insensitive",
    "multi-term search matches across name and rationale fields",
    "filters by industry",
    "filters by product",
    "filters by confidence",
    "applies multiple filters together (AND)",
    "shows an empty state when nothing matches",
    "sorts by est uplift descending by default",
    "sorts by current spend descending",
    "sorts by confidence high first",
    "confidence sort tiebreaks by uplift descending",
    "priority sort orders by uplift weighted by confidence",
    "summary reflects the filtered view",
    "per-opportunity state stays with the opportunity after re-sort",
    "new opportunity defaults to status New and unassigned",
    "changing a row status updates it",
    "filters by status using per-row state",
    "changing status removes a row from an active status filter",
    "persists status and assignee across remount",
    "search persists across refresh",
    "filter selection persists across refresh",
    "sort selection persists across refresh",
]


def _run(cmd, cwd=None, timeout=480, env=None):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )


def _wait_for_server(url, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _port_open():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


@pytest.fixture(scope="session")
def grading():
    """Build the app, serve it, and run the hidden Playwright suite in Chromium."""
    assert shutil.which("node"), "node was not found in the grading environment"
    assert AGENT_APP.exists(), f"Agent did not create the root component at {AGENT_APP}"
    assert HIDDEN_TESTS_DIR.exists(), f"Hidden tests missing at {HIDDEN_TESTS_DIR}"

    # Restore grading tooling if the agent pruned node_modules.
    if not TSX_BIN.exists() or not PW_PKG.exists():
        _run(
            [
                "npm",
                "install",
                "--no-save",
                "--prefer-offline",
                "--no-audit",
                "--no-fund",
            ]
            + GRADING_DEPS,
            cwd=str(APP),
            timeout=300,
        )
    assert TSX_BIN.exists(), f"tsx missing at {TSX_BIN} even after reinstall"
    assert PW_PKG.exists(), "playwright missing even after reinstall"

    # Stage the grading script inside /app so its TS imports resolve via /app.
    if GRADE_DIR.exists():
        shutil.rmtree(GRADE_DIR)
    GRADE_DIR.mkdir(parents=True)
    for src in HIDDEN_TESTS_DIR.glob("*.mts"):
        shutil.copy(src, GRADE_DIR / src.name)

    # (a) Type-check + production build.
    build = _run(["npm", "run", "build"], cwd=str(APP), timeout=480)
    build_diag = (
        f"`npm run build` exit={build.returncode}\n"
        f"----- STDOUT -----\n{build.stdout[-4000:]}\n"
        f"----- STDERR -----\n{build.stderr[-2500:]}\n"
    )

    outcomes = {}
    ran = False
    pw_diag = ""

    if build.returncode == 0:
        # (b) Serve the built app and drive it with Playwright.
        server = subprocess.Popen(
            [
                "npx",
                "vite",
                "preview",
                "--port",
                str(PORT),
                "--strictPort",
                "--host",
                "127.0.0.1",
            ],
            cwd=str(APP),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            up = _wait_for_server(BASE_URL, timeout=40)
            if not up:
                pw_diag = "Preview server did not become ready on " + BASE_URL
            else:
                if PW_RESULTS.exists():
                    PW_RESULTS.unlink()
                import os

                env = dict(os.environ)
                env["BASE_URL"] = BASE_URL
                env["PW_OUT"] = str(PW_RESULTS)
                pw = _run(
                    [str(TSX_BIN), "__grading__/run-playwright.mts"],
                    cwd=str(APP),
                    timeout=600,
                    env=env,
                )
                pw_diag = (
                    f"playwright exit={pw.returncode}\n"
                    f"----- STDOUT -----\n{pw.stdout[-4000:]}\n"
                    f"----- STDERR -----\n{pw.stderr[-2500:]}\n"
                )
                if PW_RESULTS.exists():
                    try:
                        data = json.loads(PW_RESULTS.read_text())
                        if isinstance(data, list):
                            ran = True
                            for r in data:
                                outcomes[r.get("title", "")] = (
                                    "passed" if r.get("passed") else "failed"
                                )
                    except json.JSONDecodeError:
                        ran = False
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    return {
        "build_ok": build.returncode == 0,
        "build_diag": build_diag,
        "ran": ran,
        "outcomes": outcomes,
        "pw_diag": pw_diag,
    }


def test_project_builds(grading):
    """`npm run build` (tsc -b && vite build) must succeed."""
    assert grading["build_ok"], (
        "The project failed to type-check / build.\n\n" + grading["build_diag"]
    )


def test_suite_ran(grading):
    """The hidden Playwright suite must execute and produce a parseable report."""
    assert grading["ran"], (
        "The Playwright grading suite did not produce a parseable report — the app "
        "likely failed to build or serve.\n\n"
        + grading["build_diag"]
        + "\n"
        + grading["pw_diag"]
    )


@pytest.mark.parametrize("test_name", EXPECTED_TESTS)
def test_case_passes(grading, test_name):
    outcome = grading["outcomes"].get(test_name)
    assert outcome is not None, (
        f"Expected test '{test_name}' did not run.\n\n" + grading["pw_diag"]
    )
    assert outcome == "passed", (
        f"'{test_name}' outcome was {outcome!r}, expected 'passed'.\n\n"
        + grading["pw_diag"]
    )
