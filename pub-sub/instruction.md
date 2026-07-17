Pub-Sub Broker
Build a thread-safe, in-memory publish-subscribe message broker as a single file /app/pubsub.py. Export a class PubSub importable via from pubsub import PubSub. Use only the Python standard library. All tests import directly from this file.

This is not a standard fan-out pub-sub system. Where this specification differs from typical pub-sub semantics, this document is the source of truth.

Topics and Validation
Pattern (for subscribe*, mute)
A valid pattern is a non-empty string matching one of:

An exact topic — contains no * character.

The global wildcard — literally "*".

A prefix wildcard — the form "<prefix>.*" where <prefix> is non-empty and itself contains no *.

Anything else is invalid.

Publish topic (for publish*, get_matching_count, get_history)
A valid publish topic is a non-empty string containing no *.

Pattern matching rules
A pattern matches a topic when any of the following hold:

The pattern is identical to the topic.

The pattern is "*".

The pattern is "<p>.*" and the topic either equals <p> or begins with <p>. (dot-boundary matching — e.g., "order.*" matches "order" and "order.placed" but not "order123").

Validation errors
Raise ValueError for any of:

Invalid pattern or publish topic.

A callback, filter, or error handler that is not callable.

A transform that is neither callable nor None.

A priority that is not an int (booleans excluded).

A max_calls that is neither None nor a positive integer (booleans excluded).

Callback signature
Subscriber callbacks always receive exactly one positional argument: the delivered value (i.e., data after any per-subscription transform has been applied). The topic is not passed to callbacks. (Note: filters receive (topic, data) — this is different.)

Subscriptions
subscribe(pattern, callback, priority=0, max_calls=None, transform=None) -> int
Register a subscription and return a unique integer ID, assigned incrementally from 1 in call order. The same callback object may be registered multiple times — each registration is independent.

max_calls: limits how many successful deliveries fire this subscription (see Delivery).

transform: if provided, applied to data before the callback receives it.

Upon registration, the new subscriber immediately receives any retained messages whose topics its pattern matches (see Retained), subject to the same transform / max_calls rules.

subscribe_once(pattern, callback, priority=0, transform=None) -> int
Equivalent to subscribe(..., max_calls=1).

subscribe_many(patterns, callback, priority=0, max_calls=None, transform=None) -> list[int]
Subscribe the same callback to each pattern in the list. Return the IDs in order. patterns must be a list or tuple.

unsubscribe(sub_id) -> bool
Remove a subscription by ID. Return whether it existed.

unsubscribe_callback(callback) -> int
Remove every subscription whose callback is the given object (identity comparison). Return the count removed.

Delivery Semantics
These rules govern publish, publish_all, and retained-message replay.

Ordering
Among the set of subscribers to notify, invoke callbacks in this order:

Descending priority (highest first).

Descending ID (newest first) as tiebreaker.

Invocation
Each callback receives one argument: transform(data) if the subscription has a transform, otherwise data.

Lock discipline
Compute and snapshot the ordered subscriber set while holding the lock. Then release the lock before invoking any callbacks. Subscriptions created during delivery are excluded from the in-progress delivery.

Skipping removed subscriptions
Before invoking each callback, check whether its subscription still exists. If it was removed during this delivery cycle, skip it (do not count it).

Exception handling
If a callback or its transform raises an exception:

Swallow it.

Invoke the error handler (if one is set).

Continue with the next callback.

The invocation counts toward total delivered but does not count toward that subscription's max_calls.

max_calls behavior
A subscription with max_calls=N is automatically removed after N successful (non-raising) deliveries. max_calls=None means unlimited.

Return value
All publish methods return the number of callbacks actually invoked (including those that raised).

Publish Pipeline
Both publish(topic, data=None, retain=False) and publish_all(topic, data=None, retain=False) process an event through these steps in order:

Validate the publish topic.

Pause check — if the broker is paused, enqueue the event (preserving its mode, topic, data, and retain flag) and return 0.

Apply filters — in ascending filter-ID order, call each filter as fn(topic, data). A filter returns either a replacement (topic, data) tuple or None. If None, the event is aborted (return 0). Otherwise the returned values replace topic and data for subsequent steps. After all filters, re-validate the (possibly rewritten) topic.

Retain — if retain is true, store data as the retained value for the final topic (replacing any prior value).

Mute check — if the final topic matches any muted pattern, return 0.

Record history — append data to the final topic's history log.

Route and deliver:

publish (most-specific tier only): notify exact-topic subscribers if any exist; otherwise the subscribers of the single longest matching prefix pattern; otherwise "*" subscribers. A more specific tier suppresses all less-specific tiers.

publish_all (all tiers): notify every subscription whose pattern matches the topic.

Important ordering note
A muted event still updates retained data (step 4 happens before step 5) but is not recorded in history and delivers to nobody. A paused or filter-aborted event skips steps 4–7 entirely.

publish_batch(events) -> list[int]
Publish each event sequentially; return per-event delivered counts. Each element of events is either a (topic, data) tuple or a dict with keys topic, optional data, optional retain. The events argument must be a list or tuple.

Filters, Error Handling, Muting, and Pausing
Filters
add_filter(fn) -> int: Register a pipeline filter (invoked at step 3). Returns a filter ID.

remove_filter(filter_id) -> bool: Remove a filter by ID.

Error handler
set_error_handler(handler) -> None: Set a handler called as handler(exception, sub_id, data) (with the original pre-transform data) whenever a callback or transform raises. If the handler itself raises, swallow it. Pass None to clear.

Muting
mute(pattern) -> None: Suppress delivery and history recording for publishes matching this pattern.

unmute(pattern) -> bool: Remove a mute; return whether it existed.

muted_patterns() -> set: Return all currently muted patterns.

Pausing
pause() -> None: Enter paused state; subsequent publishes are queued.

is_paused() -> bool: Check whether the broker is paused.

resume() -> int: Unpause, replay all queued events in FIFO order through the full pipeline (using state at replay time), and return the total callbacks invoked across the entire replay.

Retained Messages and History
Retained messages
When a subscription is created, it immediately receives retained messages for all topics its pattern matches (in the insertion order of retained topics). The same ordering, transform, exception handling, and max_calls rules apply as for normal delivery.

clear_retained(topic=None): Clear retained data for one exact topic, or all topics if None.

retained_topics() -> set: Return the set of topics that currently have a retained value.

History
get_history(topic) -> list: Return the list of data values recorded for that topic (from pipeline step 6), in chronological order. Empty list if none.

clear_history(topic=None): Clear history for one exact topic, or all topics if None.

Introspection
clear(topic=None): Remove all subscriptions (if None) or only those whose pattern exactly equals the given string (literal comparison, never wildcard-expanded).

get_subscriber_count(topic=None): Total subscription count (if None) or the count whose pattern exactly equals the given string.

get_matching_count(topic): The number of subscribers that publish(topic) would notify (i.e., the size of the most-specific matching tier), without actually invoking anything.

topics() -> set: The set of distinct pattern strings currently subscribed.

delivered_count() -> int: The lifetime total of callbacks invoked by this broker (includes normal deliveries, retained replays, and resume replays). Skipped, muted, and paused events contribute nothing.

Ordered Delivery (publish_ordered)
publish_ordered(events) -> dict delivers a batch of interdependent events respecting declared dependencies.

Event format
events is a list of dicts. Each dict has:

Key	Required	Default	Description
id	Yes	—	Unique string identifier for this event
topic	Yes	—	A valid publish topic
data	No	None	Payload
deps	No	[]	List of event IDs this event depends on
threshold	No	len(deps)	Minimum number of deps that must be delivered before this event is ready
priority	No	0	Scheduling priority
Raise ValueError if events is not a list or any event lacks id or a valid topic.

Execution rules
An event becomes ready once at least threshold of its deps have been delivered within this batch. An ID that is never delivered (missing from the batch, or itself undeliverable) never counts toward any dependent's threshold.

Repeatedly select the ready event with the highest priority (ties broken by smallest original index in the input list), deliver it via publish(topic, data), then re-evaluate readiness (delivering one event may unblock others — cascading).

Stop when no undelivered event is ready.

Remaining events are undeliverable (due to missing deps, cycles, or unreachable thresholds).

Return value
{
    "delivered": [...],      # event IDs in delivery order
    "undeliverable": [...],  # event IDs by original index
    "count": int             # total callbacks invoked across all delivered events
}
Thread Safety
Use threading.RLock. All public methods must be thread-safe. Never hold the lock while invoking a callback, filter, transform, or error handler. Reentrant calls from user code must not deadlock.
