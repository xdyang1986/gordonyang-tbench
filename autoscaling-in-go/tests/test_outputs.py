"""Black-box tests: build the agent's Go program and drive it via stdin/stdout.

Scale-in is checked asymptotically and the rate limiter via a per-step invariant
(the exact scale-in tick is convention-dependent). Scale-up and clamps are exact.
"""

import os
import subprocess

import pytest

APP = "/app"
BIN = "/tmp/agent_autoscaler"

GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}


def _find_main_pkg():
    """Find the dir holding `func main` (agent may put it at root or a subdir)."""
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
    """Init a module if needed and build the agent's program."""
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(
            ["go", "mod", "init", "autoscaler"],
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
        # main not at root — find the package that declares func main
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert (
        r.returncode == 0
    ), f"`go build` failed (no buildable `func main` found):\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run(stdin, timeout=30):
    return subprocess.run(
        [BIN], input=stdin, capture_output=True, text=True, timeout=timeout
    )


def rows(out):
    """Parse CSV into {tick: (cpu_str, replicas_int, action_str)}; check header."""
    lines = [l for l in out.strip().split("\n") if l != ""]
    assert lines, "no output"
    assert lines[0] == "tick,cpu,replicas,action", f"bad/missing header: {lines[0]!r}"
    res = {}
    for l in lines[1:]:
        parts = l.split(",")
        assert len(parts) == 4, f"bad row: {l!r}"
        t, cpu, rep, act = parts
        res[int(t)] = (cpu, int(rep), act)
    return res


CFG = "target=0.60 min=1 max=50 tolerance=0.10 down_window=300 max_scale_down_frac=1.0 tick=30 start={start}"


# ---- format / determinism / validation (exact, unambiguous) ----


def test_output_format_and_header():
    r = run(CFG.format(start=4) + "\n0.60\n")
    assert r.returncode == 0, r.stderr
    rs = rows(r.stdout)
    assert rs[0][0] == "0.60", f"cpu must be 2 decimals, got {rs[0][0]!r}"


def test_deterministic():
    stdin = CFG.format(start=10) + "\n0.60\n0.60\n0.10\n0.60\n"
    a = run(stdin)
    b = run(stdin)
    assert (
        a.stdout == b.stdout and a.stdout != ""
    ), "output must be byte-identical across runs"


def test_invalid_config_exits_nonzero():
    cfg = "target=1.5 min=1 max=2 tolerance=0.1 down_window=0 max_scale_down_frac=1.0 tick=30 start=1"
    assert (
        run(cfg + "\n0.60\n").returncode != 0
    ), "invalid config (target>1) must exit non-zero"


def test_bad_sample_unparseable_exits_nonzero():
    assert (
        run(CFG.format(start=4) + "\nabc\n").returncode != 0
    ), "unparseable sample must exit non-zero"


def test_bad_sample_out_of_range_exits_nonzero():
    assert (
        run(CFG.format(start=4) + "\n1.5\n").returncode != 0
    ), "out-of-range sample (>1) must exit non-zero"


# ---- scale-up & clamps (exact: spec fixes immediate scale-up + bounds) ----


def test_scale_up_immediate_on_spike():
    r = run(CFG.format(start=4) + "\n0.60\n1.00\n")
    rs = rows(r.stdout)
    assert rs[0][1] == 4 and rs[0][2] == "none"
    # ceil(4 * 1.0/0.6) = 7, applied on the very next tick (no up-side delay)
    assert rs[1] == ("1.00", 7, "up"), f"expected immediate scale-up to 7, got {rs[1]}"


def test_respects_max_replicas():
    cfg = "target=0.60 min=1 max=8 tolerance=0.10 down_window=0 max_scale_down_frac=1.0 tick=30 start=6"
    rs = rows(run(cfg + "\n1.00\n").stdout)
    assert rs[0][1] == 8, f"must clamp to max=8, got {rs[0][1]}"


# ---- transient-dip absorption (exact: a single dip must not scale in) ----


def test_transient_dip_held():
    r = run(
        CFG.format(start=10) + "\n0.60\n0.60\n0.60\n0.10\n0.60\n"
    )  # down_window=300 (10 ticks)
    rs = rows(r.stdout)
    assert (
        rs[3][1] == 10 and rs[3][2] == "none"
    ), f"transient dip must hold at 10, got {rs[3]}"
    assert rs[4][1] == 10, f"fleet must stay 10 after recovery, got {rs[4]}"


# ---- sustained scale-in (asymptotic: convention-robust) ----


def test_sustained_drop_eventually_scales_in():
    # high once, then sustained low for many ticks (down_window=60 == 2 ticks).
    cfg = "target=0.60 min=1 max=50 tolerance=0.10 down_window=60 max_scale_down_frac=1.0 tick=30 start=10"
    samples = "\n".join(["0.60"] + ["0.10"] * 15)
    rs = rows(run(cfg + "\n" + samples + "\n").stdout)
    # the FIRST low tick is still within the window -> must hold (no premature scale-in)
    assert rs[1][1] == 10, f"must not scale in on the first low tick, got {rs[1]}"
    # after sustained low demand, the fleet must drain to the floor
    last = max(rs)
    assert (
        rs[last][1] == 1
    ), f"sustained low must eventually drain to min=1, got {rs[last][1]}"


def test_idle_eventually_reaches_min():
    cfg = "target=0.60 min=2 max=50 tolerance=0.10 down_window=0 max_scale_down_frac=1.0 tick=30 start=6"
    samples = "\n".join(["0.00"] * 6)
    rs = rows(run(cfg + "\n" + samples + "\n").stdout)
    last = max(rs)
    assert rs[last][1] == 2, f"sustained idle must reach min=2, got {rs[last][1]}"
    assert all(rs[i][1] >= 2 for i in rs), "must never drop below min"


# ---- rate-limited scale-in (invariant: convention-robust) ----


def test_rate_limited_scale_in_is_gradual():
    frac = 0.5
    cfg = f"target=0.60 min=1 max=50 tolerance=0.10 down_window=0 max_scale_down_frac={frac} tick=30 start=10"
    samples = "\n".join(["0.10"] * 8)
    rs = rows(run(cfg + "\n" + samples + "\n").stdout)
    seq = [rs[i][1] for i in sorted(rs)]
    # invariant: no single step removes more than max(1, floor(prev*frac))
    down_steps = 0
    for prev, cur in zip(seq, seq[1:]):
        removed = prev - cur
        if removed > 0:
            down_steps += 1
            cap = max(1, int(prev * frac))
            assert removed <= cap, f"removed {removed} > rate cap {cap} (from {prev})"
    # the big drop (10 -> 1) must be spread over several steps, not done at once
    assert (
        down_steps >= 3
    ), f"scale-in must be gradual (>=3 down steps), got {down_steps}: {seq}"
    assert seq[-1] == 1, f"must still converge to min=1, got {seq[-1]}"


# ---- tolerance dead-band (exact: spec fixes it) ----


def test_tolerance_dead_band():
    rs = rows(
        run(CFG.format(start=5) + "\n0.63\n").stdout
    )  # ratio 1.05, within 0.10 band
    assert rs[0] == ("0.63", 5, "none"), f"within tolerance must hold, got {rs[0]}"


# ---- predictive scale-up: directional / trend-gating (convention-robust) ----
# The spec describes the scenario only (pre-scale up on a clear sustained rise,
# never on transient blips; scale-up only; off by default) without pinning a
# formula, so these assert direction and trend-gating, not exact values. A naive
# "pre-scale on any positive slope" recall solution fails the transient-blip gate.

PRED_BASE = (
    "target=0.60 min=1 max=50 tolerance=0.10 down_window=300 "
    "max_scale_down_frac=1.0 tick=30 start=4"
)
RAMP = "\n".join(["0.30", "0.45", "0.60", "0.75", "0.90"])
ZIGZAG = "\n".join(["0.40", "0.60", "0.40", "0.60", "0.40", "0.60"])


def _fleet_seq(cfg, samples):
    rs = rows(run(cfg + "\n" + samples + "\n").stdout)
    return [rs[i][1] for i in sorted(rs)]


def test_predictive_off_by_default():
    # an omitted key behaves identically to predict_lookahead=0 (reactive).
    seq0 = _fleet_seq(PRED_BASE + " predict_lookahead=0", RAMP)
    seq_omitted = _fleet_seq(PRED_BASE, RAMP)
    assert seq_omitted == seq0, f"omitted key must equal 0: {seq_omitted} vs {seq0}"


def test_predictive_prescales_on_sustained_rise():
    off = _fleet_seq(PRED_BASE + " predict_lookahead=0", RAMP)
    on = _fleet_seq(PRED_BASE + " predict_lookahead=120", RAMP)
    # scale-up only: never below the reactive fleet
    assert all(o >= f for o, f in zip(on, off)), f"scale-up only: on={on} off={off}"
    # and it pre-scales: strictly higher than reactive somewhere on the ramp
    assert any(
        o > f for o, f in zip(on, off)
    ), f"prediction must pre-scale up on a sustained rise: on={on} off={off}"


def test_predictive_ignores_transient_blips():
    # a choppy series with no sustained trend must NOT trigger pre-scaling; the
    # fleet stays on the reactive baseline. A 2-point "any positive slope"
    # extrapolation would pre-scale on every up-tick and fail here.
    off = _fleet_seq(PRED_BASE + " predict_lookahead=0", ZIGZAG)
    on = _fleet_seq(PRED_BASE + " predict_lookahead=120", ZIGZAG)
    assert on == off, f"transient blips must not pre-scale: on={on} off={off}"


def test_predictive_scale_up_only_on_falling_trend():
    base = (
        "target=0.60 min=1 max=50 tolerance=0.10 down_window=0 "
        "max_scale_down_frac=1.0 tick=30 start=10"
    )
    falling = "\n".join(["1.00", "0.80", "0.60", "0.40", "0.20", "0.10"])
    off = _fleet_seq(base + " predict_lookahead=0", falling)
    on = _fleet_seq(base + " predict_lookahead=120", falling)
    assert all(
        o >= f for o, f in zip(on, off)
    ), f"prediction must not scale down (only up): on={on} off={off}"
