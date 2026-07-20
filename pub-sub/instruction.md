# Pub-Sub Broker (Go CLI)

Implement, in **Go** (standard library only), a command-driven in-memory publish-subscribe broker as a program under `/app`. It is built with `go build` and run as a single process that **reads commands from standard input, one per line**, and prints **exactly one line of output per command** to standard output, in order. An unknown command or any invalid argument prints `ERR`.

This is **not** a standard fan-out pub-sub. Where this specification differs from typical pub-sub semantics, this document is the source of truth. Subscribers are identified by integer id; there are no user callbacks — a "delivery" simply means the subscriber was selected, and deliveries are observable through command output.

## Core model

- A **subscription** has: an integer `id` (assigned incrementally from 1 in creation order), a `pattern`, an integer `priority`, a non-negative integer `capacity`, and a `max_calls` budget (`-1` = unlimited, otherwise a positive integer). Its token balance starts equal to `capacity`.
- **Pattern**: a non-empty string that is an exact topic (no `*`), the global wildcard `"*"`, or a prefix wildcard `"<prefix>.*"` (prefix non-empty, no `*`). Anything else is invalid.
- **Publish topic**: a non-empty string with no `*`.
- A pattern **matches** a topic when: pattern equals topic; pattern is `"*"`; or pattern is `"<p>.*"` and the topic equals `<p>` or begins with `<p>.` (`"order.*"` matches `"order"` and `"order.x"` but not `"order123"`).
- **Winning tier** of a topic: the exact-topic subscriptions if any exist; else the subscriptions of the single longest matching prefix pattern; else the `"*"` subscriptions; else empty. A more specific tier suppresses less specific ones.
- **Delivery order**: within a set of recipients, order by priority descending, then id descending (newest first).
- **max_calls**: each delivery to a subscription counts against its budget; when a subscription has received `max_calls` deliveries it is removed. `-1` means never removed. This applies to **every** command that delivers to a subscription (PUB, PUBALL, SHARD, FAIR, RING, METER, SEQ, ORDERED, and retained replay).
- **delivered_count**: a lifetime counter incremented once per subscriber delivery across every delivering command (PUB, PUBALL, SHARD, FAIR, METER, RING, SEQ, ORDERED, and retained replay).
- Data values are single whitespace-free tokens.

## Publish pipeline

`PUB` and `PUBALL` process an event: (1) validate the topic; (2) if paused, enqueue the event and deliver to nobody; (3) if the retain flag is set, store the data as the topic's retained value (replacing any prior, remembering first-insertion order across topics); (4) if the topic matches any muted pattern, deliver to nobody; (5) otherwise append the data to the topic's history log; (6) route and deliver. A muted event still updates the retained value (step 3 before 4) but is not recorded in history and reaches nobody.

## Commands

Notation: `[x]` optional. Every command prints one line.

Subscriptions:
- `SUB <pattern> <priority> <capacity> <maxcalls>` → on success `id=<n> replay=<csv>` where `<csv>` is the data of any retained messages delivered to this new subscription on creation (the retained topics its pattern matches, in first-insertion order, honoring its max_calls budget which may stop the replay early); else `ERR`.
- `SUBMANY <p1,p2,...> <priority> <capacity> <maxcalls>` → subscribe the same settings to each comma-separated pattern; print `ids=<n,n,...>` in order. Validate everything first: if any pattern or argument is invalid, print `ERR` and register nothing (atomic).
- `UNSUB <id>` → `true` if removed, else `false`.
- `COUNT` → total subscription count; `COUNT <pattern>` → number of subscriptions whose pattern exactly equals the string.
- `MATCH <topic>` → size of the winning tier for the topic (what `PUB` would deliver to), without delivering.
- `TOPICS` → comma-separated sorted distinct subscription patterns.
- `CLEAR` → remove all subscriptions; `CLEAR <pattern>` → remove those whose pattern exactly equals the string. Prints `ok`.

Publish / pipeline:
- `PUB <topic> <data> [R]` → route to the winning tier, deliver in delivery order; print `<count>:<id,id,...>` (delivered subscriber ids in delivery order). `R` sets the retain flag.
- `PUBALL <topic> <data> [R]` → same, but route to **every** subscription whose pattern matches the topic (all tiers).
- `MUTE <pattern>` → add a mute pattern, print `ok` (or `ERR` if the pattern is invalid); `UNMUTE <pattern>` → `true`/`false`; `MUTED` → sorted csv of mute patterns.
- `PAUSE` → `ok`; `PAUSED` → `true`/`false`; `RESUME` → unpause and replay queued events in FIFO order through the full pipeline (using current state), print the total number of subscriber deliveries across the replay.
- `RETAINED` → sorted csv of topics with a retained value; `CLEARRETAIN` → clear all retained (`ok`); `CLEARRETAIN <topic>` → clear one (`ok`).
- `HISTORY <topic>` → csv of data recorded for the topic in order (empty if none); `CLEARHIST` / `CLEARHIST <topic>` → `ok`.
- `DELIVERED` → the delivered_count.

Algorithms (each routes over the **winning tier** of the topic unless noted):
- `DISTRIBUTE <topic> <load>` → split the non-negative integer `load` across the tier by `capacity` as a hard cap, as evenly as possible, using integer **water-filling with a saturation cascade**: repeatedly, among recipients still below capacity, if the amount left is fewer than the number of such recipients give one unit each to the smallest ids; otherwise give each `floor(remaining/active)` (never exceeding capacity); repeat with the new remainder and active set. Leftover beyond total capacity is overflow; `load=0` gives all zeros; empty/zero-capacity tier makes the whole load overflow. Print `overflow=<o> alloc=<id:amt,...>` with ids ascending (every tier member listed, including 0). Does **not** count as delivery.
- `SHARD <topic> <key> <n>` → select up to `n` tier members by **rendezvous (HRW) hashing**: score = first 8 bytes (big-endian uint64) of `SHA-256("<key>:<id>")`; rank by score descending, ties by larger id; take the first `n`. Deliver to them in that order; print the selected ids as csv (empty if `n=0` or empty tier). `key` non-empty; `n` non-negative.
- `RING <topic> <key>` → route `key` to one tier member via a **consistent-hash ring with virtual nodes**: each member places `capacity` virtual nodes at positions = first 8 bytes big-endian of `SHA-256("<id>#<v>")` for `v` in `0..capacity-1`; the key's position is the first 8 bytes big-endian of `SHA-256("<key>")`; the owner is the virtual node with the smallest position `>=` the key position, wrapping to the smallest position overall if none, ties broken by smaller id. Deliver to the owner; print its id, or `none` if the ring is empty.
- `FAIR <topic>` → deliver to exactly one tier member with `capacity >= 1`, chosen by **stride scheduling**: keep a persistent `pass` value per subscription (starting 0); pick the eligible member with the smallest `pass` (ties: smallest id), then add `stride = (2^32) / capacity` to its `pass`. Print its id, or `none` if no eligible member.
- `METER <topic> <cost>` → among the tier in delivery order, deliver to every member whose token balance is `>= cost`, deducting `cost` from each; skip the rest. Print delivered ids as csv. `cost` non-negative.
- `REFILL <topic> <amount>` → add `amount` tokens to every tier member, capped at its `capacity`; print `ok`. `amount` non-negative.
- `TOKENS <id>` → the subscription's current token balance, or `none` if unknown.
- `SEQ <topic> <seq>` → in-order delivery per topic with a reorder buffer and an expected counter starting at 0: if `seq < expected`, drop (print empty); if `seq > expected`, buffer it (print empty); if `seq == expected`, deliver it, then flush the contiguous run of buffered following seqs, advancing expected; each delivery is a `PUB` of that topic. Print the csv of sequence numbers delivered, in ascending order. `seq` non-negative.
- `NEXTSEQ <topic>` → the topic's expected counter (0 if unused); `PENDING <topic>` → csv of buffered sequence numbers, sorted.
- `ORDERED <json>` → the rest of the line is a JSON array of event objects, each `{"id": str, "topic": str, "deps": [ids], "threshold": int, "data": str, "priority": int}` (`deps` default `[]`, `threshold` default `len(deps)`, `data` default empty, `priority` default 0). Validate: it must be a JSON array, every event needs a non-empty `id` and a valid topic, and ids must be unique — else `ERR`. Deliver by **k-of-n dependency order**: an event is ready once at least `threshold` of its deps have already been delivered in this batch (ids never delivered never count); repeatedly deliver the ready event with the highest priority (ties: smallest original index) via a `PUB` of its topic/data, then re-evaluate (cascading); remaining events (missing deps, cycles, self-deps, unreachable thresholds) are undeliverable. Print `delivered=<csv ids in delivery order> undeliverable=<csv ids by original index> count=<total subscriber deliveries>`.

## Error handling

Every command prints exactly one line. A command prints `ERR` (and changes nothing) whenever any of the following holds; otherwise it prints its normal result:

- The command word is unknown, or the line is empty.
- A **required argument is missing** or a **required integer argument is not a valid integer**. Each command requires exactly the arguments shown before any `[optional]` part: `SUB`/`SUBMANY` require all four (`pattern(s)`, `priority`, `capacity`, `maxcalls`); `PUB`/`PUBALL` require `topic` and `data` (the `R` flag is optional); `SHARD` requires `topic`, `key`, `n`; `RING` requires `topic`, `key`; `METER`/`REFILL`/`DISTRIBUTE`/`SEQ` require `topic` and the trailing integer; `UNSUB`/`TOKENS` require an integer id; `MATCH`/`HISTORY`/`NEXTSEQ`/`PENDING`/`MUTE` require their one argument. (Unneeded extra trailing tokens are ignored.)
- A `pattern` argument (for `SUB`, `SUBMANY`, `MUTE`) is not a valid pattern, or a `topic` argument (for `PUB`, `PUBALL`, `MATCH`, `HISTORY`, `DISTRIBUTE`, `SHARD`, `RING`, `FAIR`, `METER`, `REFILL`, `SEQ`, `NEXTSEQ`, `PENDING`, and each event topic in `ORDERED`) is not a valid publish topic (empty or containing `*`).
- An out-of-range integer: `capacity` < 0; `maxcalls` neither `-1` nor `>= 1`; any of `load`, `cost`, `amount`, `seq`, `n` < 0.
- A `key` argument (for `SHARD`, `RING`) that is empty.
- For `SUBMANY`: an empty pattern list, or **any** invalid pattern/argument (nothing is registered).
- For `ORDERED`: the payload is not a JSON array of objects, an event is missing `id` or has an invalid `topic`, a field has the wrong JSON type, or two events share an `id`.

Commands that legitimately have an "absent" result do **not** print `ERR`: `UNSUB`/`UNMUTE` of an unknown id/pattern print `false`; `TOKENS` of an unknown id prints `none`; `FAIR`/`RING` over an empty tier print `none`; `HISTORY`/`SHARD`/`METER`/`PENDING` over nothing print an empty line; `RESUME` when not paused prints `0`.

Standard library only. The program is a single process reading stdin to EOF.
