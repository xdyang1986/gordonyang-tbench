"""Black-box tests for control plane coordinator: build Go program and drive via stdin/stdout.

Semantics under test:
  * REGISTER <id> <address> <zone> <weight> <timestamp>.
  * Sticky leadership: the primary is set on the first REGISTER and is NOT preempted
    by a later higher-weight/fresher node; it ignores heartbeat expiration; it only
    changes when the current primary is explicitly FAILed (then re-elect the best
    non-failed node by weight -> freshness -> address -> id).
  * QUERY_CONNECT: sum-of-bytes modulo over id-sorted alive nodes.
  * QUERY_ROUTE: deterministic, minimal-disruption (consistent-hashing) routing.
  * QUERY_REPLICAS <k>: rank-ordered, zone-diverse then fill; min(k, alive) entries.
  * QUERY_NODES: id-sorted alive nodes (aliveness uses heartbeat expiration).
  * Durable mode (COORD_STATE_DIR): CRC-framed append log, crash-consistent recovery,
    torn-tail tolerance, atomic compaction. Leadership survives restart & compaction.
"""

import os
import struct
import subprocess
import zlib

import pytest

APP = "/app"
BIN = "/tmp/agent_controlplane"

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
            ["go", "mod", "init", "controlplane"],
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
    assert r.returncode == 0, (
        f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run(stdin, timeout=30, state_dir=None):
    env = {k: v for k, v in os.environ.items() if k != "COORD_STATE_DIR"}
    if state_dir is not None:
        env["COORD_STATE_DIR"] = state_dir
    return subprocess.run(
        [BIN], input=stdin, capture_output=True, text=True, timeout=timeout, env=env
    )


def lines(out):
    return [l for l in out.strip().split("\n") if l != ""]


def record(payload: str) -> bytes:
    b = payload.encode()
    return struct.pack("<II", len(b), zlib.crc32(b) & 0xFFFFFFFF) + b


# --------------------------------------------------------------------------
# In-memory mode — membership, sticky election, routing
# --------------------------------------------------------------------------


def test_example_from_spec():
    stdin = """timeout=60
REGISTER n2 10.0.0.2:8080 zoneB 5 0
REGISTER n1 10.0.0.1:8080 zoneA 5 0
QUERY_PRIMARY 10
QUERY_CONNECT clientA 10
QUERY_NODES 10
FAIL n1 20
QUERY_PRIMARY 30
QUERY_CONNECT clientA 30
HEARTBEAT n2 70
QUERY_PRIMARY 80
QUERY_PRIMARY 140
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    # n2 registered first -> sticky primary (n1 does not preempt).
    # FAIL n1 is not the primary, so primary stays n2. At t=140 n2's heartbeat has
    # expired for aliveness, but leadership is sticky -> still n2.
    assert lines(r.stdout) == [
        "n2 1",
        "10.0.0.1:8080",
        "n1,n2",
        "n2 1",
        "10.0.0.2:8080",
        "n2 1",
        "n2 1",
    ]


def test_primary_is_sticky_first_registered():
    # 'high' has larger weight but registers second -> does not preempt 'low'.
    stdin = "timeout=0\nREGISTER low aaa z1 1 0\nREGISTER high zzz z2 9 0\nQUERY_PRIMARY 0\n"
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["low 1"]


def test_primary_sticky_survives_later_higher_weight_register():
    stdin = (
        "timeout=0\nREGISTER a a_addr z1 1 0\nQUERY_PRIMARY 1\n"
        "REGISTER b b_addr z1 9 5\nQUERY_PRIMARY 6\n"
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "a 1"]


def test_primary_ignores_heartbeat_expiration():
    # Under sticky leadership the primary never expires; only QUERY_NODES expires it.
    stdin = "timeout=10\nREGISTER n1 a1 z1 5 0\nQUERY_PRIMARY 1000\nQUERY_NODES 1000\n"
    r = run(stdin)
    assert lines(r.stdout) == ["n1 1", "NONE"]


def test_primary_stays_even_when_expired_and_another_is_alive():
    # a is primary (first). At t=55 a is expired (aliveness) but b is alive.
    # Primary stays a (sticky); QUERY_NODES shows only b.
    stdin = "timeout=10\nREGISTER a aA z1 5 0\nREGISTER b bB z1 5 50\nQUERY_PRIMARY 55\nQUERY_NODES 55\n"
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "b"]


def test_fail_primary_reelects_best_non_failed():
    # a primary (first). FAIL a -> re-elect among {b,c}: weight tie 9, hb tie,
    # address bB < cC -> b.
    stdin = (
        "timeout=0\nREGISTER a aA z1 5 0\nREGISTER b bB z1 9 0\nREGISTER c cC z1 9 0\n"
        "QUERY_PRIMARY 1\nFAIL a 2\nQUERY_PRIMARY 3\n"
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "b 2"]


def test_fail_non_primary_keeps_primary():
    stdin = "timeout=0\nREGISTER a aA z1 5 0\nREGISTER b bB z1 9 0\nFAIL b 1\nQUERY_PRIMARY 2\n"
    r = run(stdin)
    assert lines(r.stdout) == ["a 1"]


def test_primary_none_before_any_register():
    r = run("timeout=0\nQUERY_PRIMARY 0\n")
    assert lines(r.stdout) == ["NONE"]


def test_failover_changes_primary():
    stdin = "timeout=0\nREGISTER n1 a1 z1 5 0\nREGISTER n2 a2 z1 5 0\nQUERY_PRIMARY 0\nFAIL n1 1\nQUERY_PRIMARY 2\n"
    r = run(stdin)
    # n1 first -> primary (term 1); FAIL n1 -> re-elect n2 (term 2)
    assert lines(r.stdout) == ["n1 1", "n2 2"]


def test_register_revives_after_fail():
    stdin = "timeout=0\nREGISTER n1 a1 z1 5 0\nFAIL n1 1\nQUERY_PRIMARY 2\nREGISTER n1 a1new z1 5 3\nQUERY_PRIMARY 4\nQUERY_CONNECT x 4\n"
    r = run(stdin)
    # term 1 (elect n1) -> NONE after FAIL -> term 2 (re-elect n1 on revive)
    assert lines(r.stdout) == ["NONE", "n1 2", "a1new"]


def test_demote_reelects_same_node_but_bumps_epoch():
    # Sole node: DEMOTE steps it down then re-elects it (still the best) -> the
    # primary is unchanged but the term MUST advance.
    stdin = "timeout=0\nREGISTER a aA z1 5 0\nQUERY_PRIMARY 1\nDEMOTE 2\nQUERY_PRIMARY 3\n"
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "a 2"]


def test_demote_lets_blocked_higher_weight_take_over():
    # 'a' is sticky primary; 'b' (weight 9) was blocked by stickiness. DEMOTE
    # re-elects best non-failed -> b now wins WITHOUT any FAIL. Term advances.
    stdin = (
        "timeout=0\nREGISTER a aA z1 1 0\nREGISTER b bB z1 9 0\nQUERY_PRIMARY 1\n"
        "DEMOTE 2\nQUERY_PRIMARY 3\n"
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "b 2"]


def test_demote_no_primary_is_noop():
    stdin = "timeout=0\nDEMOTE 0\nQUERY_PRIMARY 1\n"
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["NONE"]


def test_demote_then_sticky_resumes():
    # After DEMOTE hands leadership to b, a later higher-weight REGISTER must NOT
    # preempt b -- stickiness resumes for the new incumbent.
    stdin = (
        "timeout=0\nREGISTER a aA z1 1 0\nDEMOTE 1\nQUERY_PRIMARY 2\n"  # re-elect a, term 2
        "REGISTER b bB z1 9 3\nQUERY_PRIMARY 4\n"                        # sticky: still a
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 2", "a 2"]


def test_transfer_forces_named_node_ignoring_rank():
    # 'a' is sticky primary. TRANSFER to 'b' hands leadership to b even though b
    # has *lower* weight than a -- ranking is ignored on an explicit handoff.
    stdin = (
        "timeout=0\nREGISTER a aA z1 9 0\nREGISTER b bB z1 1 0\nQUERY_PRIMARY 1\n"
        "TRANSFER b 2\nQUERY_PRIMARY 3\n"
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "b 2"]


def test_transfer_to_self_bumps_epoch():
    # A handoff to the current primary is still a valid handoff: same primary, new term.
    stdin = "timeout=0\nREGISTER a aA z1 5 0\nQUERY_PRIMARY 1\nTRANSFER a 2\nQUERY_PRIMARY 3\n"
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "a 2"]


def test_transfer_to_unknown_or_failed_is_noop():
    # Unknown target -> ignored; failed target -> ignored. Term unchanged either way.
    stdin = (
        "timeout=0\nREGISTER a aA z1 5 0\nTRANSFER ghost 1\nQUERY_PRIMARY 2\n"  # a 1
        "REGISTER b bB z1 5 3\nFAIL b 4\nTRANSFER b 5\nQUERY_PRIMARY 6\n"       # a 1
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "a 1"]


def test_transfer_then_sticky_resumes():
    # After TRANSFER to b, a later higher-weight REGISTER must not preempt b.
    stdin = (
        "timeout=0\nREGISTER a aA z1 5 0\nREGISTER b bB z1 1 0\nTRANSFER b 1\n"
        "QUERY_PRIMARY 2\nREGISTER c cC z1 9 3\nQUERY_PRIMARY 4\n"
    )
    r = run(stdin)
    assert lines(r.stdout) == ["b 2", "b 2"]


def test_connect_hash_selection():
    stdin = (
        "timeout=0\nREGISTER a addrA z1 1 0\nREGISTER b addrB z1 1 0\nREGISTER c addrC z1 1 0\n"
        "QUERY_CONNECT clientA 0\nQUERY_CONNECT clientB 0\nQUERY_CONNECT clientC 0\n"
    )
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["addrC", "addrA", "addrB"]


def test_nodes_list_sorted_and_none():
    stdin = "timeout=0\nQUERY_NODES 0\nREGISTER z az z1 1 0\nREGISTER a aa z1 1 0\nREGISTER m am z1 1 0\nQUERY_NODES 1\nFAIL a 2\nQUERY_NODES 3\n"
    r = run(stdin)
    assert lines(r.stdout) == ["NONE", "a,m,z", "m,z"]


def test_deterministic():
    stdin = "timeout=5\nREGISTER b baddr z1 5 0\nREGISTER a aaddr z1 5 0\nQUERY_PRIMARY 1\nQUERY_CONNECT cli 1\nQUERY_NODES 1\n"
    a = run(stdin)
    b = run(stdin)
    assert a.stdout == b.stdout and a.stdout != ""


def test_invalid_config_exits_nonzero():
    assert run("timeout=-1\n").returncode != 0
    assert run("timeout=abc\n").returncode != 0
    assert run("badconfig\n").returncode != 0


def test_invalid_command_exits_nonzero():
    assert run("timeout=0\nUNKNOWN 0\n").returncode != 0
    assert run("timeout=0\nREGISTER onlytwo 0\n").returncode != 0
    assert run("timeout=0\nREGISTER n1 addr z1 notint 0\n").returncode != 0
    assert run("timeout=0\nREGISTER n1 addr z1 -1 0\n").returncode != 0
    assert run("timeout=0\nQUERY_PRIMARY notint\n").returncode != 0
    assert run("timeout=0\nQUERY_REPLICAS 0 0\n").returncode != 0
    assert run("timeout=0\nQUERY_REPLICAS -1 0\n").returncode != 0
    assert run("timeout=0\nDEMOTE\n").returncode != 0
    assert run("timeout=0\nDEMOTE notint\n").returncode != 0
    assert run("timeout=0\nTRANSFER onlyone\n").returncode != 0
    assert run("timeout=0\nTRANSFER n1 notint\n").returncode != 0


def test_heartbeat_unknown_ignored_not_error():
    stdin = "timeout=0\nHEARTBEAT unknown 0\nQUERY_PRIMARY 0\n"
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["NONE"]


def test_heartbeat_on_failed_node_ignored():
    stdin = "timeout=0\nREGISTER n1 a1 z1 5 0\nFAIL n1 1\nHEARTBEAT n1 2\nQUERY_PRIMARY 3\n"
    r = run(stdin)
    assert lines(r.stdout) == ["NONE"]


def test_address_update_on_reregister():
    stdin = "timeout=0\nREGISTER n1 old z1 5 0\nREGISTER n1 new z1 5 1\nQUERY_CONNECT c 2\n"
    r = run(stdin)
    assert lines(r.stdout) == ["new"]


def test_inmemory_mode_creates_no_state():
    r1 = run("timeout=0\nREGISTER n1 a1 z1 5 0\n")
    assert r1.returncode == 0
    r2 = run("timeout=0\nQUERY_NODES 0\n")
    assert lines(r2.stdout) == ["NONE"]


# --------------------------------------------------------------------------
# Zone-aware replica selection
# --------------------------------------------------------------------------


def _four_nodes():
    # ranked -> [n3(w9,Z2), n1(w5,a1,Z1), n2(w5,a2,Z1), n4(w1,Z3)]
    return (
        "timeout=0\n"
        "REGISTER n1 a1 Z1 5 0\n"
        "REGISTER n2 a2 Z1 5 0\n"
        "REGISTER n3 a3 Z2 9 0\n"
        "REGISTER n4 a4 Z3 1 0\n"
    )


def test_replicas_zone_diverse():
    r = run(_four_nodes() + "QUERY_REPLICAS 3 0\n")
    assert lines(r.stdout) == ["n3,n1,n4"]


def test_replicas_fallback_fills_when_zones_exhausted():
    r = run(_four_nodes() + "QUERY_REPLICAS 4 0\n")
    assert lines(r.stdout) == ["n3,n1,n4,n2"]


def test_replicas_k_exceeds_node_count():
    r = run(_four_nodes() + "QUERY_REPLICAS 10 0\n")
    assert lines(r.stdout) == ["n3,n1,n4,n2"]


def test_replicas_single_zone_fallback():
    stdin = "timeout=0\nREGISTER a aa Z1 5 0\nREGISTER b bb Z1 5 0\nQUERY_REPLICAS 2 0\n"
    r = run(stdin)
    assert lines(r.stdout) == ["a,b"]


def test_replicas_excludes_dead_nodes():
    stdin = "timeout=0\nREGISTER n1 a1 Z1 5 0\nREGISTER n2 a2 Z2 5 0\nFAIL n1 1\nQUERY_REPLICAS 3 2\n"
    r = run(stdin)
    assert lines(r.stdout) == ["n2"]


def test_replicas_none_when_no_alive():
    r = run("timeout=0\nQUERY_REPLICAS 3 0\n")
    assert lines(r.stdout) == ["NONE"]


# --------------------------------------------------------------------------
# Stable routing (QUERY_ROUTE) — minimal-disruption property
# --------------------------------------------------------------------------

_ROUTE_NODES = "".join(f"REGISTER n{i} addr{i} z{i % 3} 5 0\n" for i in range(6))
_CLIENTS = [f"client{i}" for i in range(40)]


def _routes(extra_cmds, query_ts):
    stdin = "timeout=0\n" + _ROUTE_NODES + extra_cmds
    stdin += "".join(f"QUERY_ROUTE {c} {query_ts}\n" for c in _CLIENTS)
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    out = lines(r.stdout)
    assert len(out) == len(_CLIENTS)
    return dict(zip(_CLIENTS, out))


def test_route_single_node_and_none():
    r = run("timeout=0\nQUERY_ROUTE x 0\nREGISTER only theaddr z1 5 0\nQUERY_ROUTE x 1\n")
    assert lines(r.stdout) == ["NONE", "theaddr"]


def test_route_is_deterministic():
    assert _routes("", 0) == _routes("", 0)


def test_route_targets_are_all_alive():
    routed = _routes("", 0)
    valid = {f"addr{i}" for i in range(6)}
    assert set(routed.values()) <= valid
    assert len(set(routed.values())) >= 2


def test_route_stable_on_node_removal():
    before = _routes("", 0)
    after = _routes("FAIL n0 1\n", 2)
    for c in _CLIENTS:
        if before[c] != "addr0":
            assert after[c] == before[c], (
                f"{c} moved {before[c]}->{after[c]} but its node was not removed"
            )
    for c in _CLIENTS:
        if before[c] == "addr0":
            assert after[c] != "addr0" and after[c] in {f"addr{i}" for i in range(1, 6)}


def test_route_stable_on_node_addition():
    before = _routes("", 0)
    after = _routes("REGISTER n9 addr9 z1 5 1\n", 2)
    for c in _CLIENTS:
        assert after[c] == before[c] or after[c] == "addr9", (
            f"{c} moved {before[c]}->{after[c]} on addition of an unrelated node"
        )


def test_route_reassigns_when_own_node_fails():
    before = _routes("", 0)
    victim = next((c for c in _CLIENTS if before[c] == "addr0"), None)
    assert victim is not None
    after = _routes("FAIL n0 1\n", 2)
    assert after[victim] != "addr0" and after[victim] != "NONE"


def test_route_stable_across_restart(tmp_path):
    d = str(tmp_path)
    run("timeout=0\n" + _ROUTE_NODES, state_dir=d)
    stdin = "timeout=0\n" + "".join(f"QUERY_ROUTE {c} 5\n" for c in _CLIENTS)
    r1 = run(stdin, state_dir=d)
    r2 = run(stdin, state_dir=d)
    assert lines(r1.stdout) == lines(r2.stdout)
    assert len(lines(r1.stdout)) == len(_CLIENTS)


# --------------------------------------------------------------------------
# Durable mode
# --------------------------------------------------------------------------


def test_persist_across_restart(tmp_path):
    d = str(tmp_path)
    r1 = run("timeout=0\nREGISTER n2 a2 z1 5 0\nREGISTER n1 a1 z1 5 0\n", state_dir=d)
    assert r1.returncode == 0
    r2 = run("timeout=0\nQUERY_NODES 5\nQUERY_PRIMARY 5\nQUERY_CONNECT c 5\n", state_dir=d)
    assert r2.returncode == 0
    # n2 registered first -> sticky primary recovered as n2 (term 1); connect 'c'=99%2=1 -> n2 -> a2
    assert lines(r2.stdout) == ["n1,n2", "n2 1", "a2"]


def test_sticky_primary_survives_restart(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER low la z1 1 0\nREGISTER high ha z2 9 0\n", state_dir=d)
    r = run("timeout=0\nQUERY_PRIMARY 5\n", state_dir=d)
    assert lines(r.stdout) == ["low 1"]  # sticky incumbent + term recovered


def test_sticky_primary_survives_compaction(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER low la z1 1 0\nREGISTER high ha z2 9 0\n", state_dir=d)
    run("timeout=0\nCOMPACT 10\n", state_dir=d)
    r = run("timeout=0\nQUERY_PRIMARY 11\n", state_dir=d)
    assert lines(r.stdout) == ["low 1"]  # compaction must preserve incumbent + term


def test_epoch_increments_only_on_new_election():
    # term bumps on first election and each re-election; sticky/none do not bump.
    stdin = (
        "timeout=0\n"
        "REGISTER a aA z1 5 0\n"       # elect a -> term 1
        "REGISTER b bB z1 9 0\n"       # sticky, no bump
        "QUERY_PRIMARY 1\n"           # a 1
        "FAIL a 2\n"                   # re-elect b -> term 2
        "QUERY_PRIMARY 3\n"           # b 2
        "FAIL b 4\n"                   # no non-failed left -> NONE, term unchanged
        "QUERY_PRIMARY 5\n"           # NONE
        "REGISTER c cC z1 1 6\n"       # elect c -> term 3
        "QUERY_PRIMARY 7\n"           # c 3
    )
    r = run(stdin)
    assert lines(r.stdout) == ["a 1", "b 2", "NONE", "c 3"]


def test_epoch_unchanged_by_sticky_register():
    stdin = (
        "timeout=0\nREGISTER a aA z1 1 0\nQUERY_PRIMARY 1\n"
        "REGISTER b bB z1 9 2\nREGISTER c cC z1 9 3\nQUERY_PRIMARY 4\n"
    )
    r = run(stdin)
    # later higher-weight registers are sticky no-ops: term stays 1
    assert lines(r.stdout) == ["a 1", "a 1"]


def test_epoch_survives_compaction(tmp_path):
    d = str(tmp_path)
    # elect a (term 1) -> fail a (none) -> elect b (term 2)
    run("timeout=0\nREGISTER a aA z1 5 0\nFAIL a 1\nREGISTER b bB z1 5 2\n", state_dir=d)
    run("timeout=0\nCOMPACT 3\n", state_dir=d)
    # compacted log replays to a single election; the term must be persisted (=2)
    r = run("timeout=0\nQUERY_PRIMARY 4\n", state_dir=d)
    assert lines(r.stdout) == ["b 2"]


def test_demote_survives_restart(tmp_path):
    d = str(tmp_path)
    # a sticky primary; b(w9) blocked. DEMOTE hands leadership to b at term 2.
    run("timeout=0\nREGISTER a aA z1 1 0\nREGISTER b bB z1 9 0\nDEMOTE 2\n", state_dir=d)
    r = run("timeout=0\nQUERY_PRIMARY 3\n", state_dir=d)
    assert lines(r.stdout) == ["b 2"]  # DEMOTE logged & replayed


def test_demote_survives_compaction(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER a aA z1 1 0\nREGISTER b bB z1 9 0\nDEMOTE 2\n", state_dir=d)
    run("timeout=0\nCOMPACT 5\n", state_dir=d)
    r = run("timeout=0\nQUERY_PRIMARY 6\n", state_dir=d)
    assert lines(r.stdout) == ["b 2"]  # post-DEMOTE incumbent + term preserved


def test_transfer_survives_restart(tmp_path):
    d = str(tmp_path)
    # a is best (w9) but leadership is TRANSFERred to lower-weight b.
    run("timeout=0\nREGISTER a aA z1 9 0\nREGISTER b bB z1 1 0\nTRANSFER b 2\n", state_dir=d)
    r = run("timeout=0\nQUERY_PRIMARY 3\n", state_dir=d)
    assert lines(r.stdout) == ["b 2"]  # TRANSFER logged & replayed


def test_transfer_survives_compaction(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER a aA z1 9 0\nREGISTER b bB z1 1 0\nTRANSFER b 2\n", state_dir=d)
    run("timeout=0\nCOMPACT 5\n", state_dir=d)
    # compaction must emit b (the incumbent) first so replay keeps b primary even
    # though a outranks it, and persist the term (=2).
    r = run("timeout=0\nQUERY_PRIMARY 6\n", state_dir=d)
    assert lines(r.stdout) == ["b 2"]


def test_replicas_survive_restart(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 Z1 5 0\nREGISTER n2 a2 Z2 9 0\nREGISTER n3 a3 Z1 1 0\n", state_dir=d)
    r = run("timeout=0\nQUERY_REPLICAS 2 5\n", state_dir=d)
    assert lines(r.stdout) == ["n2,n1"]


def test_persist_expiry_uses_current_session_timeout(tmp_path):
    d = str(tmp_path)
    run("timeout=10\nREGISTER n1 a1 z1 5 0\nHEARTBEAT n1 8\n", state_dir=d)
    r = run("timeout=10\nQUERY_NODES 18\nQUERY_NODES 19\n", state_dir=d)
    assert lines(r.stdout) == ["n1", "NONE"]


def test_persist_fail_and_revive(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 z1 5 0\nFAIL n1 1\n", state_dir=d)
    r2 = run("timeout=0\nQUERY_PRIMARY 2\n", state_dir=d)
    assert lines(r2.stdout) == ["NONE"]
    run("timeout=0\nREGISTER n1 a1new z1 5 3\n", state_dir=d)
    r3 = run("timeout=0\nQUERY_PRIMARY 4\nQUERY_CONNECT x 4\n", state_dir=d)
    assert lines(r3.stdout) == ["n1 2", "a1new"]  # term 2 after fail+revive, recovered


def test_recover_ignores_torn_tail(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 z1 5 0\nREGISTER n2 a2 z1 5 0\n", state_dir=d)
    logp = os.path.join(d, "coordinator.log")
    with open(logp, "ab") as f:
        f.write(b"\x05\x00\x00\x00")
    r = run("timeout=0\nQUERY_NODES 1\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1,n2"]


def test_recover_ignores_bad_crc_tail(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 z1 5 0\n", state_dir=d)
    logp = os.path.join(d, "coordinator.log")
    payload = b"REGISTER n9 a9 z1 5 0"
    with open(logp, "ab") as f:
        f.write(struct.pack("<II", len(payload), 0xDEADBEEF) + payload)
    r = run("timeout=0\nQUERY_NODES 1\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1"]


def test_replays_manually_appended_valid_record(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 z1 5 0\n", state_dir=d)
    logp = os.path.join(d, "coordinator.log")
    with open(logp, "ab") as f:
        f.write(record("REGISTER n2 a2 z1 5 3"))
    r = run("timeout=0\nQUERY_NODES 5\nQUERY_CONNECT c 5\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1,n2", "a2"]


def test_torn_tail_is_truncated_then_appendable(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 z1 5 0\n", state_dir=d)
    logp = os.path.join(d, "coordinator.log")
    with open(logp, "ab") as f:
        f.write(b"\xff\xff")
    run("timeout=0\nREGISTER n2 a2 z1 5 1\n", state_dir=d)
    r = run("timeout=0\nQUERY_NODES 2\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1,n2"]


def test_compact_preserves_state_and_shrinks_log(tmp_path):
    d = str(tmp_path)
    run(
        "timeout=0\nREGISTER a 1 z1 5 0\nREGISTER b 2 z1 5 0\nHEARTBEAT a 5\nFAIL b 6\nREGISTER a 3 z1 5 7\n",
        state_dir=d,
    )
    logp = os.path.join(d, "coordinator.log")
    before = os.path.getsize(logp)
    run("timeout=0\nCOMPACT 10\n", state_dir=d)
    after = os.path.getsize(logp)
    assert after <= before
    # a is primary (registered first); a alive (addr 3), b failed
    r = run("timeout=0\nQUERY_NODES 11\nQUERY_PRIMARY 11\nQUERY_CONNECT z 11\n", state_dir=d)
    assert lines(r.stdout) == ["a", "a 1", "3"]


def test_compact_ignores_stray_tmp(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 a1 z1 5 0\n", state_dir=d)
    with open(os.path.join(d, "coordinator.log.tmp"), "wb") as f:
        f.write(b"garbage that is not a valid log")
    r = run("timeout=0\nQUERY_NODES 1\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1"]


def test_replay_fail_then_revive_sequence(tmp_path):
    d = str(tmp_path)
    run("timeout=0\nREGISTER n1 old z1 5 0\nFAIL n1 1\nREGISTER n1 new z1 7 2\n", state_dir=d)
    r = run("timeout=0\nQUERY_PRIMARY 3\nQUERY_CONNECT z 3\n", state_dir=d)
    assert lines(r.stdout) == ["n1 2", "new"]  # elect(1) -> none -> re-elect(2), replayed


# --------------------------------------------------------------------------
# Implicit requirements & corner cases (expiration probed via QUERY_NODES)
# --------------------------------------------------------------------------


def test_sort_is_lexicographic_not_numeric():
    stdin = "timeout=0\nREGISTER 10 a10 z1 5 0\nREGISTER 2 a2 z1 5 0\nREGISTER 9 a9 z1 5 0\nQUERY_NODES 0\n"
    r = run(stdin)
    assert lines(r.stdout) == ["10,2,9"]


def test_expired_node_revived_by_later_heartbeat():
    stdin = "timeout=10\nREGISTER n1 a1 z1 5 0\nQUERY_NODES 15\nHEARTBEAT n1 16\nQUERY_NODES 20\n"
    r = run(stdin)
    assert lines(r.stdout) == ["NONE", "n1"]


def test_expiry_boundary_inclusive():
    stdin = "timeout=10\nREGISTER n1 a1 z1 5 0\nQUERY_NODES 10\nQUERY_NODES 11\n"
    r = run(stdin)
    assert lines(r.stdout) == ["n1", "NONE"]


def test_blank_lines_and_extra_spaces_ignored():
    stdin = "timeout=0\n\n  \nREGISTER   n1   a1   z1   5   0\n\nQUERY_NODES 0\n"
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1"]


def test_config_is_first_nonempty_line():
    stdin = "\n\n   \ntimeout=0\nREGISTER n1 a1 z1 5 0\nQUERY_PRIMARY 0\n"
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["n1 1"]


def test_per_session_timeout_not_persisted(tmp_path):
    d = str(tmp_path)
    run("timeout=100\nREGISTER n1 a1 z1 5 0\n", state_dir=d)
    r = run("timeout=5\nQUERY_NODES 10\n", state_dir=d)
    assert lines(r.stdout) == ["NONE"]  # 10 - 0 = 10 > 5


def test_empty_log_recovers_clean(tmp_path):
    d = str(tmp_path)
    open(os.path.join(d, "coordinator.log"), "wb").close()
    r = run("timeout=0\nQUERY_NODES 0\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["NONE"]
