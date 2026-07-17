# Pub-Sub Broker

Implement a thread-safe, in-memory publish-subscribe broker in `/app/pubsub.py`, in a class `PubSub` importable as `from pubsub import PubSub`. Pure Python, standard library only. Put the solution only in `pubsub.py` (tests import from it).

This is **not** a textbook fan-out pub-sub; where the contract departs from common pub-sub behavior, the contract below is authoritative.

## Topics & validation

- **Pattern** (used by `subscribe*`, `mute`): non-empty string that is either an exact topic (no `*`), the global `"*"`, or a prefix wildcard `"<prefix>.*"` (prefix non-empty, no `*`). Anything else is invalid.
- **Publish topic** (used by `publish*`, `get_matching_count`, `get_history`): non-empty string with no `*`.
- A pattern **matches** a topic when: pattern equals topic; or pattern is `"*"`; or pattern is `"<p>.*"` and the topic equals `<p>` or starts with `<p>.` (the dot boundary matters: `"order.*"` matches `"order"` and `"order.x"` but not `"order123"`).
- `ValueError` on: invalid pattern/topic; non-callable `callback`, `filter`, or error `handler`; `transform` that is neither callable nor `None`; `priority` that is not an int (bool is not an int); `max_calls` that is neither `None` nor a positive int (bool excluded).

Subscriber **callbacks** are always invoked with exactly one positional argument: the delivered value (the event `data` after any per-subscription `transform`) — the topic is not passed. (This differs from filters, which receive `(topic, data)`.)

## Subscriptions

- `subscribe(topic, callback, priority=0, max_calls=None, transform=None) -> int`: register and return a unique id, incremental from 1 in call order. Same callback may be registered many times (each independent). `max_calls` (see Delivery) bounds how many times it fires; `transform`, if given, is applied to `data` before the callback receives it. Immediately upon registering, the new subscriber receives any retained messages whose topics its own pattern matches (see Retained), subject to the same transform / max_calls / once rules.
- `subscribe_once(topic, callback, priority=0, transform=None) -> int`: equivalent to `subscribe(..., max_calls=1)`.
- `subscribe_many(patterns, callback, priority=0, max_calls=None, transform=None) -> list[int]`: subscribe the same callback to each pattern; return the ids in order. `patterns` must be a list/tuple.
- `unsubscribe(sub_id) -> bool`: remove by id.
- `unsubscribe_callback(callback) -> int`: remove every subscription whose callback is that object (identity); return the number removed.

## Delivery (used by `publish`, `publish_all`, and retained replay)

- **Order**: within the set to notify, invoke by priority descending, then by id descending (newest first).
- Each callback is invoked with one argument: `transform(data)` if the subscription has a transform, else `data`.
- Compute and snapshot the ordered set under the lock, then invoke callbacks with the lock released. Subscriptions created during delivery are not part of the in-progress delivery.
- Before invoking each callback, if its subscription no longer exists (removed during this delivery), skip it and do not count it.
- If a callback (or its transform) raises, swallow it, invoke the error handler if one is set (see below), and continue; it still counts as delivered but does **not** count toward that subscription's `max_calls`.
- **max_calls**: a subscription with `max_calls=N` is removed after it has been delivered `N` times without raising. `max_calls=None` means unlimited. (`subscribe_once` is `max_calls=1`.)
- The return value is the number of callbacks actually invoked.

## Publish pipeline

`publish(topic, data=None, retain=False)` and `publish_all(topic, data=None, retain=False)` process an event in this order:

1. Validate the publish topic.
2. If paused, enqueue the event (preserving its publish vs publish_all mode, topic, data, retain) and return 0.
3. Apply filters in ascending filter-id order. Each filter is called as `fn(topic, data)` and returns either a replacement `(topic, data)` or `None`. `None` aborts the event (return 0). Otherwise the returned values replace `topic`/`data` for the rest of the pipeline. After filters, re-validate the (possibly rewritten) publish topic.
4. If `retain`, store `data` as the retained value for the (final) topic, replacing any prior retained value.
5. If the final topic matches any muted pattern, return 0.
6. Append `data` to the (final) topic's history log.
7. Route and deliver:
   - `publish`: notify only the most-specific matching tier — exact-topic subscribers if any exist, else the subscribers of the single longest matching prefix pattern, else the `"*"` subscribers. A more specific tier suppresses the rest.
   - `publish_all`: notify every subscription whose pattern matches the topic, across all tiers.

Order matters: a muted publish still updates the retained value (step 4 before 5) but is not recorded in history and delivers to nobody (steps 6–7 skipped). A paused or filter-aborted event does none of steps 4–7.

- `publish_batch(events) -> list[int]`: publish each event sequentially and return the per-event delivered counts. Each event is either a `(topic, data)` pair or a dict with `topic`, optional `data`, optional `retain`. `events` must be a list/tuple.

## Filters, error handler, mute, pause

- `add_filter(fn) -> int` / `remove_filter(filter_id) -> bool`: register/remove pipeline interceptors (step 3).
- `set_error_handler(handler) -> None`: `handler` is called as `handler(exception, sub_id, data)` (with the original, pre-transform `data`) whenever a callback/transform raises; if the handler itself raises, swallow it. `None` clears the handler.
- `mute(pattern) -> None`, `unmute(pattern) -> bool`, `muted_patterns() -> set`: muting suppresses delivery and history for matching publishes but still retains.
- `pause() -> None`, `is_paused() -> bool`, `resume() -> int`: while paused, publishes are queued (step 2). `resume` clears the paused state, replays queued events in FIFO order through the full pipeline (using the state at replay time), and returns the total callbacks invoked across the replay.

## Retained & history

- Retained messages are replayed to a subscription when it is created (in the insertion order of the retained topics), following the same ordering / transform / exception / max_calls rules as normal delivery. `clear_retained(topic=None)`: clear all (None) or one exact topic. `retained_topics() -> set`.
- `get_history(topic) -> list`: the data values recorded for that topic (pipeline step 6), in order; empty list if none. `clear_history(topic=None)`: clear all (None) or one exact topic.

## Introspection

- `clear(topic=None)`: remove all subscriptions (None) or those whose topic exactly equals the string (exact comparison, never wildcard).
- `get_subscriber_count(topic=None)`: total (None) or count of subscriptions whose topic exactly equals the string.
- `get_matching_count(topic)`: size of the tier `publish(topic)` would notify, without invoking anything.
- `topics() -> set`: distinct subscribed topic strings.
- `delivered_count() -> int`: total callbacks ever invoked by this broker (normal deliveries, retained replays, resume replays); skipped/muted/paused events contribute nothing.

## Ordered delivery (`publish_ordered`)

`publish_ordered(events) -> dict` delivers a batch of inter-dependent events in dependency order. `events` is a list of dicts; each has `id` (unique string), `topic` (a publish topic), optionally `data`, `deps` (list of event ids, default `[]`), `threshold` (int, default `len(deps)`), and `priority` (int, default `0`). Validate that `events` is a list and each event has an `id` and a valid `topic`, else `ValueError`.

- An event is **ready** once at least `threshold` of its `deps` have already been delivered — counting only deps that actually get delivered in this batch (an id never delivered — missing, or itself undeliverable — never counts). Default threshold means all deps; a smaller threshold is k-of-n.
- Repeatedly: among undelivered events, deliver the ready one with highest `priority`, ties broken by smallest arrival index; then re-evaluate readiness (delivering may unblock others — a cascade). Stop when nothing is ready.
- Remaining events are **undeliverable** (missing deps, cycles, unreachable threshold), reported by arrival index.

Each delivered event is dispatched via `publish(topic, data)` in the computed order. Return `{"delivered": [...], "undeliverable": [...], "count": total callbacks invoked}`.

## Thread-safety

Use `threading.RLock`. All public methods are thread-safe. Never hold the lock while invoking a callback, filter, transform, or error handler. Reentrant calls from these must not deadlock.
