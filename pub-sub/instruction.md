# Pub-Sub Broker

Build a thread-safe, in-memory publish-subscribe message broker as a single file `/app/pubsub.py`. Export a class `PubSub` importable via `from pubsub import PubSub`. Use only the Python standard library. All tests import directly from this file.

This is **not** a standard fan-out pub-sub system. Where this specification differs from typical pub-sub semantics, this document is the source of truth.

## Topics and validation

**Pattern** (used by `subscribe*`, `mute`) — a non-empty string that is one of:
- an exact topic — contains no `*`;
- the global wildcard — literally `"*"`;
- a prefix wildcard — `"<prefix>.*"` where `<prefix>` is non-empty and contains no `*`.

Anything else is an invalid pattern.

**Publish topic** (used by `publish*`, `get_matching_count`, `get_history`, `distribute`, `publish_sharded`, `publish_fair`) — a non-empty string containing no `*`.

**A pattern matches a topic** when any of: the pattern equals the topic; the pattern is `"*"`; or the pattern is `"<p>.*"` and the topic equals `<p>` or begins with `<p>.` (dot-boundary matching — `"order.*"` matches `"order"` and `"order.placed"` but not `"order123"`).

Raise `ValueError` for: an invalid pattern or publish topic; a callback, filter, or error handler that is not callable; a transform that is neither callable nor `None`; a priority that is not an int (booleans excluded); a `max_calls` that is neither `None` nor a positive int (booleans excluded); a `capacity` that is not a non-negative int (booleans excluded); a `load` that is not a non-negative int (booleans excluded); a filter that returns a value which is neither a `(topic, data)` pair nor `None`; a sharding `key` that is not a non-empty string; and an `n` (shard count) that is not a non-negative int (booleans excluded).

Subscriber **callbacks** always receive exactly one positional argument: the delivered value (the `data` after any per-subscription transform). The topic is not passed to callbacks. (Filters receive `(topic, data)` — this is different.)

## Subscriptions

- `subscribe(pattern, callback, priority=0, max_calls=None, transform=None, capacity=1) -> int` — register a subscription and return a unique integer id, assigned incrementally from 1 in call order. The same callback object may be registered multiple times; each registration is independent. `max_calls` bounds successful deliveries (see Delivery); `transform`, if given, is applied to `data` before the callback receives it; `capacity` is used by `distribute` and `publish_fair` (see below). On registration, the new subscriber immediately receives any retained messages whose topics its pattern matches (see Retained), subject to the same transform / max_calls rules.
- `subscribe_once(pattern, callback, priority=0, transform=None, capacity=1) -> int` — equivalent to `subscribe(..., max_calls=1)`.
- `subscribe_many(patterns, callback, priority=0, max_calls=None, transform=None, capacity=1) -> list[int]` — subscribe the same callback to each pattern; return the ids in order. `patterns` must be a list or tuple. Validation is up front: if any pattern or other argument is invalid, raise `ValueError` **before registering any** subscription (the call is atomic — no partial registration).
- `unsubscribe(sub_id) -> bool` — remove by id; return whether it existed.
- `unsubscribe_callback(callback) -> int` — remove every subscription whose callback is that object (identity comparison); return the count removed.

## Delivery semantics (apply to `publish`, `publish_all`, retained replay, `publish_sharded`, and `publish_fair`)

- **Ordering**: invoke callbacks by descending priority, then descending id (newest first) as tiebreaker. (`publish_sharded` and `publish_fair` use their own selection order — see below.)
- **Invocation**: each callback receives `transform(data)` if the subscription has a transform, else `data`.
- **Lock discipline**: compute and snapshot the ordered recipient set while holding the lock, then release the lock before invoking any callbacks. Subscriptions created during delivery are excluded from the in-progress delivery.
- **Skipping removed subscriptions**: before invoking each callback, if its subscription was removed during this delivery, skip it and do not count it.
- **Exceptions**: if a callback or its transform raises, swallow it, invoke the error handler if one is set, and continue. The invocation still counts as delivered but does **not** count toward that subscription's `max_calls`.
- **max_calls**: a subscription with `max_calls=N` is removed after `N` successful (non-raising) deliveries. `None` means unlimited. (`subscribe_once` is `max_calls=1`.)
- **Return value**: `publish`/`publish_all` return the number of callbacks actually invoked (including any that raised).

## Publish pipeline

`publish(topic, data=None, retain=False)` and `publish_all(topic, data=None, retain=False)` process an event through these steps, in order:

1. Validate the publish topic.
2. If paused, enqueue the event (preserving its mode — publish vs publish_all — topic, data, and retain flag) and return 0.
3. Apply filters in ascending filter-id order; each is called `fn(topic, data)` and returns a replacement `(topic, data)` or `None`. `None` aborts the event (return 0). A return value that is neither raises `ValueError`. Otherwise the returned values replace `topic`/`data`. After all filters, re-validate the (possibly rewritten) topic.
4. If `retain`, store `data` as the retained value for the final topic (replacing any prior value).
5. If the final topic matches any muted pattern, return 0.
6. Append `data` to the final topic's history log.
7. Route and deliver: `publish` notifies only the most-specific matching tier (exact-topic subscribers if any exist; else the subscribers of the single longest matching prefix pattern; else `"*"` subscribers — a more specific tier suppresses all less-specific tiers); `publish_all` notifies every subscription whose pattern matches the topic.

Ordering note: a muted event still updates retained data (step 4 before 5) but is not recorded in history and delivers to nobody. A paused or filter-aborted event skips steps 4–7 entirely.

`publish_batch(events) -> list[int]` — publish each event sequentially and return per-event delivered counts. Each element is either a `(topic, data)` tuple or a dict with `topic`, optional `data`, optional `retain`. `events` must be a list or tuple. (While paused, each queued event contributes 0 to the returned list, and is delivered later by `resume`.)

## Filters, error handler, mute, pause

- `add_filter(fn) -> int` / `remove_filter(filter_id) -> bool` — register/remove pipeline filters (step 3).
- `set_error_handler(handler) -> None` — `handler` is called `handler(exception, sub_id, data)` (with the original, pre-transform `data`) whenever a callback or transform raises; if the handler itself raises, swallow it. Pass `None` to clear.
- `mute(pattern) -> None`, `unmute(pattern) -> bool`, `muted_patterns() -> set` — muting suppresses delivery and history for matching publishes but still retains.
- `pause() -> None`, `is_paused() -> bool`, `resume() -> int` — while paused, publishes are queued. `resume` unpauses, replays queued events in FIFO order through the full pipeline (using state at replay time — filters, mutes, and subscriptions added while paused all apply on replay), and returns the total callbacks invoked across the replay. Calling `resume` when not paused is a no-op that returns 0.

## Retained messages and history

- Retained messages are replayed to a subscription when it is created, in the insertion order of the retained topics, following the same ordering / transform / exception / max_calls rules as normal delivery (so a `max_calls` budget can be exhausted partway through the replay). `clear_retained(topic=None)` clears all (None) or one exact topic. `retained_topics() -> set`.
- `get_history(topic) -> list` — the data values recorded for that topic (pipeline step 6), in order; empty list if none. `clear_history(topic=None)` clears all (None) or one exact topic.

## Introspection

- `clear(topic=None)` — remove all subscriptions (None) or those whose pattern exactly equals the string (literal comparison, never wildcard-expanded).
- `get_subscriber_count(topic=None)` — total (None) or count whose pattern exactly equals the string.
- `get_matching_count(topic)` — the number of subscribers `publish(topic)` would notify (size of the most-specific matching tier), without invoking anything.
- `topics() -> set` — distinct pattern strings currently subscribed.
- `delivered_count() -> int` — lifetime total callbacks invoked (normal deliveries, retained replays, resume replays, sharded deliveries, and fair deliveries); skipped/muted/paused events contribute nothing, while callbacks that raised do count.

## Ordered delivery (`publish_ordered`)

`publish_ordered(events) -> dict` delivers a batch of interdependent events respecting declared dependencies. `events` is a list of dicts; each has `id` (unique string), `topic` (a valid publish topic), optional `data` (default `None`), optional `deps` (list of event ids, default `[]`), optional `threshold` (int, default `len(deps)`), and optional `priority` (int, default `0`). Raise `ValueError` if `events` is not a list, if any event lacks `id` or a valid `topic`, or if two events share the same `id`.

- An event becomes **ready** once at least `threshold` of its `deps` have been delivered within this batch. An id that is never delivered (missing from the batch, or itself undeliverable) never counts toward any dependent's threshold. `threshold` may be `0` (ready immediately, even before its deps) or larger than the number of deps (then it can never become ready). Default threshold means all deps must be delivered; a smaller threshold is a k-of-n rule.
- Repeatedly select the ready event with the highest `priority` (ties broken by smallest original index), deliver it via `publish(topic, data)` (so a delivered event still passes through the full publish pipeline — it may be muted, filtered, or reach zero subscribers, contributing 0 to `count` while still counting as delivered for its dependents), then re-evaluate readiness (delivering one event may unblock others — cascading). Stop when no undelivered event is ready.
- Remaining events are **undeliverable** (missing deps, cycles, self-dependencies, or unreachable thresholds), reported by original index.

Return `{"delivered": [ids in delivery order], "undeliverable": [ids by original index], "count": total callbacks invoked}`.

## Capacity-weighted distribution (`distribute`)

`distribute(topic, load) -> dict` splits a whole-number `load` across the subscribers that `publish(topic)` would notify (the most-specific matching tier), respecting each subscriber's `capacity` as a hard cap. It does **not** invoke callbacks — it only computes an allocation.

Allocation rules (integer, deterministic):

- The recipients are exactly the winning specificity tier for `topic`; each recipient's cap is its `capacity`.
- Distribute `load` as **evenly as possible** among recipients, giving no recipient more than its capacity. When a recipient would exceed its capacity, it is filled to its capacity and its surplus is redistributed evenly among the recipients that still have spare capacity — repeat until the load is placed or every recipient is at capacity (a saturation cascade).
- Concretely, iterate: let the *active* recipients be those below their capacity. If the amount left to place is fewer than the number of active recipients, give one unit each to the active recipients with the smallest ids until it runs out. Otherwise give each active recipient `floor(remaining / number_active)` units (never exceeding its capacity), then repeat with the new remainder and the new (possibly smaller) active set.
- If `load` exceeds the total capacity of the tier, every recipient is filled to capacity and the leftover is **overflow**. If `load` is `0`, every allocation is `0`. If the tier's total capacity is `0` (or the tier is empty), the entire `load` is overflow.

Return `{"allocations": {sub_id: amount, ...}, "overflow": leftover}` where `allocations` covers every recipient in the tier (including those allocated 0) and `overflow` is the load that could not be placed (0 unless `load` exceeded total tier capacity).

Worked example: three subscribers on `"t"` with capacities 1, 10, 10 (ids 1, 2, 3) and `load=12`. Subscriber 1 fills to 1; the remaining 11 spreads over subscribers 2 and 3; the even split plus the leftover unit (to the smaller id) yields allocations `{1: 1, 2: 6, 3: 5}` with overflow 0.

## Sharded delivery (`publish_sharded`)

`publish_sharded(topic, key, data=None, n=1) -> list[int]` delivers `data` to `n` of the subscribers that `publish(topic)` would notify (the most-specific matching tier), selected by **rendezvous (highest-random-weight) hashing** on `key` so that the choice is stable for a given key and evenly spread across keys. `key` must be a non-empty string; `n` a non-negative int.

- For each candidate subscriber, compute a score: take the SHA-256 digest of the UTF-8 bytes of the string `"{key}:{sub_id}"`, and interpret the **first 8 bytes** as a big-endian unsigned integer.
- Rank candidates by score **descending**; break ties by **larger** `sub_id`. Select the first `n` (all of them if the tier has fewer than `n`).
- Deliver `data` to the selected subscribers in that ranked order, following the normal Delivery semantics (transform, exceptions/error handler, `max_calls`, `delivered_count`).
- Return the selected subscriber ids in delivery (ranked) order. If `n` is 0 or the tier is empty, deliver to nobody and return `[]`.

## Weighted fair scheduling (`publish_fair`)

`publish_fair(topic, data=None) -> int | None` delivers `data` to exactly **one** subscriber from the tier `publish(topic)` would notify, chosen by **stride scheduling** so that, over repeated calls, each eligible subscriber is selected in proportion to its `capacity`. Only subscribers with `capacity >= 1` are eligible.

The broker keeps a persistent integer `pass` value per subscription (shared across all `publish_fair` calls, independent of topic). On each call:

- Consider the eligible subscribers in the winning tier. If none, deliver to nobody and return `None`.
- Any eligible subscriber not yet seen by `publish_fair` starts with `pass = 0`.
- Select the eligible subscriber with the smallest `pass`, breaking ties by smallest `sub_id`.
- Increase that subscriber's `pass` by its **stride** = `(2**32) // capacity` (so higher capacity → smaller stride → selected more often).
- Deliver `data` to the selected subscriber (normal Delivery semantics: transform, exceptions/error handler, `max_calls`, `delivered_count`) and return its `sub_id`.

## Thread safety

Use `threading.RLock`. All public methods must be thread-safe. Never hold the lock while invoking a callback, filter, transform, or error handler. Reentrant calls from user code must not deadlock.
