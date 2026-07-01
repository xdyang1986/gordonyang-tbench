"""Black-box tests: build the agent's Go failover program and drive it via stdin.

The graded surface is stdout (PROMOTE/ABORT/REJOIN lines, in tick order) plus the
process exit code (0/1/2). Survivor reattach is non-graded (stderr) and must NOT
appear on stdout. All scenarios are crafted; there is no fixed output to print.
"""

import os
import subprocess

import pytest

APP = "/app"
BIN = "/tmp/agent_failover"

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
            ["go", "mod", "init", "failover"],
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
    assert (
        r.returncode == 0
    ), f"`go build` failed (no buildable `func main` found):\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run(stdin, args=None, timeout=30):
    """Run the built binary; return (stdout_lines, stderr, returncode)."""
    cmd = [BIN] + (args or [])
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    lines = [l for l in p.stdout.split("\n") if l.strip() != ""]
    return lines, p.stderr, p.returncode


def rows(*lines):
    """Build a stdin stream from 'tick id role health pos prio' rows."""
    return "\n".join(lines) + "\n"


# The instruction's worked example.
EXAMPLE = rows(
    "0 db1 primary up 1000 10",
    "0 db2 replica up 998 5",
    "0 db3 replica up 1000 5",
    "0 db4 replica up 995 1",
    "1 db1 primary down 1000 10",
    "1 db3 replica up 1001 5",
    "2 db1 primary down 1000 10",
    "2 db3 replica up 1002 5",
    "3 db1 primary down 1000 10",
    "3 db3 replica up 1003 5",
)


# ---- worked example & basic shape ----


def test_worked_example():
    out, _err, rc = run(EXAMPLE)
    assert out == ["PROMOTE db3"], out
    assert rc == 0, rc


def test_deterministic():
    a = run(EXAMPLE)
    b = run(EXAMPLE)
    assert a == b and a[0] == ["PROMOTE db3"]


def test_no_failover_empty_stdout_exit0():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary up 1000 5",
        )
    )
    assert out == [], out
    assert rc == 0, rc


def test_reattach_not_on_stdout():
    # survivors reattach silently on stderr; stdout carries only the decision.
    out, err, _rc = run(EXAMPLE)
    assert out == ["PROMOTE db3"]
    assert not any("reattach" in l.lower() for l in out)


# ---- debounce N=3 (with reset-on-up) ----


def test_debounce_two_downs_does_not_fire():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
        )
    )
    assert out == [], "2 consecutive downs must not trigger failover"
    assert rc == 1, "ended with a dead, unreplaced primary"


def test_debounce_three_downs_fires():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        )
    )
    assert out == ["PROMOTE db2"], out
    assert rc == 0


def test_reset_on_up_restarts_count():
    # down,down,up resets; a later run of 3 downs is needed to fire (one promote only).
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary up 1000 5",
            "4 db1 primary down 1000 5",
            "5 db1 primary down 1000 5",
            "6 db1 primary down 1000 5",
        )
    )
    assert out == ["PROMOTE db2"], out
    assert rc == 0


# ---- election order: pos -> priority(higher) -> lowest id ----


def test_elect_highest_position():
    out, _err, _rc = run(
        rows(
            "0 db1 primary up 990 5",
            "0 db2 replica up 995 5",
            "0 db3 replica up 1000 5",
            "1 db1 primary down 990 5",
            "2 db1 primary down 990 5",
            "3 db1 primary down 990 5",
        )
    )
    assert out == ["PROMOTE db3"], out  # 1000 > 995


def test_tiebreak_higher_priority():
    out, _err, _rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "0 db3 replica up 1000 9",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        )
    )
    assert out == ["PROMOTE db3"], out  # equal pos -> higher prio (9) wins


def test_tiebreak_lowest_id():
    out, _err, _rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 dbB replica up 1000 5",
            "0 dbA replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        )
    )
    assert out == ["PROMOTE dbA"], out  # equal pos & prio -> lowest id


# ---- data-loss guardrail (max-loss default 0; reference = cluster-max pos) ----


def test_default_maxloss_aborts_behind_candidate():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 998 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        )
    )
    assert out == ["ABORT"], out  # db2 trails cluster-max(1000) by 2 > 0
    assert rc == 1


def test_maxloss_flag_allows_promote():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 998 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        ),
        args=["-max-loss=5"],
    )
    assert out == ["PROMOTE db2"], out  # loss 2 <= 5
    assert rc == 0


def test_clustermax_includes_a_down_node():
    # a down/lagging node holds the highest pos; the up candidate trails it,
    # so the default guardrail aborts (proves cluster-max spans down nodes).
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "0 db3 replica down 1050 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        )
    )
    assert out == ["ABORT"], out  # cluster-max 1050 (down db3) - db2 1000 = 50 > 0
    assert rc == 1


def test_reattempt_after_abort_then_promote():
    # abort (candidate behind), candidate catches up, next 3-down run promotes.
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 995 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",  # ABORT (995 trails 1000)
            "4 db2 replica up 1000 5",  # caught up
            "5 db1 primary down 1000 5",
            "6 db1 primary down 1000 5",
            "7 db1 primary down 1000 5",  # PROMOTE db2
        )
    )
    assert out == ["ABORT", "PROMOTE db2"], out
    assert rc == 0


# ---- REJOIN: only a fenced ex-primary, once, sorted by id, after the decision ----


def test_rejoin_when_ex_primary_returns():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
            "4 db1 primary up 1000 5",  # ex-primary returns -> REJOIN
        )
    )
    assert out == ["PROMOTE db2", "REJOIN db1 db2"], out
    assert rc == 0


def test_survivor_flap_never_rejoins():
    # a surviving replica going down then up must NOT emit REJOIN (only ex-primaries do).
    out, _err, _rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "0 db3 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",  # PROMOTE db2 (tie -> lowest id)
            "4 db3 replica down 1000 5",
            "5 db3 replica up 1000 5",
        )
    )
    assert out == ["PROMOTE db2"], out


def test_multiple_failovers_two_promotes():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "0 db3 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",  # PROMOTE db2
            "4 db2 primary down 1000 5",
            "5 db2 primary down 1000 5",
            "6 db2 primary down 1000 5",  # PROMOTE db3
        )
    )
    assert out == ["PROMOTE db2", "PROMOTE db3"], out
    assert rc == 0


def test_rejoin_sorted_by_id_two_ex_primaries():
    out, _err, rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "0 db3 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",  # PROMOTE db2 (fence db1)
            "4 db2 replica down 1000 5",
            "5 db2 replica down 1000 5",
            "6 db2 replica down 1000 5",  # PROMOTE db3 (fence db2)
            "7 db2 replica up 1000 5",
            "7 db1 primary up 1000 5",
        )
    )
    assert out == [
        "PROMOTE db2",
        "PROMOTE db3",
        "REJOIN db1 db3",
        "REJOIN db2 db3",
    ], out
    assert rc == 0


# ---- carry-forward / merge semantics ----


def test_unmentioned_node_retained_as_candidate():
    # db2 is stated only at tick 0 yet must remain electable at tick 3 (no expiry).
    out, _err, _rc = run(
        rows(
            "0 db1 primary up 1000 5",
            "0 db2 replica up 1000 5",
            "1 db1 primary down 1000 5",
            "2 db1 primary down 1000 5",
            "3 db1 primary down 1000 5",
        )
    )
    assert out == ["PROMOTE db2"], out


# ---- input/flag validation -> exit 2, no decisions ----


def test_bad_input_field_count_exit2():
    out, _err, rc = run(rows("0 db1 primary up 1000 5", "garbage line here"))
    assert rc == 2, rc
    assert out == [], "no decisions on bad input"


def test_bad_input_unparseable_pos_exit2():
    out, _err, rc = run(rows("0 db1 primary up notanint 5"))
    assert rc == 2, rc
    assert out == []


def test_invalid_flag_exit2():
    # a non-integer flag value is unambiguously an invalid flag per the spec.
    out, _err, rc = run(EXAMPLE, args=["-max-loss=abc"])
    assert rc == 2, rc
    assert out == [], "no decisions on invalid flag"
