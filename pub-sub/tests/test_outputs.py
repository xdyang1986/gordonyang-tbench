"""
Tests for the specificity-routing pub-sub broker task.

Covers the publish pipeline (pause/queue -> filters -> retain -> mute -> route),
specificity routing, (priority DESC, id DESC) ordering, once-successful
subscriptions, live-removal delivery, retained-message replay on subscribe,
mute patterns, pause/resume queueing, filters/interceptors, and the
introspection surface. These semantics deliberately differ from a textbook
fan-out pub-sub.
"""

import sys
import os
import hashlib
import threading
import importlib
import pytest


def _rendezvous_rank(key, ids):
    def score(sid):
        return int.from_bytes(hashlib.sha256(f"{key}:{sid}".encode()).digest()[:8], "big")
    return sorted(ids, key=lambda sid: (score(sid), sid), reverse=True)

sys.path.insert(0, "/app")


def _load_pubsub():
    if "pubsub" in sys.modules:
        importlib.reload(sys.modules["pubsub"])
    import pubsub as m

    assert hasattr(m, "PubSub"), "pubsub.py must contain class PubSub"
    return m.PubSub


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #


def test_file_exists_and_importable():
    assert os.path.exists("/app/pubsub.py")
    assert _load_pubsub() is not None


def test_basic_subscribe_and_publish():
    bus = _load_pubsub()()
    rec = []
    sid = bus.subscribe("order.created", lambda d: rec.append(d))
    assert isinstance(sid, int)
    assert bus.publish("order.created", {"id": 1}) == 1
    assert rec == [{"id": 1}]


def test_callback_receives_data_and_none_default():
    bus = _load_pubsub()()
    got = []
    bus.subscribe("topic", lambda d: got.append(d))
    bus.publish("topic")
    assert got == [None]
    bus.publish("topic", "hello")
    assert got == [None, "hello"]


def test_publish_no_subscribers_returns_zero():
    bus = _load_pubsub()()
    assert bus.publish("nonexistent", 123) == 0


def test_incremental_ids():
    bus = _load_pubsub()()
    assert bus.subscribe("t", lambda d: None) == 1
    assert bus.subscribe("t", lambda d: None) == 2
    assert bus.subscribe("t", lambda d: None) == 3


def test_duplicate_callbacks_counted_separately():
    bus = _load_pubsub()()
    calls = []
    cb = lambda d: calls.append(1)
    bus.subscribe("dup", cb)
    bus.subscribe("dup", cb)
    assert bus.publish("dup", None) == 2
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Specificity routing
# --------------------------------------------------------------------------- #


def test_exact_tier_suppresses_wildcard_and_global():
    bus = _load_pubsub()()
    re_, rw, rg = [], [], []
    bus.subscribe("order.created", lambda d: re_.append(d))
    bus.subscribe("order.*", lambda d: rw.append(d))
    bus.subscribe("*", lambda d: rg.append(d))
    assert bus.publish("order.created", "x") == 1
    assert re_ == ["x"] and rw == [] and rg == []


def test_longest_prefix_tier_wins():
    bus = _load_pubsub()()
    rs, rl, rg = [], [], []
    bus.subscribe("order.*", lambda d: rs.append(d))
    bus.subscribe("order.created.*", lambda d: rl.append(d))
    bus.subscribe("*", lambda d: rg.append(d))
    assert bus.publish("order.created.detail", "deep") == 1
    assert rl == ["deep"] and rs == [] and rg == []
    assert bus.publish("order.foo", "shallow") == 1
    assert rs == ["shallow"] and rl == ["deep"]


def test_global_tier_only_when_nothing_more_specific():
    bus = _load_pubsub()()
    rp, rg = [], []
    bus.subscribe("order.*", lambda d: rp.append(d))
    bus.subscribe("*", lambda d: rg.append(d))
    assert bus.publish("billing.paid", 1) == 1
    assert rg == [1] and rp == []
    assert bus.publish("order.created", 2) == 1
    assert rp == [2] and rg == [1]


def test_prefix_matches_exact_prefix_token():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("order.*", lambda d: rec.append(d))
    assert bus.publish("order", "bare") == 1
    assert rec == ["bare"]
    assert bus.publish("order123", "no") == 0
    assert bus.publish("other.order", "no") == 0
    assert rec == ["bare"]


def test_multiple_subs_in_winning_tier_all_fire():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append("a"))
    bus.subscribe("t", lambda d: rec.append("b"))
    bus.subscribe("*", lambda d: rec.append("glob"))
    assert bus.publish("t", None) == 2
    assert sorted(rec) == ["a", "b"]


def test_get_matching_count_reflects_winning_tier():
    bus = _load_pubsub()()
    bus.subscribe("order.created", lambda d: None)
    bus.subscribe("order.created", lambda d: None)
    bus.subscribe("order.*", lambda d: None)
    bus.subscribe("*", lambda d: None)
    assert bus.get_matching_count("order.created") == 2
    assert bus.get_matching_count("order.shipped") == 1
    assert bus.get_matching_count("unrelated") == 1
    assert _load_pubsub()().get_matching_count("x") == 0


def test_get_matching_count_validates_topic():
    bus = _load_pubsub()()
    for bad in ("*", "", "a.*"):
        with pytest.raises(ValueError):
            bus.get_matching_count(bad)


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #


def test_delivery_order_lifo_within_tier():
    bus = _load_pubsub()()
    order = []
    bus.subscribe("t", lambda d: order.append(1))
    bus.subscribe("t", lambda d: order.append(2))
    bus.subscribe("t", lambda d: order.append(3))
    bus.publish("t", None)
    assert order == [3, 2, 1]


def test_delivery_order_priority_then_lifo():
    bus = _load_pubsub()()
    order = []
    bus.subscribe("t", lambda d: order.append("a"), priority=0)
    bus.subscribe("t", lambda d: order.append("b"), priority=0)
    bus.subscribe("t", lambda d: order.append("c"), priority=5)
    bus.subscribe("t", lambda d: order.append("d"), priority=5)
    bus.publish("t", None)
    assert order == ["d", "c", "b", "a"]


def test_negative_priority_last():
    bus = _load_pubsub()()
    order = []
    bus.subscribe("t", lambda d: order.append("low"), priority=-3)
    bus.subscribe("t", lambda d: order.append("mid"), priority=0)
    bus.subscribe("t", lambda d: order.append("high"), priority=10)
    bus.publish("t", None)
    assert order == ["high", "mid", "low"]


# --------------------------------------------------------------------------- #
# once-successful
# --------------------------------------------------------------------------- #


def test_once_removed_on_success():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe_once("once.topic", lambda d: rec.append(d))
    assert bus.get_subscriber_count() == 1
    assert bus.publish("once.topic", "first") == 1
    assert rec == ["first"]
    assert bus.get_subscriber_count() == 0
    assert bus.publish("once.topic", "second") == 0


def test_once_stays_on_exception_then_removed_on_success():
    bus = _load_pubsub()()
    calls = []

    def cb(d):
        calls.append(d)
        if len(calls) == 1:
            raise RuntimeError("boom")

    bus.subscribe_once("t", cb)
    assert bus.publish("t", "a") == 1
    assert bus.get_subscriber_count() == 1
    assert bus.publish("t", "b") == 1
    assert bus.get_subscriber_count() == 0
    assert bus.publish("t", "c") == 0
    assert calls == ["a", "b"]


def test_once_with_wildcard_routing():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe_once("order.*", lambda d: rec.append(d))
    bus.publish("order.created", "a")
    assert rec == ["a"]
    assert bus.publish("order.created", "b") == 0


# --------------------------------------------------------------------------- #
# Live removal / snapshot additions
# --------------------------------------------------------------------------- #


def test_live_removal_skips_and_does_not_count():
    bus = _load_pubsub()()
    rec = []
    ids = []

    def high(d):
        rec.append("high")
        bus.unsubscribe(ids[0])

    ids.append(bus.subscribe("t", lambda d: rec.append("low"), priority=0))
    ids.append(bus.subscribe("t", high, priority=10))
    assert bus.publish("t", None) == 1
    assert rec == ["high"]


def test_subscribe_during_publish_not_in_current():
    bus = _load_pubsub()()
    rec = []

    def cb(d):
        rec.append("orig")
        bus.subscribe("t", lambda d: rec.append("new"))

    bus.subscribe("t", cb)
    assert bus.publish("t", None) == 1
    assert rec == ["orig"]
    rec.clear()
    assert bus.publish("t", None) == 2
    assert "orig" in rec and "new" in rec


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


def test_exception_does_not_break_others():
    bus = _load_pubsub()()
    rec = []

    def bad(d):
        raise RuntimeError("oops")

    bus.subscribe("t", lambda d: rec.append(1))
    bus.subscribe("t", bad)
    bus.subscribe("t", lambda d: rec.append(3))
    assert bus.publish("t", None) == 3
    assert rec == [3, 1]


def test_exception_counts_and_continues():
    bus = _load_pubsub()()
    bus.subscribe("x", lambda d: (_ for _ in ()).throw(Exception("fail")))
    assert bus.publish("x", None) == 1
    assert bus.publish("x", None) == 1


# --------------------------------------------------------------------------- #
# publish_all
# --------------------------------------------------------------------------- #


def test_publish_all_fans_out():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("order.created", lambda d: rec.append("exact"))
    bus.subscribe("order.*", lambda d: rec.append("prefix"))
    bus.subscribe("*", lambda d: rec.append("glob"))
    assert bus.publish("order.created", None) == 1
    assert rec == ["exact"]
    rec.clear()
    assert bus.publish_all("order.created", None) == 3
    assert set(rec) == {"exact", "prefix", "glob"}


def test_publish_all_ordering():
    bus = _load_pubsub()()
    order = []
    bus.subscribe("a", lambda d: order.append("exact"), priority=0)
    bus.subscribe("*", lambda d: order.append("glob"), priority=0)
    bus.subscribe("a.*", lambda d: order.append("pref"), priority=9)
    assert bus.publish_all("a", None) == 3
    assert order == ["pref", "glob", "exact"]


# --------------------------------------------------------------------------- #
# unsubscribe / clear / counts / topics
# --------------------------------------------------------------------------- #


def test_unsubscribe_and_unknown():
    bus = _load_pubsub()()
    sid = bus.subscribe("t", lambda d: None)
    assert bus.unsubscribe(sid) is True
    assert bus.unsubscribe(sid) is False
    assert bus.unsubscribe(999) is False


def test_unsubscribe_callback():
    bus = _load_pubsub()()
    cb = lambda d: None
    bus.subscribe("a", cb)
    bus.subscribe("b", cb)
    bus.subscribe("c.*", cb)
    bus.subscribe("a", lambda d: None)
    assert bus.unsubscribe_callback(cb) == 3
    assert bus.get_subscriber_count() == 1
    assert bus.unsubscribe_callback(lambda d: None) == 0


def test_clear_all_and_exact():
    bus = _load_pubsub()()
    bus.subscribe("order.created", lambda d: None)
    bus.subscribe("order.created", lambda d: None)
    bus.subscribe("order.*", lambda d: None)
    bus.subscribe("*", lambda d: None)
    bus.clear("order.created")
    assert bus.get_subscriber_count("order.created") == 0
    assert bus.get_subscriber_count() == 2
    bus.clear()
    assert bus.get_subscriber_count() == 0


def test_get_subscriber_count_exact():
    bus = _load_pubsub()()
    assert bus.get_subscriber_count() == 0
    bus.subscribe("a", lambda d: None)
    bus.subscribe("a", lambda d: None)
    bus.subscribe("b", lambda d: None)
    assert bus.get_subscriber_count() == 3
    assert bus.get_subscriber_count("a") == 2
    assert bus.get_subscriber_count("nope") == 0


def test_topics():
    bus = _load_pubsub()()
    assert bus.topics() == set()
    bus.subscribe("a", lambda d: None)
    bus.subscribe("a", lambda d: None)
    bus.subscribe("b.*", lambda d: None)
    bus.subscribe("*", lambda d: None)
    assert bus.topics() == {"a", "b.*", "*"}


# --------------------------------------------------------------------------- #
# Filters / interceptors
# --------------------------------------------------------------------------- #


def test_filter_rewrites_topic():
    bus = _load_pubsub()()
    rec = []
    bus.add_filter(lambda t, d: ("routed", d))
    bus.subscribe("routed", lambda d: rec.append(d))
    bus.subscribe("orig", lambda d: rec.append("WRONG"))
    assert bus.publish("orig", 5) == 1
    assert rec == [5]


def test_filter_none_drops():
    bus = _load_pubsub()()
    rec = []
    fid = bus.add_filter(lambda t, d: None)
    bus.subscribe("t", lambda d: rec.append(d))
    assert bus.publish("t", 1) == 0
    assert rec == []
    assert bus.remove_filter(fid) is True
    assert bus.publish("t", 2) == 1
    assert rec == [2]


def test_filter_chain_ascending_id():
    bus = _load_pubsub()()
    order = []
    bus.add_filter(lambda t, d: (t, d + ["f1"]))
    bus.add_filter(lambda t, d: (t, d + ["f2"]))
    bus.subscribe("t", lambda d: order.extend(d))
    bus.publish("t", [])
    assert order == ["f1", "f2"]


def test_filter_invalid_topic_raises():
    bus = _load_pubsub()()
    bus.add_filter(lambda t, d: ("bad.*", d))
    with pytest.raises(ValueError):
        bus.publish("t", 1)


def test_filter_applies_to_publish_all():
    bus = _load_pubsub()()
    rec = []
    bus.add_filter(lambda t, d: ("x", d))
    bus.subscribe("x", lambda d: rec.append(d))
    assert bus.publish_all("y", 1) == 1
    assert rec == [1]


def test_add_filter_validates_callable():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.add_filter("not callable")


def test_remove_filter_unknown():
    bus = _load_pubsub()()
    assert bus.remove_filter(123) is False


# --------------------------------------------------------------------------- #
# Mute
# --------------------------------------------------------------------------- #


def test_mute_exact():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d))
    bus.mute("t")
    assert bus.publish("t", 1) == 0
    assert rec == []
    assert bus.unmute("t") is True
    assert bus.publish("t", 2) == 1


def test_mute_prefix_pattern():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("order.created", lambda d: rec.append(d))
    bus.mute("order.*")
    assert bus.publish("order.created", 1) == 0
    assert rec == []


def test_mute_global():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("anything", lambda d: rec.append(d))
    bus.mute("*")
    assert bus.publish("anything", 1) == 0


def test_mute_still_retains():
    bus = _load_pubsub()()
    bus.mute("t")
    assert bus.publish("t", 99, retain=True) == 0
    assert "t" in bus.retained_topics()


def test_mute_validation_and_unmute_unknown():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.mute("a*b")
    assert bus.unmute("never") is False


def test_muted_patterns():
    bus = _load_pubsub()()
    bus.mute("a")
    bus.mute("b.*")
    assert bus.muted_patterns() == {"a", "b.*"}


# --------------------------------------------------------------------------- #
# Pause / resume
# --------------------------------------------------------------------------- #


def test_pause_blocks_and_resume_replays():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d))
    bus.pause()
    assert bus.is_paused() is True
    assert bus.publish("t", 1) == 0
    assert rec == []
    assert bus.resume() == 1
    assert rec == [1]
    assert bus.is_paused() is False


def test_resume_fifo_and_aggregate_with_late_subscribers():
    bus = _load_pubsub()()
    rec = []
    bus.pause()
    bus.publish("a", 1)
    bus.subscribe("a", lambda d: rec.append(("a", d)))
    bus.publish("b", 2)
    bus.subscribe("b", lambda d: rec.append(("b", d)))
    assert bus.resume() == 2
    assert rec == [("a", 1), ("b", 2)]


def test_pause_defers_retain():
    bus = _load_pubsub()()
    bus.pause()
    bus.publish("t", 5, retain=True)
    assert bus.retained_topics() == set()
    bus.resume()
    assert "t" in bus.retained_topics()


# --------------------------------------------------------------------------- #
# Retained messages / replay on subscribe
# --------------------------------------------------------------------------- #


def test_retained_replayed_on_subscribe():
    bus = _load_pubsub()()
    rec = []
    bus.publish("t", "v", retain=True)
    bus.subscribe("t", lambda d: rec.append(d))
    assert rec == ["v"]


def test_retained_replayed_to_wildcard_in_insertion_order():
    bus = _load_pubsub()()
    rec = []
    bus.publish("a", 1, retain=True)
    bus.publish("b", 2, retain=True)
    bus.subscribe("*", lambda d: rec.append(d))
    assert rec == [1, 2]


def test_retained_replayed_to_prefix_match_only():
    bus = _load_pubsub()()
    rec = []
    bus.publish("order.created", 1, retain=True)
    bus.publish("user.x", 2, retain=True)
    bus.subscribe("order.*", lambda d: rec.append(d))
    assert rec == [1]


def test_retain_keeps_latest_value():
    bus = _load_pubsub()()
    rec = []
    bus.publish("t", 1, retain=True)
    bus.publish("t", 2, retain=True)
    bus.subscribe("t", lambda d: rec.append(d))
    assert rec == [2]


def test_once_consumes_retained_and_is_removed():
    bus = _load_pubsub()()
    rec = []
    bus.publish("t", "v", retain=True)
    bus.subscribe_once("t", lambda d: rec.append(d))
    assert rec == ["v"]
    assert bus.get_subscriber_count() == 0


def test_once_retained_raise_keeps_subscription():
    bus = _load_pubsub()()
    bus.publish("t", "v", retain=True)

    def bad(d):
        raise RuntimeError("boom")

    bus.subscribe_once("t", bad)
    assert bus.get_subscriber_count() == 1


def test_non_retained_publish_does_not_store():
    bus = _load_pubsub()()
    bus.publish("t", 1)
    assert bus.retained_topics() == set()


def test_clear_retained():
    bus = _load_pubsub()()
    bus.publish("a", 1, retain=True)
    bus.publish("b", 2, retain=True)
    assert bus.retained_topics() == {"a", "b"}
    bus.clear_retained("a")
    assert bus.retained_topics() == {"b"}
    bus.clear_retained()
    assert bus.retained_topics() == set()


# --------------------------------------------------------------------------- #
# delivered_count
# --------------------------------------------------------------------------- #


def test_delivered_count_tracks_all_deliveries():
    bus = _load_pubsub()()
    bus.subscribe("t", lambda d: None)
    bus.subscribe("t", lambda d: None)
    assert bus.publish("t", 1) == 2
    assert bus.delivered_count() == 2
    assert bus.publish_all("t", 1) == 2
    assert bus.delivered_count() == 4


def test_delivered_count_includes_retained_replay():
    bus = _load_pubsub()()
    bus.publish("t", "v", retain=True)
    bus.subscribe("t", lambda d: None)
    assert bus.delivered_count() == 1


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_subscribe_validation_topic():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.subscribe("", lambda d: None)
    with pytest.raises(ValueError):
        bus.subscribe(None, lambda d: None)


def test_subscribe_validation_callback():
    bus = _load_pubsub()()
    for bad in (None, "nope", 123):
        with pytest.raises(ValueError):
            bus.subscribe("t", bad)


def test_subscribe_validation_wildcard_rules():
    bus = _load_pubsub()()
    bus.subscribe("*", lambda d: None)
    bus.subscribe("order.*", lambda d: None)
    bus.subscribe("order.created", lambda d: None)
    for t in ["ord*er", "*.created", "order.*.created", "order.*.",
              "order*", "*.*", "a*b.*", "order.**"]:
        with pytest.raises(ValueError):
            bus.subscribe(t, lambda d: None)


def test_subscribe_validation_priority():
    bus = _load_pubsub()()
    for bad in ("high", 1.5, True):
        with pytest.raises(ValueError):
            bus.subscribe("t", lambda d: None, priority=bad)
    assert isinstance(bus.subscribe("t", lambda d: None, priority=-2), int)


def test_publish_validation():
    bus = _load_pubsub()()
    for bad in ("", "topic.with.*", "*", "bad*topic"):
        with pytest.raises(ValueError):
            bus.publish(bad, None)
    for bad in ("", "*", "a.*"):
        with pytest.raises(ValueError):
            bus.publish_all(bad, None)


# --------------------------------------------------------------------------- #
# Corner cases (pipeline order, routing edges, once/retained interplay)
# --------------------------------------------------------------------------- #


def test_exact_wins_over_prefix_equal_to_topic():
    bus = _load_pubsub()()
    re_, rp = [], []
    bus.subscribe("order", lambda d: re_.append(d))
    bus.subscribe("order.*", lambda d: rp.append(d))
    # "order.*" matches the topic "order", but the exact tier still wins
    assert bus.publish("order", 1) == 1
    assert re_ == [1] and rp == []


def test_filter_reroutes_into_prefix_tier():
    bus = _load_pubsub()()
    rec = []
    bus.add_filter(lambda t, d: ("order.created", d))
    bus.subscribe("order.*", lambda d: rec.append(d))
    assert bus.publish("x", 7) == 1
    assert rec == [7]


def test_retain_stores_post_filter_topic_and_data():
    bus = _load_pubsub()()
    rec = []
    bus.add_filter(lambda t, d: ("b", d * 10))
    assert bus.publish("a", 5, retain=True) == 0  # no subscriber for "b"
    assert bus.retained_topics() == {"b"}
    bus.subscribe("b", lambda d: rec.append(d))
    assert rec == [50]


def test_mute_checked_on_post_filter_topic():
    bus = _load_pubsub()()
    rec = []
    bus.add_filter(lambda t, d: ("muted", d))
    bus.mute("muted")
    bus.subscribe("muted", lambda d: rec.append(d))
    assert bus.publish("a", 1) == 0
    assert rec == []


def test_mute_not_triggered_when_filter_rewrites_away():
    bus = _load_pubsub()()
    rec = []
    bus.mute("a")
    bus.add_filter(lambda t, d: ("clean", d))
    bus.subscribe("clean", lambda d: rec.append(d))
    assert bus.publish("a", 1) == 1
    assert rec == [1]


def test_resume_preserves_publish_all_mode():
    bus = _load_pubsub()()
    bus.subscribe("x", lambda d: None)
    bus.subscribe("*", lambda d: None)
    bus.pause()
    bus.publish_all("x", None)
    assert bus.resume() == 2  # fan-out mode preserved across the queue


def test_once_removed_via_publish_all():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe_once("t", lambda d: rec.append(d))
    assert bus.publish_all("t", 1) == 1
    assert bus.get_subscriber_count() == 0
    assert bus.publish_all("t", 2) == 0
    assert rec == [1]


def test_once_consumes_only_first_matching_retained():
    bus = _load_pubsub()()
    rec = []
    bus.publish("a", 1, retain=True)
    bus.publish("b", 2, retain=True)
    bus.subscribe_once("*", lambda d: rec.append(d))
    assert rec == [1]
    assert bus.get_subscriber_count() == 0


def test_delivered_count_excludes_skipped():
    bus = _load_pubsub()()
    ids = []

    def high(d):
        bus.unsubscribe(ids[0])

    ids.append(bus.subscribe("t", lambda d: None, priority=0))
    ids.append(bus.subscribe("t", high, priority=10))
    assert bus.publish("t", None) == 1
    assert bus.delivered_count() == 1


# --------------------------------------------------------------------------- #
# Thread-safety & reentrancy
# --------------------------------------------------------------------------- #


def test_thread_safety_concurrent_publish():
    bus = _load_pubsub()()
    counter = []
    lock = threading.Lock()

    def cb(d):
        with lock:
            counter.append(1)

    for _ in range(10):
        bus.subscribe("concurrent", cb)

    def publisher():
        for _ in range(50):
            bus.publish("concurrent", None)

    threads = [threading.Thread(target=publisher) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(counter) == 2500


def test_thread_safety_concurrent_mixed():
    bus = _load_pubsub()()
    errors = []

    def sub_thread():
        try:
            for i in range(100):
                bus.subscribe(f"topic.{i % 5}", lambda d: None)
        except Exception as e:
            errors.append(e)

    def pub_thread():
        try:
            for _ in range(100):
                bus.publish("topic.1", None)
                bus.publish_all("topic.2", None)
        except Exception as e:
            errors.append(e)

    def unsub_thread():
        try:
            ids = [bus.subscribe("topic.1", lambda d: None) for _ in range(20)]
            for sid in ids:
                bus.unsubscribe(sid)
        except Exception as e:
            errors.append(e)

    threads = []
    for _ in range(3):
        threads.append(threading.Thread(target=sub_thread))
        threads.append(threading.Thread(target=pub_thread))
        threads.append(threading.Thread(target=unsub_thread))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"errors: {errors}"
    assert isinstance(bus.get_subscriber_count(), int)


def test_reentrancy_publish_inside_callback():
    bus = _load_pubsub()()
    rec = []

    def outer(d):
        rec.append("outer")
        bus.publish("inner", "data")

    bus.subscribe("outer", outer)
    bus.subscribe("inner", lambda d: rec.append("inner"))
    bus.publish("outer", None)
    assert "outer" in rec and "inner" in rec


def test_reentrancy_subscribe_inside_callback():
    bus = _load_pubsub()()
    rec = []

    def cb(d):
        rec.append("first")
        bus.subscribe("t", lambda d: rec.append("added"))

    bus.subscribe("t", cb)
    bus.publish("t", None)
    assert rec == ["first"]
    rec.clear()
    bus.publish("t", None)
    assert "first" in rec and "added" in rec


# --------------------------------------------------------------------------- #
# k-of-n dependency-ordered delivery (publish_ordered)
# --------------------------------------------------------------------------- #


def test_ordered_cascade_reversed_chain():
    bus = _load_pubsub()()
    events = [
        {"id": "C", "topic": "t", "deps": ["B"]},
        {"id": "B", "topic": "t", "deps": ["A"]},
        {"id": "A", "topic": "t", "deps": []},
    ]
    res = bus.publish_ordered(events)
    assert res["delivered"] == ["A", "B", "C"]
    assert res["undeliverable"] == []


def test_ordered_priority_tiebreak():
    bus = _load_pubsub()()
    events = [
        {"id": "A", "topic": "t", "priority": 1},
        {"id": "B", "topic": "t", "priority": 5},
        {"id": "C", "topic": "t", "priority": 3},
    ]
    res = bus.publish_ordered(events)
    # all deliverable at once -> priority DESC, then arrival
    assert res["delivered"] == ["B", "C", "A"]


def test_ordered_k_of_n_threshold():
    bus = _load_pubsub()()
    events = [
        {"id": "D1", "topic": "t", "deps": []},
        {"id": "D2", "topic": "t", "deps": []},
        {"id": "X", "topic": "t", "deps": ["D1", "D2", "D3"], "threshold": 2},
        {"id": "D3", "topic": "t", "deps": []},
    ]
    res = bus.publish_ordered(events)
    # X needs only 2 of its 3 deps; it fires after D1,D2 (before D3, since X's
    # arrival index precedes D3's)
    assert res["delivered"] == ["D1", "D2", "X", "D3"]
    assert res["undeliverable"] == []


def test_ordered_missing_dep_undeliverable():
    bus = _load_pubsub()()
    events = [
        {"id": "X", "topic": "t", "deps": ["ghost"]},
        {"id": "Y", "topic": "t", "deps": ["X"]},
        {"id": "Z", "topic": "t", "deps": []},
    ]
    res = bus.publish_ordered(events)
    assert res["delivered"] == ["Z"]
    assert res["undeliverable"] == ["X", "Y"]


def test_ordered_cycle_undeliverable():
    bus = _load_pubsub()()
    events = [
        {"id": "A", "topic": "t", "deps": ["B"]},
        {"id": "B", "topic": "t", "deps": ["A"]},
    ]
    res = bus.publish_ordered(events)
    assert res["delivered"] == []
    assert res["undeliverable"] == ["A", "B"]


def test_ordered_integration_fires_in_order_and_counts():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("*", lambda d: rec.append(d))
    events = [
        {"id": "C", "topic": "t", "data": "c", "deps": ["B"]},
        {"id": "B", "topic": "t", "data": "b", "deps": ["A"]},
        {"id": "A", "topic": "t", "data": "a", "deps": []},
    ]
    res = bus.publish_ordered(events)
    assert res["delivered"] == ["A", "B", "C"]
    assert res["count"] == 3
    assert rec == ["a", "b", "c"]


def test_ordered_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.publish_ordered("not a list")
    with pytest.raises(ValueError):
        bus.publish_ordered([{"id": "A"}])  # missing topic
    with pytest.raises(ValueError):
        bus.publish_ordered([{"id": "A", "topic": "bad*topic"}])


# --------------------------------------------------------------------------- #
# max_calls (generalized auto-remove)
# --------------------------------------------------------------------------- #


def test_max_calls_removes_after_n_successful():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d), max_calls=2)
    assert bus.publish("t", 1) == 1
    assert bus.publish("t", 2) == 1
    assert bus.publish("t", 3) == 0
    assert rec == [1, 2]
    assert bus.get_subscriber_count() == 0


def test_max_calls_raise_does_not_count():
    bus = _load_pubsub()()
    calls = []

    def cb(d):
        calls.append(d)
        if len(calls) == 1:
            raise RuntimeError("boom")

    bus.subscribe("t", cb, max_calls=1)
    assert bus.publish("t", "a") == 1
    assert bus.get_subscriber_count() == 1  # raised -> not counted
    assert bus.publish("t", "b") == 1
    assert bus.get_subscriber_count() == 0
    assert calls == ["a", "b"]


def test_subscribe_once_is_max_calls_one():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe_once("t", lambda d: rec.append(d))
    bus.publish("t", 1)
    assert bus.publish("t", 2) == 0
    assert rec == [1]


def test_max_calls_validation():
    bus = _load_pubsub()()
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            bus.subscribe("t", lambda d: None, max_calls=bad)
    assert isinstance(bus.subscribe("t", lambda d: None, max_calls=None), int)
    assert isinstance(bus.subscribe("t", lambda d: None, max_calls=3), int)


# --------------------------------------------------------------------------- #
# transform
# --------------------------------------------------------------------------- #


def test_transform_applied_to_callback():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d), transform=lambda d: d * 2)
    bus.publish("t", 5)
    assert rec == [10]


def test_transform_applied_to_retained_replay():
    bus = _load_pubsub()()
    rec = []
    bus.publish("t", 5, retain=True)
    bus.subscribe("t", lambda d: rec.append(d), transform=lambda d: d + 100)
    assert rec == [105]


def test_transform_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.subscribe("t", lambda d: None, transform="nope")


# --------------------------------------------------------------------------- #
# error handler
# --------------------------------------------------------------------------- #


def test_error_handler_called_on_exception():
    bus = _load_pubsub()()
    seen = []
    bus.set_error_handler(lambda exc, sid, data: seen.append((type(exc), sid, data)))
    rec = []

    def bad(d):
        raise ValueError("x")

    sid = bus.subscribe("t", bad)
    bus.subscribe("t", lambda d: rec.append(d))
    assert bus.publish("t", 7) == 2
    assert rec == [7]  # other callback still ran
    assert seen == [(ValueError, sid, 7)]


def test_error_handler_raising_is_swallowed():
    bus = _load_pubsub()()
    rec = []
    bus.set_error_handler(lambda exc, sid, data: (_ for _ in ()).throw(Exception("h")))
    bus.subscribe("t", lambda d: (_ for _ in ()).throw(Exception("cb")))
    bus.subscribe("t", lambda d: rec.append(d))
    assert bus.publish("t", 1) == 2
    assert rec == [1]


def test_error_handler_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.set_error_handler("nope")
    bus.set_error_handler(None)  # ok


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #


def test_history_records_in_order():
    bus = _load_pubsub()()
    bus.publish("t", 1)
    bus.publish("t", 2)
    bus.publish("other", 9)
    assert bus.get_history("t") == [1, 2]
    assert bus.get_history("other") == [9]
    assert bus.get_history("none") == []


def test_history_not_recorded_when_muted_but_retained_is():
    bus = _load_pubsub()()
    bus.mute("t")
    bus.publish("t", 5, retain=True)
    assert bus.get_history("t") == []
    assert "t" in bus.retained_topics()


def test_history_deferred_when_paused():
    bus = _load_pubsub()()
    bus.pause()
    bus.publish("t", 1)
    assert bus.get_history("t") == []
    bus.resume()
    assert bus.get_history("t") == [1]


def test_history_records_post_filter_topic():
    bus = _load_pubsub()()
    bus.add_filter(lambda t, d: ("b", d))
    bus.publish("a", 3)
    assert bus.get_history("b") == [3]
    assert bus.get_history("a") == []


def test_clear_history():
    bus = _load_pubsub()()
    bus.publish("a", 1)
    bus.publish("b", 2)
    bus.clear_history("a")
    assert bus.get_history("a") == []
    assert bus.get_history("b") == [2]
    bus.clear_history()
    assert bus.get_history("b") == []


def test_get_history_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.get_history("*")


# --------------------------------------------------------------------------- #
# batch ops
# --------------------------------------------------------------------------- #


def test_subscribe_many():
    bus = _load_pubsub()()
    rec = []
    ids = bus.subscribe_many(["a", "b.*"], lambda d: rec.append(d))
    assert len(ids) == 2 and all(isinstance(i, int) for i in ids)
    bus.publish("a", 1)
    bus.publish("b.x", 2)
    assert rec == [1, 2]


def test_subscribe_many_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.subscribe_many("not a list", lambda d: None)


def test_publish_batch_tuples():
    bus = _load_pubsub()()
    bus.subscribe("a", lambda d: None)
    bus.subscribe("b", lambda d: None)
    bus.subscribe("b", lambda d: None)
    assert bus.publish_batch([("a", 1), ("b", 2)]) == [1, 2]


def test_publish_batch_dicts_with_retain():
    bus = _load_pubsub()()
    assert bus.publish_batch([{"topic": "t", "data": 9, "retain": True}]) == [0]
    assert "t" in bus.retained_topics()


def test_publish_batch_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.publish_batch("nope")


# --------------------------------------------------------------------------- #
# capacity-weighted distribution (integer water-filling)
# --------------------------------------------------------------------------- #


def test_distribute_even_split():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=10)
    b = bus.subscribe("t", lambda d: None, capacity=10)
    c = bus.subscribe("t", lambda d: None, capacity=10)
    res = bus.distribute("t", 9)
    assert res["allocations"] == {a: 3, b: 3, c: 3}
    assert res["overflow"] == 0


def test_distribute_remainder_to_smallest_ids():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=10)
    b = bus.subscribe("t", lambda d: None, capacity=10)
    c = bus.subscribe("t", lambda d: None, capacity=10)
    res = bus.distribute("t", 10)
    # 3 each, remaining 1 goes to the smallest id
    assert res["allocations"] == {a: 4, b: 3, c: 3}
    assert res["overflow"] == 0


def test_distribute_saturation_cascade():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=1)
    b = bus.subscribe("t", lambda d: None, capacity=10)
    c = bus.subscribe("t", lambda d: None, capacity=10)
    # a saturates at 1; its excess cascades to b and c
    res = bus.distribute("t", 12)
    assert res["allocations"] == {a: 1, b: 6, c: 5}
    assert res["overflow"] == 0
    assert sum(res["allocations"].values()) == 12


def test_distribute_overflow_when_load_exceeds_capacity():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=2)
    b = bus.subscribe("t", lambda d: None, capacity=3)
    res = bus.distribute("t", 10)
    assert res["allocations"] == {a: 2, b: 3}
    assert res["overflow"] == 5


def test_distribute_zero_capacity_excluded():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=0)
    b = bus.subscribe("t", lambda d: None, capacity=5)
    res = bus.distribute("t", 3)
    assert res["allocations"] == {a: 0, b: 3}
    assert res["overflow"] == 0


def test_distribute_uses_specificity_tier():
    bus = _load_pubsub()()
    exact = bus.subscribe("t", lambda d: None, capacity=5)
    bus.subscribe("*", lambda d: None, capacity=5)  # different tier, excluded
    res = bus.distribute("t", 4)
    assert res["allocations"] == {exact: 4}
    assert res["overflow"] == 0


def test_distribute_no_recipients_all_overflow():
    bus = _load_pubsub()()
    res = bus.distribute("none", 5)
    assert res["allocations"] == {}
    assert res["overflow"] == 5


def test_distribute_validation():
    bus = _load_pubsub()()
    for bad in (-1, True, 1.5, "3"):
        with pytest.raises(ValueError):
            bus.distribute("t", bad)
    with pytest.raises(ValueError):
        bus.distribute("*", 1)


def test_capacity_validation():
    bus = _load_pubsub()()
    for bad in (-1, True, 1.5):
        with pytest.raises(ValueError):
            bus.subscribe("t", lambda d: None, capacity=bad)
    assert isinstance(bus.subscribe("t", lambda d: None, capacity=0), int)


# --------------------------------------------------------------------------- #
# Corner cases: clarified ambiguities (group A)
# --------------------------------------------------------------------------- #


def test_filter_malformed_return_raises():
    bus = _load_pubsub()()
    bus.add_filter(lambda t, d: "not a pair")
    bus.subscribe("t", lambda d: None)
    with pytest.raises(ValueError):
        bus.publish("t", 1)


def test_publish_ordered_duplicate_id_raises():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.publish_ordered([
            {"id": "A", "topic": "t"},
            {"id": "A", "topic": "t"},
        ])


def test_subscribe_many_is_atomic_on_invalid_pattern():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.subscribe_many(["a", "bad*pattern", "b"], lambda d: None)
    assert bus.get_subscriber_count() == 0  # nothing registered


def test_resume_when_not_paused_is_noop():
    bus = _load_pubsub()()
    assert bus.resume() == 0


# --------------------------------------------------------------------------- #
# Corner cases: publish_ordered thresholds (group B)
# --------------------------------------------------------------------------- #


def test_ordered_threshold_zero_ready_immediately():
    bus = _load_pubsub()()
    res = bus.publish_ordered([
        {"id": "X", "topic": "t", "deps": ["A"], "threshold": 0},
        {"id": "A", "topic": "t"},
    ])
    # X needs 0 of its deps -> ready immediately, delivered before A (arrival order)
    assert res["delivered"] == ["X", "A"]
    assert res["undeliverable"] == []


def test_ordered_threshold_exceeds_deps_undeliverable():
    bus = _load_pubsub()()
    res = bus.publish_ordered([
        {"id": "A", "topic": "t"},
        {"id": "X", "topic": "t", "deps": ["A"], "threshold": 2},
    ])
    assert res["delivered"] == ["A"]
    assert res["undeliverable"] == ["X"]


def test_ordered_self_dependency_undeliverable():
    bus = _load_pubsub()()
    res = bus.publish_ordered([{"id": "A", "topic": "t", "deps": ["A"]}])
    assert res["delivered"] == []
    assert res["undeliverable"] == ["A"]


# --------------------------------------------------------------------------- #
# Corner cases: distribute boundaries (group B)
# --------------------------------------------------------------------------- #


def test_distribute_load_zero():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=5)
    res = bus.distribute("t", 0)
    assert res["allocations"] == {a: 0}
    assert res["overflow"] == 0


def test_distribute_load_equals_total_capacity():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=2)
    b = bus.subscribe("t", lambda d: None, capacity=3)
    res = bus.distribute("t", 5)
    assert res["allocations"] == {a: 2, b: 3}
    assert res["overflow"] == 0


def test_distribute_all_zero_capacity_is_all_overflow():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=0)
    b = bus.subscribe("t", lambda d: None, capacity=0)
    res = bus.distribute("t", 3)
    assert res["allocations"] == {a: 0, b: 0}
    assert res["overflow"] == 3


def test_distribute_deep_cascade():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=1)
    b = bus.subscribe("t", lambda d: None, capacity=1)
    c = bus.subscribe("t", lambda d: None, capacity=1)
    d = bus.subscribe("t", lambda d: None, capacity=100)
    res = bus.distribute("t", 10)
    assert res["allocations"] == {a: 1, b: 1, c: 1, d: 7}
    assert res["overflow"] == 0


def test_distribute_over_prefix_tier():
    bus = _load_pubsub()()
    p = bus.subscribe("order.*", lambda d: None, capacity=5)
    res = bus.distribute("order.created", 3)
    assert res["allocations"] == {p: 3}
    assert res["overflow"] == 0


# --------------------------------------------------------------------------- #
# Corner cases: max_calls / delivered_count accounting (group B)
# --------------------------------------------------------------------------- #


def test_max_calls_transform_raise_not_counted():
    bus = _load_pubsub()()

    def boom(d):
        raise ValueError("x")

    bus.subscribe("t", lambda d: None, max_calls=2, transform=boom)
    bus.publish("t", 1)
    bus.publish("t", 2)
    # transform always raises -> never counts toward max_calls -> still subscribed
    assert bus.get_subscriber_count() == 1
    assert bus.delivered_count() == 2  # both invocations count as delivered


def test_delivered_count_excludes_muted_and_paused_includes_raised():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d))
    bus.publish("t", 1)
    assert bus.delivered_count() == 1
    bus.mute("t")
    bus.publish("t", 2)
    assert bus.delivered_count() == 1  # muted -> not counted
    bus.unmute("t")
    bus.pause()
    bus.publish("t", 3)
    assert bus.delivered_count() == 1  # paused -> queued, not counted
    assert bus.resume() == 1
    assert bus.delivered_count() == 2  # replayed now counts

    def boom(d):
        raise RuntimeError("x")

    bus.subscribe("t", boom)
    bus.publish("t", 4)
    assert bus.delivered_count() == 4  # good + raising callback both invoked
    assert rec == [1, 3, 4]


# --------------------------------------------------------------------------- #
# Corner cases: cross-feature interactions (group C)
# --------------------------------------------------------------------------- #


def test_ordered_muted_event_still_delivered_zero_count():
    bus = _load_pubsub()()
    rec = []
    bus.mute("m")
    bus.subscribe("t", lambda d: rec.append(d))
    res = bus.publish_ordered([
        {"id": "A", "topic": "m", "data": 1},
        {"id": "B", "topic": "t", "data": 2, "deps": ["A"]},
    ])
    # A is dispatched (unblocks B) but muted -> 0 callbacks; B delivers to the sub
    assert res["delivered"] == ["A", "B"]
    assert res["undeliverable"] == []
    assert res["count"] == 1
    assert rec == [2]


def test_resume_applies_filter_added_during_pause():
    bus = _load_pubsub()()
    rec = []
    bus.pause()
    bus.publish("a", 1)
    bus.add_filter(lambda t, d: ("b", d))
    bus.subscribe("b", lambda d: rec.append(d))
    assert bus.resume() == 1  # replay applies the now-registered filter -> routes to "b"
    assert rec == [1]


def test_publish_batch_while_paused_then_resume():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d))
    bus.pause()
    assert bus.publish_batch([("t", 1), ("t", 2)]) == [0, 0]
    assert rec == []
    assert bus.resume() == 2
    assert rec == [1, 2]


def test_retained_replay_max_calls_exhausts_midway():
    bus = _load_pubsub()()
    rec = []
    bus.publish("a", 1, retain=True)
    bus.publish("b", 2, retain=True)
    bus.subscribe("*", lambda d: rec.append(d), max_calls=1)
    # matches both retained topics (insertion order); budget exhausts after "a"
    assert rec == [1]
    assert bus.get_subscriber_count() == 0


# --------------------------------------------------------------------------- #
# rendezvous-hash sharded delivery (publish_sharded)
# --------------------------------------------------------------------------- #


def test_sharded_selects_top_n_by_rendezvous():
    bus = _load_pubsub()()
    ids = [bus.subscribe("t", lambda d: None) for _ in range(6)]
    key = "user-42"
    expected = _rendezvous_rank(key, ids)[:3]
    before = bus.delivered_count()
    chosen = bus.publish_sharded("t", key, "payload", n=3)
    assert chosen == expected
    assert bus.delivered_count() - before == 3


def test_sharded_is_deterministic_for_same_key():
    bus = _load_pubsub()()
    for _ in range(5):
        bus.subscribe("t", lambda d: None)
    a = bus.publish_sharded("t", "k", None, n=2)
    b = bus.publish_sharded("t", "k", None, n=2)
    assert a == b


def test_sharded_n_zero_delivers_nothing():
    bus = _load_pubsub()()
    bus.subscribe("t", lambda d: None)
    before = bus.delivered_count()
    assert bus.publish_sharded("t", "k", None, n=0) == []
    assert bus.delivered_count() == before


def test_sharded_n_exceeds_recipients():
    bus = _load_pubsub()()
    ids = [bus.subscribe("t", lambda d: None) for _ in range(3)]
    chosen = bus.publish_sharded("t", "k", None, n=10)
    assert sorted(chosen) == sorted(ids)
    assert chosen == _rendezvous_rank("k", ids)


def test_sharded_uses_specificity_tier():
    bus = _load_pubsub()()
    exact = [bus.subscribe("t", lambda d: None) for _ in range(3)]
    bus.subscribe("*", lambda d: None)  # different tier, excluded
    chosen = bus.publish_sharded("t", "k", None, n=2)
    assert set(chosen).issubset(set(exact))
    assert chosen == _rendezvous_rank("k", exact)[:2]


def test_sharded_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.publish_sharded("*", "k", None, n=1)
    with pytest.raises(ValueError):
        bus.publish_sharded("t", "", None, n=1)
    for bad in (-1, True):
        with pytest.raises(ValueError):
            bus.publish_sharded("t", "k", None, n=bad)


# --------------------------------------------------------------------------- #
# weighted fair scheduling (publish_fair / stride scheduling)
# --------------------------------------------------------------------------- #


def test_fair_weighted_selection_sequence():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=1)
    b = bus.subscribe("t", lambda d: None, capacity=2)
    picks = [bus.publish_fair("t", None) for _ in range(6)]
    # stride scheduling by capacity 1:2 -> b picked twice as often; ties to smaller id
    assert picks == [a, b, b, a, b, b]


def test_fair_single_recipient_always_selected():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=3)
    assert [bus.publish_fair("t", None) for _ in range(4)] == [a, a, a, a]


def test_fair_no_eligible_returns_none():
    bus = _load_pubsub()()
    assert bus.publish_fair("t", None) is None
    bus.subscribe("t", lambda d: None, capacity=0)
    assert bus.publish_fair("t", None) is None


def test_fair_excludes_zero_capacity():
    bus = _load_pubsub()()
    bus.subscribe("t", lambda d: None, capacity=0)
    b = bus.subscribe("t", lambda d: None, capacity=1)
    assert [bus.publish_fair("t", None) for _ in range(3)] == [b, b, b]


def test_fair_delivers_and_counts():
    bus = _load_pubsub()()
    rec = []
    bus.subscribe("t", lambda d: rec.append(d), capacity=1)
    before = bus.delivered_count()
    bus.publish_fair("t", "x")
    assert rec == ["x"]
    assert bus.delivered_count() - before == 1


def test_fair_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.publish_fair("*", None)


# --------------------------------------------------------------------------- #
# token-bucket rate limiting (publish_metered / refill / get_tokens)
# --------------------------------------------------------------------------- #


def test_metered_initial_tokens_equal_capacity():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=3)
    assert bus.get_tokens(a) == 3
    assert bus.get_tokens(9999) is None


def test_metered_delivers_until_empty():
    bus = _load_pubsub()()
    rec = []
    a = bus.subscribe("t", lambda d: rec.append(d), capacity=2)
    assert bus.publish_metered("t", "x") == [a]
    assert bus.publish_metered("t", "y") == [a]
    assert bus.get_tokens(a) == 0
    assert bus.publish_metered("t", "z") == []  # bucket empty -> skipped
    assert rec == ["x", "y"]


def test_metered_refill_caps_at_capacity():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=2)
    bus.publish_metered("t", None)
    bus.publish_metered("t", None)
    assert bus.get_tokens(a) == 0
    bus.refill("t", 5)  # capped at capacity=2
    assert bus.get_tokens(a) == 2
    assert bus.publish_metered("t", None) == [a]


def test_metered_cost_greater_than_one():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=3)
    assert bus.publish_metered("t", None, cost=2) == [a]
    assert bus.get_tokens(a) == 1
    assert bus.publish_metered("t", None, cost=2) == []  # only 1 token left
    assert bus.get_tokens(a) == 1


def test_metered_partial_tier():
    bus = _load_pubsub()()
    a = bus.subscribe("t", lambda d: None, capacity=1)
    b = bus.subscribe("t", lambda d: None, capacity=3)
    # first metered: both have tokens -> both (order: same priority, id DESC -> b, a)
    assert bus.publish_metered("t", None) == [b, a]
    # a now empty; second metered: only b
    assert bus.publish_metered("t", None) == [b]


def test_metered_validation():
    bus = _load_pubsub()()
    with pytest.raises(ValueError):
        bus.publish_metered("*", None)
    for bad in (-1, True):
        with pytest.raises(ValueError):
            bus.publish_metered("t", None, cost=bad)
    with pytest.raises(ValueError):
        bus.refill("*", 1)
    with pytest.raises(ValueError):
        bus.refill("t", -1)
