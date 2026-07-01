"""Black-box tests: build the agent's Go failover program and drive it via stdin.

The graded surface is stdout (PROMOTE/ABORT/REJOIN lines, in tick order) plus the
process exit code (0/1/2). Survivor reattach is non-graded (stderr).

Because the instruction specifies debounce at the *scenario* level ("ignore
transient blips, fail over only on a sustained outage") without pinning the exact
number of ticks, debounce is graded by INVARIANT: a single-tick blip must never
trigger a failover, and a clearly-sustained outage must eventually trigger one.
Every firing scenario therefore uses a long consecutive-down run (SUS ticks) so
any reasonable threshold fires, and abort scenarios assert the N-robust invariant
"every emitted decision is ABORT" (re-attempts may emit ABORT more than once).

Quorum is graded with robust majorities (clear minority up -> abort; clear
majority up -> promote) so the exact boundary convention is not unfairly binding.
Election, data-loss, REJOIN, retention, and validation are exact.
"""

import os
import subprocess

import pytest

APP = "/app"
BIN = "/tmp/agent_failover"
SUS = 10  # length of a "sustained" outage: long enough that any reasonable debounce fires

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
            ["go", "mod", "init", "failover"],
            cwd=APP, env=GO_ENV, capture_output=True, text=True,
        )

    def _build(pkg):
        return subprocess.run(
            ["go", "build", "-o", BIN, pkg],
            cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=240,
        )

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run(stdin, args=None, timeout=30):
    cmd = [BIN] + (args or [])
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    lines = [l for l in p.stdout.split("\n") if l.strip() != ""]
    return lines, p.stderr, p.returncode


def rows(*lines):
    return "\n".join(lines) + "\n"


def dn(t, pid="db1", pos=1000, prio=10):
    """A tick line: primary `pid` observed DOWN."""
    return f"{t} {pid} primary down {pos} {prio}"


def down_run(n, t0=1, pid="db1", pos=1000, prio=10):
    """`n` consecutive ticks (starting t0) restating the primary as down."""
    return [dn(t0 + i, pid, pos, prio) for i in range(n)]


def assert_all_abort(out, rc, exp_rc=1):
    assert out, f"expected at least one ABORT, got {out}"
    assert all(l == "ABORT" for l in out), f"expected only ABORT lines, got {out}"
    assert rc == exp_rc, f"exit {rc} != {exp_rc}"


# ---- debounce as invariant: blip vs sustained ----


def test_single_blip_never_fails_over():
    # one down tick, then recovery -> must NOT trigger a failover.
    out, _e, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary up 1000 5",
        )
    )
    assert out == [], f"a single-tick blip must not fail over, got {out}"
    assert rc == 0


def test_sustained_outage_promotes():
    # a clearly-sustained outage must eventually promote (quorum met, no data loss).
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        *down_run(SUS),
    )
    out, _e, rc = run(stdin)
    assert out == ["PROMOTE db2"], out  # tie 1000/prio -> lowest id db2
    assert rc == 0


def test_deterministic():
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        *down_run(SUS),
    )
    assert run(stdin) == run(stdin)


def test_no_failover_empty_stdout_exit0():
    out, _e, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary up 1000 5",
        )
    )
    assert out == [] and rc == 0, (out, rc)


def test_reattach_not_on_stdout():
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        *down_run(SUS),
    )
    out, _e, _rc = run(stdin)
    assert not any("reattach" in l.lower() for l in out)


# ---- election order: pos -> priority(higher) -> lowest id (exact) ----


def test_elect_highest_position():
    stdin = rows(
        "0 db1 primary up 990 5",
        "0 db2 replica up 995 5",
        "0 db3 replica up 1000 5",
        *[f"{1+i} db1 primary down 990 5" for i in range(SUS)],
    )
    out, _e, _rc = run(stdin)
    assert out == ["PROMOTE db3"], out  # 1000 is highest and == cluster-max


def test_tiebreak_higher_priority():
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 9",
        *down_run(SUS),
    )
    out, _e, _rc = run(stdin)
    assert out == ["PROMOTE db3"], out  # equal pos -> higher prio wins


def test_tiebreak_lowest_id():
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 dbA replica up 1000 5",
        "0 dbB replica up 1000 5",
        *down_run(SUS),
    )
    out, _e, _rc = run(stdin)
    assert out == ["PROMOTE dbA"], out  # equal pos & prio -> lowest id


# ---- data-loss guardrail (quorum met, so the abort is data-loss) ----


def test_default_maxloss_aborts_behind_candidate():
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 998 5",
        "0 db3 replica up 998 5",
        *down_run(SUS),
    )
    out, _e, rc = run(stdin)  # cluster-max 1000 (dead primary) - 998 = 2 > 0
    assert_all_abort(out, rc, 1)


def test_maxloss_flag_allows_promote():
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 998 5",
        "0 db3 replica up 998 5",
        *down_run(SUS),
    )
    out, _e, rc = run(stdin, args=["-max-loss=5"])
    assert out == ["PROMOTE db2"], out  # loss 2 <= 5; tie -> lowest id
    assert rc == 0


def test_maxloss_boundary_equal_promotes():
    # loss exactly == max-loss does NOT exceed it -> promote (spec says "exceeds").
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 998 5",
        "0 db3 replica up 998 5",
        *down_run(SUS),
    )
    out, _e, rc = run(stdin, args=["-max-loss=2"])  # cluster-max 1000 - 998 = 2, not > 2
    assert out == ["PROMOTE db2"], out
    assert rc == 0


def test_clustermax_includes_a_down_node():
    # 5 nodes so quorum (3) is met by the up replicas; a DOWN node holds the max pos.
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        "0 db4 replica up 1000 5",
        "0 db5 replica down 1050 5",
        *down_run(SUS),
    )
    out, _e, rc = run(stdin)  # cluster-max 1050 (down db5) - 1000 = 50 > 0
    assert_all_abort(out, rc, 1)


def test_reattempt_after_abort_then_promote():
    # behind candidate -> abort(s); it catches up -> a later sustained run promotes.
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 995 5",
        "0 db3 replica up 995 5",
    ]
    lines += [f"{1+i} db1 primary down 1000 5" for i in range(2 * SUS)]  # long outage
    lines += ["11 db2 replica up 1000 5", "11 db3 replica up 1000 5"]  # catch up mid-outage
    out, _e, rc = run(rows(*lines))
    assert "ABORT" in out, out
    assert out[-1] == "PROMOTE db2", out
    assert all(l == "ABORT" for l in out[:-1]), out
    assert rc == 0


# ---- quorum gate (robust majorities) ----


def test_no_quorum_aborts():
    # 5 nodes seen; at the outage only 1 replica is up (1 < majority 3) -> abort.
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        "0 db4 replica up 1000 5",
        "0 db5 replica up 1000 5",
        "1 db3 replica down 1000 5",
        "1 db4 replica down 1000 5",
        "1 db5 replica down 1000 5",
        *down_run(SUS),
    )
    out, _e, rc = run(stdin)
    assert_all_abort(out, rc, 1)  # only db2 up -> no quorum


def test_quorum_restored_then_promotes():
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        "0 db4 replica up 1000 5",
        "0 db5 replica up 1000 5",
        "1 db3 replica down 1000 5",
        "1 db4 replica down 1000 5",
        "1 db5 replica down 1000 5",
    ]
    lines += [f"{1+i} db1 primary down 1000 5" for i in range(2 * SUS)]
    lines += [  # quorum restored mid-outage
        "12 db3 replica up 1000 5",
        "12 db4 replica up 1000 5",
        "12 db5 replica up 1000 5",
    ]
    out, _e, rc = run(rows(*lines))
    assert "ABORT" in out, out
    assert out[-1] == "PROMOTE db2", out
    assert all(l == "ABORT" for l in out[:-1]), out
    assert rc == 0


# ---- REJOIN: only a fenced ex-primary, once, sorted, after the decision ----


def test_rejoin_when_ex_primary_returns():
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
    ]
    lines += down_run(SUS)  # -> PROMOTE db2, fence db1
    lines += [f"{SUS+5} db1 primary up 1000 5"]  # ex-primary returns, well after any fire
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "REJOIN db1 db2"], out
    assert rc == 0


def test_survivor_flap_never_rejoins():
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
    ]
    lines += down_run(SUS)  # -> PROMOTE db2
    lines += [f"{SUS+3} db3 replica down 1000 5", f"{SUS+4} db3 replica up 1000 5"]
    out, _e, _rc = run(rows(*lines))
    assert out == ["PROMOTE db2"], out  # survivor db3 flap emits no REJOIN


def test_multiple_failovers_two_promotes():
    # 5 nodes so the 2nd failover still has quorum.
    lines = [f"0 db{i} replica up 1000 5" for i in range(2, 6)]
    lines.insert(0, "0 db1 primary up 1000 5")
    lines += [f"{1+i} db1 primary down 1000 5" for i in range(SUS)]  # -> PROMOTE db2
    lines += [f"{SUS+1+i} db2 primary down 1000 5" for i in range(SUS)]  # -> PROMOTE db3
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "PROMOTE db3"], out
    assert rc == 0


def test_rejoin_sorted_by_id_two_ex_primaries():
    lines = [f"0 db{i} replica up 1000 5" for i in range(2, 6)]
    lines.insert(0, "0 db1 primary up 1000 5")
    lines += [f"{1+i} db1 primary down 1000 5" for i in range(SUS)]  # PROMOTE db2 (fence db1)
    lines += [f"{SUS+1+i} db2 primary down 1000 5" for i in range(SUS)]  # PROMOTE db3 (fence db2)
    lines += [f"{2*SUS+5} db1 primary up 1000 5", f"{2*SUS+5} db2 replica up 1000 5"]
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "PROMOTE db3", "REJOIN db1 db3", "REJOIN db2 db3"], out
    assert rc == 0


# ---- REJOIN divergence guard (ex-primary rejoins only if not diverged) ----


def test_rejoin_diverged_ahead_no_rejoin():
    # ex-primary returns strictly AHEAD of the current primary -> diverged -> no REJOIN.
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
    ]
    lines += down_run(SUS)  # PROMOTE db2 (fence db1); primary db2 pos 1000
    lines += [f"{SUS+5} db1 primary up 1005 10"]  # returns ahead (1005 > 1000)
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2"], out  # diverged -> no REJOIN line
    assert rc == 0


def test_rejoin_behind_still_rejoins():
    # ex-primary returns behind the current primary -> not diverged -> REJOIN.
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
    ]
    lines += down_run(SUS)  # PROMOTE db2; primary db2 pos 1000
    lines += [f"{SUS+5} db1 primary up 990 10"]  # returns behind (990 <= 1000)
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "REJOIN db1 db2"], out
    assert rc == 0


def test_rejoin_diverged_then_recovers_when_primary_advances():
    # diverged (ahead) ex-primary does not rejoin until the primary advances to/past it.
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
    ]
    lines += down_run(SUS)  # PROMOTE db2; primary db2 pos 1000
    lines += [f"{SUS+5} db1 primary up 1005 10"]  # ahead -> no rejoin yet
    lines += [f"{SUS+8} db2 primary up 1010 5"]  # primary advances past 1005 -> rejoin
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "REJOIN db1 db2"], out
    assert rc == 0


def test_two_ex_primaries_one_diverged_only_other_rejoins():
    lines = [f"0 db{i} replica up 1000 5" for i in range(2, 6)]
    lines.insert(0, "0 db1 primary up 1000 5")
    lines += [f"{1+i} db1 primary down 1000 5" for i in range(SUS)]  # PROMOTE db2 (fence db1)
    lines += [f"{SUS+1+i} db2 primary down 1000 5" for i in range(SUS)]  # PROMOTE db3 (fence db2)
    # db1 returns at/behind (1000 <= 1000 -> rejoin); db2 returns ahead (1005 -> diverged).
    lines += [f"{2*SUS+5} db1 primary up 1000 5", f"{2*SUS+5} db2 replica up 1005 5"]
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "PROMOTE db3", "REJOIN db1 db3"], out
    assert rc == 0


def test_diverged_node_quorum_yes_election_no_clustermax_no():
    # A fenced+diverged (ahead) node is treated three ways at the 2nd failover:
    #   - counts toward quorum: without it, only db3 is up (1 < majority 2) -> would ABORT;
    #   - NOT an election candidate: db3 is promoted, NOT db1, despite db1 holding pos 1005;
    #   - excluded from cluster-max: else 1005 - 1000 = 5 > 0 would force a data-loss ABORT.
    # The single expected output PROMOTE db3 only holds if all three treatments are correct.
    lines = [
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
    ]
    lines += down_run(SUS)  # PROMOTE db2 (fence db1); primary db2 pos 1000
    lines += [f"{SUS+3} db1 primary up 1005 10"]  # db1 returns AHEAD -> diverged (no rejoin)
    lines += [f"{SUS+4+i} db2 primary down 1000 5" for i in range(SUS)]  # db2 dies -> 2nd failover
    out, _e, rc = run(rows(*lines))
    assert out == ["PROMOTE db2", "PROMOTE db3"], out
    assert rc == 0


# ---- carry-forward / merge semantics ----


def test_unmentioned_node_retained_as_candidate():
    # db2/db3 stated only at tick 0 must remain electable through a later outage.
    stdin = rows(
        "0 db1 primary up 1000 5",
        "0 db2 replica up 1000 5",
        "0 db3 replica up 1000 5",
        *down_run(SUS),
    )
    out, _e, _rc = run(stdin)
    assert out == ["PROMOTE db2"], out


# ---- input/flag validation -> exit 2, no decisions ----


def test_bad_input_field_count_exit2():
    out, _e, rc = run(rows("0 db1 primary up 1000 5", "garbage line here"))
    assert rc == 2 and out == [], (rc, out)


def test_bad_input_unparseable_pos_exit2():
    out, _e, rc = run(rows("0 db1 primary up notanint 5"))
    assert rc == 2 and out == [], (rc, out)


def test_invalid_flag_exit2():
    out, _e, rc = run(
        rows("0 db1 primary up 1000 5", "0 db2 replica up 1000 5", *down_run(SUS)),
        args=["-max-loss=abc"],
    )
    assert rc == 2 and out == [], (rc, out)
