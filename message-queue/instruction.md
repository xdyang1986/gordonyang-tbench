# Kafka-Like Partitioned Message Queue Broker

Build a Kafka-like message queue broker in Go at `/app`. It tracks topics with partitions, supports append-only per-partition logs, producer/consumer semantics with consumer groups, and optional durable persistence with crash-consistent recovery and compaction.

You will implement a single `package main` binary. It reads commands from stdin, updates in-memory state, optionally appends to a durable log, and writes one line of output per query to stdout.

---

## Runtime and Environment

- Go standard library only (enforced by import check; no third-party packages — verifier rejects any import containing a dot, internet remains enabled for base image but not needed).
- Build: `cd /app && go build -o /app/broker .`
- Reads stdin line-by-line, writes stdout, exits 0 on valid input.
- Single-threaded sequential processing.

The broker reads environment variable `MQ_STATE_DIR`:

- unset or empty → **in-memory mode**: no disk writes, `COMPACT` is a no-op.
- set to a directory path → **durable mode**: append-only log at `$MQ_STATE_DIR/mq.log`, recovered on startup, compacted on demand. The directory is created if needed.

Blank lines (empty or whitespace-only) are ignored.

---

## Naming Validation

- **Topic / Group name**: length 1..255, characters only `[A-Za-z0-9._-]`, not `.` nor `..`.
- **Payload**: single token (no spaces), length 1..1024, must NOT contain comma `,` (to keep `FETCH_RANGE` unambiguous). Any token containing comma is invalid input.
- **Partition**: integer `>=0` for explicit commands. The broker allows up to 1000 partitions per topic.
- **Offset**: integer `>=0`, except `COMMIT` allows `-1` (meaning no committed offset / clear).
- **Timestamp**: integer `>=0`. Negative timestamps are invalid input and must cause non-zero exit. Valid inputs are non-decreasing, but your implementation does not need to enforce ordering beyond rejecting negatives; just parse it.
- **num_partitions**: integer `>=1 && <=1000`.

---

## Command Stream

After start, each non-blank line is one command. Tokens are space-separated (no quoted strings). On **invalid input** (malformed line, unknown command, wrong arity, non-integer where integer expected, invalid name according to rules above, `num_partitions` out of range, empty payload, negative timestamp), the broker must exit with non-zero status. Output is unspecified in that case.

Application-level errors (e.g., topic does not exist, partition out of range of existing topic, offset beyond log length for `COMMIT`/`SEEK`) are **not** invalid input: they produce a single line `ERROR` and continue.

### State-changing commands — no output on success, `ERROR` on application error

**`CREATE_TOPIC <topic> <num_partitions> <timestamp>`**
Create topic with `num_partitions` partitions numbered `0..num_partitions-1`. Initially empty. If topic already exists, it is idempotent: keep existing partitions, do not change them, no output, nothing logged. Logged in durable mode only when it actually creates.

**`DELETE_TOPIC <topic> <timestamp>`**
Delete topic and all its messages. Also removes all consumer-group state related to that topic (subscriptions, committed offsets, positions). Groups themselves are NOT deleted — an empty group with no subscriptions left remains visible in `LIST_GROUPS` (this is the intended behavior, matching Kafka where consumer groups outlive topics). For backwards compatibility the verifier leniently accepts either keeping the empty group or garbage-collecting it, but new implementations should keep empty groups. If topic does not exist, no-op. Logged only when topic existed.

**`PRODUCE <topic> <partition> <payload> <timestamp>`**
Append `payload` to topic-partition log. Offset assigned is current log length (starting at 0). On success output `<offset>` (single integer). On application error (topic missing, partition invalid `partition >= num_partitions`), output `ERROR`. Logged only on success.

**`PRODUCE_AUTO <topic> <payload> <timestamp>`**
Auto-partitioned produce, Kafka-like. Partition is chosen deterministically as `sum(byte values of payload) % num_partitions`. Offset assigned similarly. On success output `<partition> <offset>` (two tokens). On topic missing, output `ERROR`. In durable mode the broker must log this as a normalized `PRODUCE <topic> <chosen_partition> <payload> <timestamp>` record so replay is simple.

**`JOIN_GROUP <group> <topic> <timestamp>`**
Create consumer group if not exists, subscribe it to topic. Idempotent: subscribing twice to same topic is no-op. If topic does not exist, output `ERROR`. No output on success. Logged only when subscription is newly added.

**`COMMIT <group> <topic> <partition> <offset> <timestamp>`**
Commit offset for group. `offset` must be `>= -1` and `< high` where high is partition's next offset, and must be `>= low` or `-1` where low is partition's earliest retained after TRIMs, with `-1` meaning clear committed. If `offset >= high` or `< -1` or (`offset != -1` and `offset < low`), output `ERROR`. If topic/partition invalid, `ERROR`. Otherwise sets group's committed offset for that partition to `offset` (overwrites). Auto-creates group and auto-subscribes to topic if needed (if topic exists). No output on success. Logged only when committed value actually changes.

**`SEEK <group> <topic> <partition> <offset> <timestamp>`**
Set group's next poll position to `offset`. `offset` must be `>= low && <= high` where low is partition's earliest retained offset (after TRIMs) and high is next offset. If out of range, `ERROR`. If topic/partition invalid, `ERROR`. Auto-creates group and subscribes. No output on success. Logged only when position actually changes.

**`TRIM <topic> <partition> <offset> <timestamp>`**
Trims log by removing messages with offset < offset (log retention). Sets low watermark to max(old low, offset). After trim, messages with offset < low are considered deleted and `FETCH` returns `NONE` for them. `low` starts at 0, high is next offset to assign. Conditions:
- If topic/partition invalid → `ERROR`
- If offset < 0 or offset > high → `ERROR`
- If offset <= low (already trimmed) → no-op
- On success, if low actually increases, committed offsets that are now < low are cleared (GET returns NONE) and positions < low are auto-advanced to low. Logged only when low increases.
- `PARTITION_INFO` then returns `<low> <high>`, and `TOPIC_INFO` total counts only retained messages `sum(high-low)`.
- `COMMIT` must now respect low: offset must be >= low or -1, else `ERROR`. `SEEK` must be >= low.

**`PRODUCE_BATCH <topic> <count> <partition1> <payload1> <partition2> <payload2> ... <timestamp>`**
Atomically produces `count` messages to `topic` across possibly different partitions. `count` >=1 <=100. Format: after topic and count, there are `2*count` tokens alternating partition and payload, then timestamp. Example: `PRODUCE_BATCH orders 2 0 hello 1 world 10` produces `hello` to partition 0 and `world` to partition 1 atomically.
- Validation: topic must exist, all partitions must be >=0 and < num_partitions, all payloads valid, count matches number of pairs, timestamp >=0 else invalid input.
- Application error: if topic missing or any partition invalid → output `ERROR` and **none** of the batch is appended (atomic).
- On success: assign offsets per partition in order given (each partition's offset is its current high before append), append all messages in order given, and output comma-separated offsets in same order (e.g., `0,0` if two different partitions both at offset 0, or `0,1` if same partition gets two messages). Logged as individual `PRODUCE` records in order given (so replay preserves offsets) only on success.

**`COMPACT <timestamp>`**
In durable mode, rewrite log to minimal record set that reconstructs current state exactly, via temp file + atomic rename. In-memory mode: no-op. No output.

### Query commands — one output line each

All query commands produce exactly one line on stdout even in error cases (`ERROR`, `NONE`, or data).

**`FETCH <topic> <partition> <offset> <timestamp>`**
Retrieve message at offset. If topic/partition invalid → `ERROR`. If offset `<0` → `ERROR`. If offset `< low` (trimmed) or `>= high` → `NONE`. Else payload string.

**`FETCH_RANGE <topic> <partition> <start> <end> <timestamp>`**
Range fetch: `[start, end)` . `start` and `end` must be `>=0`, `end >= start`, else `ERROR`. If topic/partition invalid → `ERROR`. Effective start = max(start, low). If effective start `>= high` or effective start `>= end` → `NONE`. Else collect payloads from effective start to `min(end, high)-1`. If none, `NONE`, else comma-joined payloads (e.g., `a,b,c`). Tests guarantee payloads in these tests contain no commas.

**`LIST_TOPICS <timestamp>`**
Sorted (lexicographic) comma-separated topic names, or `NONE` if no topics.

**`TOPIC_INFO <topic> <timestamp>`**
If topic missing → `ERROR`. Else `<num_partitions> <total_retained>` where total_retained is sum of retained messages across all partitions `sum(high-low)` after TRIMs.

**`PARTITION_INFO <topic> <partition> <timestamp>`**
If topic/partition invalid → `ERROR`. Else `<low> <high>` where `low` is earliest retained offset (after TRIMs, starts at 0) and `high` is next offset (log length). Empty partition → `0 0` before any trim, or `<low> <low>` after trimming all.

**`POLL <group> <topic> <partition> <timestamp>`**
Consumer-group poll. If topic/partition invalid → `ERROR`. Auto-creates group and auto-subscribes to topic if needed (when topic exists). Position handling: if group has no position for that partition, initialize to `max(low, committed+1 if committed exists else low)`. If group's existing position `< low` (due to TRIM), auto-advance it to `low`. If position `< high`, output `<offset> <payload>` (e.g., `3 hello`) where offset is position before increment, then increment position by 1. If position `>= high`, output `NONE`. `ERROR` only for invalid topic/partition.

**`GET_GROUP_OFFSET <group> <topic> <partition> <timestamp>`**
If topic/partition invalid → `ERROR`. If group does not exist → `NONE`. Else if group has no committed offset (or committed==-1) or committed `< low` (trimmed) → `NONE`. Else committed offset integer (e.g., `5`).

**`LIST_GROUPS <timestamp>`**
Sorted comma-separated group names, or `NONE`.

---

## Output Format

- For each query and for each `PRODUCE` / `PRODUCE_AUTO` in input order, write exactly one line.
- `PRODUCE` → offset, `PRODUCE_AUTO` → `<partition> <offset>`.
- Queries → as described, or `ERROR` / `NONE`.
- No extra spaces, no header. Flush and exit 0 on valid input.
- On invalid input, exit non-zero.

---

## Durable Persistence

Only when `MQ_STATE_DIR` is set.

**Log format** — `mq.log`:
Sequence of records, each:
```
uint32 little-endian payload_len
uint32 little-endian crc32 IEEE of payload
payload_len bytes UTF-8 payload
```
Payload is command text exactly as logged, e.g., `CREATE_TOPIC orders 3 0`, `PRODUCE orders 0 hello 1`. A record is valid only if 8 header bytes plus payload_len bytes are present and CRC matches.

**What is logged, in order, only when it changes state:**
- `CREATE_TOPIC` → only when creates new topic.
- `DELETE_TOPIC` → only when deletes existing topic.
- `PRODUCE` → only on success. For `PRODUCE_AUTO`, log normalized `PRODUCE <topic> <chosen_partition> <payload> <timestamp>`.
- `TRIM` → only when low watermark actually increases.
- `JOIN_GROUP` → only when adds new subscription.
- `COMMIT` → only when committed value changes.
- `SEEK` → only when position changes (including auto-advances after PRODUCE, POLL, TRIM).
Queries and `COMPACT` are never appended as payloads; `COMPACT` rewrites file.

**Startup recovery:** create directory if needed. Before reading stdin, replay `$MQ_STATE_DIR/mq.log` record by record in order. Each record must reconstruct state exactly as originally processed, preserving offsets (since offsets are determined by append order). Stop at first incomplete or corrupt record (truncated header/payload or CRC mismatch); discard it and all following bytes; truncate log to valid prefix so later appends are clean. Never fail startup due to torn tail. An empty log file recovers cleanly.

Each append must be durable before process continues (fsync).

**Compaction:** `COMPACT` writes new temp file `$MQ_STATE_DIR/mq.log.tmp` containing minimal records that replay to same state:
- For each topic sorted asc: `CREATE_TOPIC <topic> <num_partitions> 0`
- For each topic sorted asc, each partition sorted asc, each message offset asc: `PRODUCE <topic> <partition> <payload> 0` (emit all messages 0..high-1 even if some trimmed, to preserve offsets)
- For each topic sorted asc, each partition sorted asc where low > 0: `TRIM <topic> <partition> <low> 0`
- For each group sorted asc, each subscribed topic sorted asc: `JOIN_GROUP <group> <topic> 0`
- For each group sorted asc, each (topic,partition) sorted asc with committed != -1, committed >= low, and topic still exists: `COMMIT <group> <topic> <partition> <committed> 0`
- For each group sorted asc, each (topic,partition) sorted asc where position exists and position != expected_default (expected = max(low, committed+1) or low): `SEEK <group> <topic> <partition> <position> 0`
All with timestamp 0 is acceptable. Deterministic sorted order required. Then atomic rename over `mq.log`. Ignore any stray `.tmp` files on recovery.

---

## Functional Requirements Summary

1. Topics with fixed partition count; per-partition append-only log; offsets start 0, low watermark starts 0 and moves via TRIM.
2. Produce with explicit partition or auto-hashed via sum(bytes)%partitions; high = len(log), low = earliest retained.
3. Fetch single offset and range respects low/high; trimmed offsets return NONE; PARTITION_INFO returns low/high, TOPIC_INFO totals retained sum(high-low).
4. List topics and groups sorted; topic info shows partitions and total retained messages.
5. Consumer groups: auto-create, subscribe via JOIN or POLL, per-partition position (next to poll) and committed offset, POLL advances position and auto-advances over trimmed ranges, COMMIT respects low (>=low or -1), SEEK respects low..high, GET returns NONE if committed < low or trimmed.
6. Delete topic removes all related group state for that topic, but groups persist visible in LIST_GROUPS even when empty (intended, leniently accepts GC). TRIM removes messages before offset, clears committed < low and advances positions < low.
7. Durable mode survives restarts with crash-consistent recovery and atomic compaction preserving all offsets, low watermarks, and group states (including TRIM).
8. Deterministic output for same stdin and starting disk state; no randomness.
9. Go stdlib only (enforced via verifier import check, allow_internet=true for base image); invalid input including negative timestamp → non-zero exit; application errors → `ERROR` line.

---

## Examples

### Basic produce/fetch

Input:
```
CREATE_TOPIC orders 2 0
PRODUCE orders 0 hello 1
PRODUCE orders 0 world 2
FETCH orders 0 0 3
FETCH orders 0 1 4
LIST_TOPICS 5
TOPIC_INFO orders 6
PARTITION_INFO orders 0 7
```

Output:
```
0
1
hello
world
orders
2 2
0 2
```

### Auto partition and groups

Input:
```
CREATE_TOPIC t 3 0
PRODUCE_AUTO t foo 1
PRODUCE_AUTO t bar 2
JOIN_GROUP g1 t 3
POLL g1 t 0 4
POLL g1 t 0 5
COMMIT g1 t 0 0 6
GET_GROUP_OFFSET g1 t 0 7
SEEK g1 t 0 0 8
POLL g1 t 0 9
```

Explanation: `foo` bytes sum = 102+111+111=324 %3=0, `bar`=98+97+114=309 %3=0, so both go to partition 0. Offsets are per-partition. `POLL` prints the offset before advancing. Output:
```
0 0
0 1
0 foo
1 bar
0
0 foo
```
(First two lines are `PARTITION OFFSET` from PRODUCE_AUTO, next two POLLs return offset+payload at 0 and 1, GET returns committed 0, final POLL after SEEK 0 returns foo again.)

### Durable

With `MQ_STATE_DIR=/tmp/mq`, first run:
```
CREATE_TOPIC a 1 0
PRODUCE a 0 m1 1
JOIN_GROUP g a 2
POLL g a 0 3
COMMIT g a 0 0 4
```
Second run same dir:
```
FETCH a 0 0 5
GET_GROUP_OFFSET g a 0 6
POLL g a 0 7
```
Output:
```
m1
0
NONE
```
Second POLL returns NONE because position was at 1 after first poll.

### Trim / retention

```
CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
PARTITION_INFO t 0 4
TRIM t 0 2 5
PARTITION_INFO t 0 6
FETCH t 0 0 7
FETCH t 0 2 8
TOPIC_INFO t 9
```

Output:
```
0
1
2
0 3
2 3
NONE
c
1 1
```
After TRIM 2, low becomes 2, high stays 3, so offset 0 is gone (NONE), offset 2 remains.

Implement at `/app`. The test harness builds your binary and drives via stdin, and restarts with shared `MQ_STATE_DIR` to verify durability and TRIM persistence.
