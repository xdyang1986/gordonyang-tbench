Scenario
Build a persistent, log-structured token-bucket rate limiter exposed as a command-line tool (rlctl). The limiter must survive across separate process invocations against the same database path, supporting bucket configuration, token consumption at explicit timestamps, inspection, deletion, range scan, batched mutations, statistics, and compaction.

The store is log-structured: set, delete, and successful allow append a record to a log file; current state is the log replayed with last record per key wins. A delete always appends a tombstone record, even for an absent key — exit 0, but it adds a dead record. Superseded sets and successful allows and tombstones are dead records that persist until compact.

Token bucket math is deterministic integer arithmetic. Each bucket stores capacity C, refill rate R tokens per second, current tokens T, and last timestamp L in milliseconds. On set, T=C and L=0. On allow at timestamp TS with request N:
  delta = max(0, TS - L)
  refilled = T + R * delta / 1000   integer division floor
  available = min(C, refilled)
  if available >= N: allow succeeds, new T = available - N, new L = TS, append a record, print "allow", exit 0
  else: deny, print "deny", exit 3, append no record, state unchanged.
Peek at TS computes available the same way but does not mutate state or append a record; prints integer available followed by newline, exit 0 if bucket exists, exit 3 if absent.

Interface
    rlctl --db <PATH> <command> args

The database file and parent directories are created on first use. Keys are UTF-8 text with no NUL, tab, or newline. Capacity, refill, tokens, timestamp are non-negative integers fitting in signed 64 bit.

Command  Behavior
    set <KEY> <CAPACITY> <REFILL>  Append a set record. Configure bucket with capacity and refill per second, resetting tokens to capacity and last timestamp to 0, replacing any existing bucket in current view. Exit 0.
    allow <KEY> <TOKENS> <TIMESTAMP_MS>  Attempt to consume tokens at timestamp. Print "allow" newline exit 0 on success and append state update record; print "deny" newline exit 3 on insufficient tokens or absent key and append nothing.
    peek <KEY> <TIMESTAMP_MS>  Print available tokens integer newline without consuming; exit 0 if bucket exists, exit 3 if absent. No record appended.
    delete <KEY>  Append a tombstone record. Exit 0 always, even if key absent. After replay key absent.
    scan START END  Print KEY<TAB>CAPACITY<TAB>REFILL<TAB>TOKENS<TAB>LAST_TS lines sorted lexicographically by raw bytes. No arguments prints all; one argument prints from START inclusive onward; two arguments prints half-open [START, END).
    batch  Read operations from stdin one per line: set <KEY> <CAPACITY> <REFILL> or delete <KEY> or allow <KEY> <TOKENS> <TIMESTAMP_MS>. Append one record per successful operation as single all-or-nothing unit. Blank lines ignored; any invalid line or any allow that would deny fails batch with non-zero exit and appends no records.
    stats  Print exactly live=<L>\tdead=<D> newline exit 0. Takes no arguments. L is number of present buckets after replay. D is total records in log minus L.
    compact  Rewrite log to one set-like record per present bucket in bytewise key order containing latest capacity refill tokens last_ts, reclaiming dead records. Afterward stats reports dead 0. Must not change peek or scan output for any timestamp. Durable across processes. Exit 0. Takes no arguments.

Constraints
    Go standard library only — no external dependencies.
    Builds with go build ./... from /app/src/ with no network access.
    Half-open scan, bytewise order, exit 3 on absent peek or denied allow are unchanged.
    Batch all-or-nothing, deny appends nothing, delete tombstone always appended.

Task
    Implement rlctl under /app/src/ (with go.mod and package main) so it behaves as described.
