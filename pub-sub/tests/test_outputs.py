"""Black-box tests for the Go pub-sub broker CLI.

Builds the Go program under /app and drives it via stdin/stdout. Each input line
is one command; the program prints exactly one output line per command. The
broker's semantics deliberately depart from a textbook fan-out pub-sub; see
instruction.md.
"""

import hashlib
import os
import subprocess

import pytest

APP = "/app"
BIN = "/tmp/agent_pubsub"

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
        subprocess.run(["go", "mod", "init", "pubsub"], cwd=APP, env=GO_ENV,
                       capture_output=True, text=True)

    def _build(pkg):
        return subprocess.run(["go", "build", "-o", BIN, pkg], cwd=APP, env=GO_ENV,
                              capture_output=True, text=True, timeout=240)

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert os.path.exists(BIN), "build produced no binary"
    yield


def run(cmds):
    """Run a list of command strings; return the list of output lines."""
    proc = subprocess.run([BIN], input="\n".join(cmds) + "\n",
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"nonzero exit: {proc.stderr}"
    return proc.stdout.splitlines()


# expectation helpers mirroring the Go hashing

def _score(key, sid):
    return int.from_bytes(hashlib.sha256(f"{key}:{sid}".encode()).digest()[:8], "big")


def _shard_rank(key, ids):
    return sorted(ids, key=lambda s: (_score(key, s), s), reverse=True)


def _ring_owner(key, subs_caps):
    def pos(label):
        return int.from_bytes(hashlib.sha256(label.encode()).digest()[:8], "big")
    ring = []
    for sid, cap in subs_caps:
        for v in range(cap):
            ring.append((pos(f"{sid}#{v}"), sid))
    if not ring:
        return None
    ring.sort(key=lambda pv: (pv[0], pv[1]))
    kpos = pos(key)
    for pp, sid in ring:
        if pp >= kpos:
            return sid
    return ring[0][1]


# --------------------------------------------------------------------------- #
# basics + specificity routing / ordering
# --------------------------------------------------------------------------- #


def test_subscribe_ids_incremental_and_basic_publish():
    out = run(["SUB order.created 0 1 -1", "SUB t 0 1 -1", "PUB order.created x"])
    assert out[0] == "id=1 replay="
    assert out[1] == "id=2 replay="
    assert out[2] == "1:1"


def test_specificity_exact_suppresses_wildcards():
    out = run([
        "SUB order.created 0 1 -1", "SUB order.* 0 1 -1", "SUB * 0 1 -1",
        "PUB order.created x", "PUBALL order.created x", "MATCH order.created",
    ])
    assert out[3] == "1:1"           # only exact tier
    assert out[4] == "3:3,2,1"       # fan-out, priority ties -> id desc
    assert out[5] == "1"


def test_longest_prefix_tier_wins():
    out = run([
        "SUB order.* 0 1 -1", "SUB order.created.* 0 1 -1", "SUB * 0 1 -1",
        "PUB order.created.detail d", "PUB order.foo d",
    ])
    assert out[3] == "1:2"   # order.created.* tier (id 2)
    assert out[4] == "1:1"   # order.* tier (id 1)


def test_prefix_dot_boundary():
    out = run(["SUB order.* 0 1 -1", "PUB order d", "PUB order123 d", "PUB other.order d"])
    assert out[1] == "1:1"
    assert out[2] == "0:"
    assert out[3] == "0:"


def test_ordering_priority_then_id_desc():
    out = run([
        "SUB t 0 1 -1", "SUB t 0 1 -1", "SUB t 5 1 -1", "SUB t 5 1 -1", "PUB t d",
    ])
    # ids 1,2 pri0 ; ids 3,4 pri5 -> order 4,3,2,1
    assert out[4] == "4:4,3,2,1"


def test_global_only_when_nothing_more_specific():
    out = run(["SUB order.* 0 1 -1", "SUB * 0 1 -1", "PUB billing.paid d", "PUB order.x d"])
    assert out[2] == "1:2"   # global (id 2)
    assert out[3] == "1:1"   # prefix (id 1)


# --------------------------------------------------------------------------- #
# max_calls / retained replay
# --------------------------------------------------------------------------- #


def test_max_calls_removes_after_n():
    out = run(["SUB t 0 1 2", "PUB t a", "PUB t b", "PUB t c", "COUNT"])
    assert out[1] == "1:1"
    assert out[2] == "1:1"
    assert out[3] == "0:"
    assert out[4] == "0"


def test_subonce_via_maxcalls_one():
    out = run(["SUB t 0 1 1", "PUB t a", "PUB t b", "COUNT"])
    assert out[1] == "1:1"
    assert out[2] == "0:"
    assert out[3] == "0"


def test_retained_replay_on_subscribe_in_order():
    out = run([
        "PUB a 1 R", "PUB b 2 R", "SUB * 0 1 -1", "RETAINED",
    ])
    assert out[0] == "0:"   # no subscribers yet
    assert out[1] == "0:"
    assert out[2] == "id=1 replay=1,2"   # both retained replayed in insertion order
    assert out[3] == "a,b"


def test_retained_replay_respects_max_calls():
    out = run(["PUB a 1 R", "PUB b 2 R", "SUB * 0 1 1", "COUNT"])
    assert out[2] == "id=1 replay=1"   # budget 1 -> only first retained
    assert out[3] == "0"               # removed after it


def test_muted_publish_still_retains():
    out = run(["MUTE t", "PUB t 9 R", "RETAINED", "SUB t 0 1 -1"])
    assert out[1] == "0:"
    assert out[2] == "t"
    assert out[3] == "id=1 replay=9"


# --------------------------------------------------------------------------- #
# pipeline: mute / pause / history
# --------------------------------------------------------------------------- #


def test_mute_suppresses_delivery_and_history():
    out = run(["SUB t 0 1 -1", "MUTE t", "PUB t d", "HISTORY t", "UNMUTE t", "PUB t d", "HISTORY t"])
    assert out[2] == "0:"
    assert out[3] == ""       # not recorded while muted
    assert out[4] == "true"
    assert out[5] == "1:1"
    assert out[6] == "d"


def test_pause_queues_then_resume_replays_fifo():
    out = run(["SUB t 0 1 -1", "PAUSE", "PUB t a", "PUB t b", "PAUSED", "RESUME", "HISTORY t"])
    assert out[2] == "0:"
    assert out[3] == "0:"
    assert out[4] == "true"
    assert out[5] == "2"      # 2 callbacks across replay
    assert out[6] == "a,b"


def test_history_records_across_topics():
    out = run(["PUB a 1", "PUB a 2", "PUB b 9", "HISTORY a", "HISTORY b", "CLEARHIST a", "HISTORY a"])
    assert out[3] == "1,2"
    assert out[4] == "9"
    assert out[6] == ""


def test_delivered_count():
    out = run(["SUB t 0 1 -1", "SUB t 0 1 -1", "PUB t d", "PUBALL t d", "DELIVERED"])
    assert out[4] == "4"


# --------------------------------------------------------------------------- #
# distribute (water-filling)
# --------------------------------------------------------------------------- #


def test_distribute_even_split():
    out = run(["SUB t 0 10 -1", "SUB t 0 10 -1", "SUB t 0 10 -1", "DISTRIBUTE t 9"])
    assert out[3] == "overflow=0 alloc=1:3,2:3,3:3"


def test_distribute_remainder_to_smallest_ids():
    out = run(["SUB t 0 10 -1", "SUB t 0 10 -1", "SUB t 0 10 -1", "DISTRIBUTE t 10"])
    assert out[3] == "overflow=0 alloc=1:4,2:3,3:3"


def test_distribute_saturation_cascade():
    out = run(["SUB t 0 1 -1", "SUB t 0 10 -1", "SUB t 0 10 -1", "DISTRIBUTE t 12"])
    assert out[3] == "overflow=0 alloc=1:1,2:6,3:5"


def test_distribute_overflow():
    out = run(["SUB t 0 2 -1", "SUB t 0 3 -1", "DISTRIBUTE t 10"])
    assert out[2] == "overflow=5 alloc=1:2,2:3"


def test_distribute_deep_cascade():
    out = run(["SUB t 0 1 -1", "SUB t 0 1 -1", "SUB t 0 1 -1", "SUB t 0 100 -1", "DISTRIBUTE t 10"])
    assert out[4] == "overflow=0 alloc=1:1,2:1,3:1,4:7"


# --------------------------------------------------------------------------- #
# sharded / hashring (compare against sha256 oracle)
# --------------------------------------------------------------------------- #


def test_shard_top_n_rendezvous():
    cmds = ["SUB t 0 1 -1"] * 6
    ids = list(range(1, 7))
    key = "user-42"
    cmds.append(f"SHARD t {key} 3")
    out = run(cmds)
    expected = _shard_rank(key, ids)[:3]
    assert out[6] == ",".join(str(x) for x in expected)


def test_shard_n_zero_and_deterministic():
    cmds = ["SUB t 0 1 -1"] * 3 + ["SHARD t k 0", "SHARD t k 2", "SHARD t k 2"]
    out = run(cmds)
    assert out[3] == ""
    assert out[4] == out[5]


def test_hashring_owner():
    caps = [3, 3, 1]
    cmds = [f"SUB t 0 {c} -1" for c in caps]
    subs_caps = [(i + 1, caps[i]) for i in range(len(caps))]
    for key in ("alpha", "beta", "gamma", "delta"):
        cmds.append(f"RING t {key}")
    out = run(cmds)
    for i, key in enumerate(("alpha", "beta", "gamma", "delta")):
        assert out[3 + i] == str(_ring_owner(key, subs_caps))


def test_hashring_empty_tier_none():
    out = run(["RING t k", "SUB t 0 0 -1", "RING t k"])
    assert out[0] == "none"
    assert out[2] == "none"


# --------------------------------------------------------------------------- #
# fair (stride scheduling)
# --------------------------------------------------------------------------- #


def test_fair_weighted_sequence():
    cmds = ["SUB t 0 1 -1", "SUB t 0 2 -1"] + ["FAIR t"] * 6
    out = run(cmds)
    assert out[2:8] == ["1", "2", "2", "1", "2", "2"]


def test_fair_none_when_no_eligible():
    out = run(["FAIR t", "SUB t 0 0 -1", "FAIR t"])
    assert out[0] == "none"
    assert out[2] == "none"


# --------------------------------------------------------------------------- #
# token bucket
# --------------------------------------------------------------------------- #


def test_meter_until_empty_then_refill():
    out = run(["SUB t 0 2 -1", "METER t 1", "METER t 1", "METER t 1", "TOKENS 1",
               "REFILL t 5", "TOKENS 1", "METER t 1"])
    assert out[1] == "1"
    assert out[2] == "1"
    assert out[3] == ""      # empty
    assert out[4] == "0"
    assert out[6] == "2"     # capped at capacity
    assert out[7] == "1"


def test_meter_cost():
    out = run(["SUB t 0 3 -1", "METER t 2", "TOKENS 1", "METER t 2"])
    assert out[1] == "1"
    assert out[2] == "1"
    assert out[3] == ""      # only 1 token, cost 2


# --------------------------------------------------------------------------- #
# sequence delivery
# --------------------------------------------------------------------------- #


def test_seq_in_order_and_reorder_flush():
    out = run(["SUB t 0 1 -1", "SEQ t 0", "SEQ t 2", "SEQ t 3", "PENDING t",
               "SEQ t 1", "NEXTSEQ t", "PENDING t"])
    assert out[1] == "0"
    assert out[2] == ""
    assert out[3] == ""
    assert out[4] == "2,3"
    assert out[5] == "1,2,3"
    assert out[6] == "4"
    assert out[7] == ""


def test_seq_drops_stale():
    out = run(["SEQ t 0", "SEQ t 1", "SEQ t 0", "NEXTSEQ t"])
    assert out[2] == ""
    assert out[3] == "2"


# --------------------------------------------------------------------------- #
# ordered (k-of-n dependency cascade)
# --------------------------------------------------------------------------- #


def test_ordered_cascade_reversed_chain():
    out = run(['ORDERED [{"id":"C","topic":"t","deps":["B"]},'
               '{"id":"B","topic":"t","deps":["A"]},{"id":"A","topic":"t"}]'])
    assert out[0] == "delivered=A,B,C undeliverable= count=0"


def test_ordered_priority_tiebreak():
    out = run(['ORDERED [{"id":"A","topic":"t","priority":1},'
               '{"id":"B","topic":"t","priority":5},{"id":"C","topic":"t","priority":3}]'])
    assert out[0] == "delivered=B,C,A undeliverable= count=0"


def test_ordered_threshold_k_of_n():
    out = run(['ORDERED [{"id":"D1","topic":"t"},{"id":"D2","topic":"t"},'
               '{"id":"X","topic":"t","deps":["D1","D2","D3"],"threshold":2},'
               '{"id":"D3","topic":"t"}]'])
    assert out[0] == "delivered=D1,D2,X,D3 undeliverable= count=0"


def test_ordered_missing_dep_and_cycle_undeliverable():
    out = run(['ORDERED [{"id":"X","topic":"t","deps":["ghost"]},'
               '{"id":"Y","topic":"t","deps":["X"]},{"id":"Z","topic":"t"}]'])
    assert out[0] == "delivered=Z undeliverable=X,Y count=0"
    out2 = run(['ORDERED [{"id":"A","topic":"t","deps":["B"]},{"id":"B","topic":"t","deps":["A"]}]'])
    assert out2[0] == "delivered= undeliverable=A,B count=0"


def test_ordered_delivers_to_subscribers():
    out = run(["SUB t 0 1 -1",
               'ORDERED [{"id":"B","topic":"t","deps":["A"]},{"id":"A","topic":"t"}]',
               "DELIVERED"])
    assert out[1] == "delivered=A,B undeliverable= count=2"
    assert out[2] == "2"


def test_ordered_duplicate_id_err():
    out = run(['ORDERED [{"id":"A","topic":"t"},{"id":"A","topic":"t"}]'])
    assert out[0] == "ERR"


# --------------------------------------------------------------------------- #
# introspection + subscribe_many + validation
# --------------------------------------------------------------------------- #


def test_unsub_count_topics():
    out = run(["SUB a 0 1 -1", "SUB a 0 1 -1", "SUB b.* 0 1 -1",
               "COUNT", "COUNT a", "TOPICS", "UNSUB 1", "UNSUB 1", "COUNT"])
    assert out[3] == "3"
    assert out[4] == "2"
    assert out[5] == "a,b.*"   # sorted distinct patterns
    assert out[6] == "true"
    assert out[7] == "false"
    assert out[8] == "2"


def test_submany_atomic():
    out = run(["SUBMANY a,b.* 0 1 -1", "COUNT", "SUBMANY x,bad*,y 0 1 -1", "COUNT"])
    assert out[0] == "ids=1,2"
    assert out[1] == "2"
    assert out[2] == "ERR"      # invalid pattern -> atomic failure
    assert out[3] == "2"        # nothing added


def test_validation_errors():
    out = run([
        "SUB ord*er 0 1 -1",       # bad pattern
        "SUB a 0 -1 -1",           # bad capacity
        "SUB a 0 1 0",             # bad max_calls
        "PUB * d",                 # bad publish topic
        "MATCH *",
        "DISTRIBUTE t -1",
        "SHARD t  1",              # empty key
        "SEQ t -1",
        "BOGUS",
    ])
    assert out == ["ERR"] * 9


# --------------------------------------------------------------------------- #
# Corner cases: routing / tiers
# --------------------------------------------------------------------------- #


def test_multiple_in_tier_all_fire_wildcard_suppressed():
    out = run(["SUB t 0 1 -1", "SUB t 0 1 -1", "SUB * 0 1 -1", "PUB t d"])
    assert out[3] == "2:2,1"   # both exact fire (id desc), global suppressed


def test_exact_wins_over_prefix_equal_to_topic():
    out = run(["SUB order 0 1 -1", "SUB order.* 0 1 -1", "PUB order d"])
    assert out[2] == "1:1"     # exact "order" wins; "order.*" (which matches) suppressed


def test_clear_topic_exact_only():
    out = run(["SUB a 0 1 -1", "SUB a 0 1 -1", "SUB b 0 1 -1", "CLEAR a", "COUNT", "COUNT b"])
    assert out[4] == "1"
    assert out[5] == "1"


def test_match_prefix_and_global_tiers():
    out = run(["SUB order.* 0 1 -1", "SUB * 0 1 -1", "MATCH order.x", "MATCH billing"])
    assert out[2] == "1"       # prefix tier
    assert out[3] == "1"       # global tier


# --------------------------------------------------------------------------- #
# Corner cases: retained / pipeline ordering
# --------------------------------------------------------------------------- #


def test_retain_keeps_latest_value():
    out = run(["PUB t 1 R", "PUB t 2 R", "SUB t 0 1 -1"])
    assert out[2] == "id=1 replay=2"


def test_retained_prefix_match_subset():
    out = run(["PUB order.created 1 R", "PUB user.x 2 R", "SUB order.* 0 5 -1"])
    assert out[2] == "id=1 replay=1"


def test_pause_defers_retain():
    out = run(["PAUSE", "PUB t 5 R", "RETAINED", "RESUME", "RETAINED"])
    assert out[2] == ""        # retain deferred while paused
    assert out[4] == "t"


def test_resume_applies_pause_time_mute():
    out = run(["SUB t 0 1 -1", "PAUSE", "PUB t d", "MUTE t", "RESUME", "HISTORY t"])
    assert out[4] == "0"       # muted at replay time -> 0 delivered
    assert out[5] == ""        # not recorded


def test_clearretain_one_vs_all():
    out = run(["PUB a 1 R", "PUB b 2 R", "CLEARRETAIN a", "RETAINED", "CLEARRETAIN", "RETAINED"])
    assert out[3] == "b"
    assert out[5] == ""


def test_retained_replay_counts_delivered():
    out = run(["PUB t v R", "SUB t 0 1 -1", "DELIVERED"])
    assert out[2] == "1"


# --------------------------------------------------------------------------- #
# Corner cases: distribute boundaries
# --------------------------------------------------------------------------- #


def test_distribute_load_zero():
    out = run(["SUB t 0 5 -1", "DISTRIBUTE t 0"])
    assert out[1] == "overflow=0 alloc=1:0"


def test_distribute_load_equals_total_capacity():
    out = run(["SUB t 0 2 -1", "SUB t 0 3 -1", "DISTRIBUTE t 5"])
    assert out[2] == "overflow=0 alloc=1:2,2:3"


def test_distribute_all_zero_capacity_overflow():
    out = run(["SUB t 0 0 -1", "SUB t 0 0 -1", "DISTRIBUTE t 3"])
    assert out[2] == "overflow=3 alloc=1:0,2:0"


def test_distribute_over_prefix_tier():
    out = run(["SUB order.* 0 5 -1", "DISTRIBUTE order.created 3"])
    assert out[1] == "overflow=0 alloc=1:3"


def test_distribute_does_not_count_delivered():
    out = run(["SUB t 0 5 -1", "DISTRIBUTE t 3", "DELIVERED"])
    assert out[2] == "0"


# --------------------------------------------------------------------------- #
# Corner cases: shard / ring / fair
# --------------------------------------------------------------------------- #


def test_shard_n_exceeds_and_specificity():
    ids = [1, 2, 3]
    cmds = ["SUB t 0 1 -1"] * 3 + ["SUB * 0 1 -1", "SHARD t k 10"]
    out = run(cmds)
    assert out[4] == ",".join(str(x) for x in _shard_rank("k", ids))  # only exact tier


def test_shard_counts_delivered():
    out = run(["SUB t 0 1 -1", "SUB t 0 1 -1", "SHARD t k 2", "DELIVERED"])
    assert out[3] == "2"


def test_ring_deterministic_and_specificity():
    caps = [2, 2]
    cmds = [f"SUB t 0 {c} -1" for c in caps] + ["SUB * 0 2 -1", "RING t k", "RING t k"]
    out = run(cmds)
    subs_caps = [(1, 2), (2, 2)]
    assert out[3] == str(_ring_owner("k", subs_caps))  # exact tier only
    assert out[3] == out[4]


def test_fair_single_and_zero_capacity():
    out = run(["SUB t 0 3 -1", "FAIR t", "FAIR t", "FAIR t"])
    assert out[1:4] == ["1", "1", "1"]
    out2 = run(["SUB t 0 0 -1", "SUB t 0 1 -1", "FAIR t", "FAIR t"])
    assert out2[2:4] == ["2", "2"]


def test_fair_counts_delivered():
    out = run(["SUB t 0 1 -1", "FAIR t", "DELIVERED"])
    assert out[2] == "1"


# --------------------------------------------------------------------------- #
# Corner cases: token bucket
# --------------------------------------------------------------------------- #


def test_meter_partial_tier_and_order():
    out = run(["SUB t 0 1 -1", "SUB t 0 3 -1", "METER t 1", "METER t 1"])
    assert out[2] == "2,1"     # both, priority tie -> id desc
    assert out[3] == "2"       # id1 drained


def test_tokens_unknown_none():
    out = run(["TOKENS 5"])
    assert out[0] == "none"


def test_meter_refill_validation():
    out = run(["METER * 1", "METER t -1", "REFILL * 1", "REFILL t -1"])
    assert out == ["ERR"] * 4


# --------------------------------------------------------------------------- #
# Corner cases: sequence delivery
# --------------------------------------------------------------------------- #


def test_seq_independent_per_topic():
    out = run(["SEQ a 0", "NEXTSEQ a", "NEXTSEQ b"])
    assert out[1] == "1"
    assert out[2] == "0"


def test_seq_validation():
    out = run(["SEQ * 0", "SEQ t -1", "NEXTSEQ *", "PENDING *"])
    assert out == ["ERR"] * 4


# --------------------------------------------------------------------------- #
# Corner cases: ordered thresholds / validation
# --------------------------------------------------------------------------- #


def test_ordered_threshold_zero_ready_immediately():
    out = run(['ORDERED [{"id":"X","topic":"t","deps":["A"],"threshold":0},{"id":"A","topic":"t"}]'])
    assert out[0] == "delivered=X,A undeliverable= count=0"


def test_ordered_threshold_exceeds_deps():
    out = run(['ORDERED [{"id":"A","topic":"t"},{"id":"X","topic":"t","deps":["A"],"threshold":2}]'])
    assert out[0] == "delivered=A undeliverable=X count=0"


def test_ordered_self_dependency():
    out = run(['ORDERED [{"id":"A","topic":"t","deps":["A"]}]'])
    assert out[0] == "delivered= undeliverable=A count=0"


def test_ordered_muted_event_still_delivered():
    out = run(["SUB t 0 1 -1", "MUTE m",
               'ORDERED [{"id":"A","topic":"m","data":"1"},{"id":"B","topic":"t","data":"2","deps":["A"]}]'])
    assert out[2] == "delivered=A,B undeliverable= count=1"


def test_ordered_not_a_list_and_missing_topic():
    out = run(['ORDERED {"id":"A","topic":"t"}', 'ORDERED [{"id":"A"}]'])
    assert out == ["ERR", "ERR"]


# --------------------------------------------------------------------------- #
# Corner cases: submany empty / unknown-topic history
# --------------------------------------------------------------------------- #


def test_submany_empty_is_err():
    out = run(["SUBMANY  0 1 -1"])
    assert out[0] == "ERR"


def test_history_unknown_topic_empty():
    out = run(["HISTORY nope"])
    assert out[0] == ""


# --------------------------------------------------------------------------- #
# Failure handling: max_calls removal across every delivery path
# --------------------------------------------------------------------------- #


def test_maxcalls_removed_via_shard():
    out = run(["SUB t 0 1 1", "SUB t 0 1 1", "SHARD t k 2", "COUNT", "DELIVERED"])
    assert out[3] == "0"       # both exhausted their 1-call budget
    assert out[4] == "2"


def test_maxcalls_removed_via_fair():
    out = run(["SUB t 0 1 1", "FAIR t", "COUNT", "FAIR t"])
    assert out[1] == "1"
    assert out[2] == "0"
    assert out[3] == "none"    # sub gone


def test_maxcalls_removed_via_ring():
    out = run(["SUB t 0 1 1", "RING t k", "COUNT"])
    assert out[1] == "1"
    assert out[2] == "0"


def test_maxcalls_removed_via_meter():
    out = run(["SUB t 0 5 1", "METER t 1", "COUNT", "METER t 1"])
    assert out[1] == "1"
    assert out[2] == "0"
    assert out[3] == ""        # tier empty now


def test_maxcalls_removed_mid_ordered_batch():
    out = run(["SUB t 0 1 1",
               'ORDERED [{"id":"A","topic":"t"},{"id":"B","topic":"t"}]',
               "COUNT"])
    # A delivers (sub removed after its 1 call); B then reaches no subscriber
    assert out[1] == "delivered=A,B undeliverable= count=1"
    assert out[2] == "0"


# --------------------------------------------------------------------------- #
# Failure handling: malformed / boundary arguments
# --------------------------------------------------------------------------- #


def test_malformed_args_are_err():
    out = run([
        "SUB t 0 1",          # missing max_calls
        "SUB t x 1 -1",       # non-int priority
        "SUB t 0 1 -2",       # invalid max_calls sentinel
        "PUB",                # missing topic
        "UNSUB xyz",          # non-int id
        "TOKENS abc",         # non-int id
        "DISTRIBUTE t nan",   # non-int load
        "SEQ t x",            # non-int seq
        "REFILL t x",         # non-int amount
        "METER t x",          # non-int cost
    ])
    assert out == ["ERR"] * 10


def test_negative_priority_ordering():
    out = run(["SUB t -5 1 -1", "SUB t 0 1 -1", "PUB t d"])
    assert out[2] == "2:2,1"   # higher (0) before lower (-5)


def test_pub_missing_data_delivers_empty():
    out = run(["SUB t 0 1 -1", "PUB t"])
    assert out[1] == "1:1"     # data defaults to empty, still delivers


# --------------------------------------------------------------------------- #
# Failure handling: empty-broker / empty-tier edges
# --------------------------------------------------------------------------- #


def test_empty_tier_edges():
    out = run([
        "DISTRIBUTE t 5", "FAIR t", "SHARD t k 2", "RING t k", "METER t 1", "MATCH t",
    ])
    assert out[0] == "overflow=5 alloc="
    assert out[1] == "none"
    assert out[2] == ""
    assert out[3] == "none"
    assert out[4] == ""
    assert out[5] == "0"


def test_ordered_empty_array():
    out = run(["ORDERED []"])
    assert out[0] == "delivered= undeliverable= count=0"


def test_ordered_malformed_json_elements():
    out = run(["ORDERED [1,2]", 'ORDERED [{"id":"A","topic":"t","threshold":"x"}]'])
    assert out == ["ERR", "ERR"]


# --------------------------------------------------------------------------- #
# Failure handling: pause/resume mode + retain edges
# --------------------------------------------------------------------------- #


def test_resume_not_paused_is_zero():
    out = run(["RESUME"])
    assert out[0] == "0"


def test_pause_preserves_psuball_mode_on_resume():
    out = run(["SUB x 0 1 -1", "SUB * 0 1 -1", "PAUSE", "PUBALL x d", "RESUME"])
    assert out[4] == "2"       # fan-out mode preserved -> both tiers delivered


def test_puball_retain():
    out = run(["PUBALL t d R", "RETAINED"])
    assert out[1] == "t"
