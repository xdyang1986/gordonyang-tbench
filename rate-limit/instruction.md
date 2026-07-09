Scenario
Build a crash-consistent, log-structured token-bucket rate limiter exposed as a command-line tool (rlctl). The limiter must survive across separate process invocations against the same database path — including after a crash mid-write — supporting bucket configuration, token consumption at explicit timestamps, inspection, deletion, range scan, batched mutations, statistics, and compaction.

The store is an append-only binary log of length-framed records. set, delete, and successful allow append one record; current state is the log replayed in order with last record per key winning. A delete always appends a tombstone, even for an absent key: exit 0 and adds a dead record. Superseded set records, successful allow records, and tombstones are dead records that persist until compact.

On-disk record format — mandatory, big-endian
Each record on disk is:
    uint32 length | payload[length] | uint32 crc32
crc32 is IEEE CRC-32 of the payload bytes (same as Go hash/crc32 IEEE or Python zlib crc32). Two payload kinds are defined:
    set or allow state update:
        byte 'S' | uint32 keylen | key bytes | int64 capacity | int64 refill | int64 tokens | int64 last_ts
    delete tombstone:
        byte 'D' | uint32 keylen | key bytes
All multi-byte integers are big-endian, signed for int64, unsigned for uint32 length and crc. Key bytes are raw; key length is number of bytes.

Crash recovery
On replay, read records sequentially from the start of the file:
- If the next record is truncated — not enough bytes remain for declared length plus trailing crc — and its computed end position is at or beyond end-of-file, treat it as a torn trailing write: stop replay, ignore that partial record and everything after it. It does not count toward stats. The next append operation must first truncate the file to the end offset of the last fully valid record, ensuring torn bytes never accumulate.
- If a record has wrong crc, unknown type byte, or internally inconsistent length, and it is not at end-of-file, that is fatal corruption: print an error message to stderr and exit 4. Do not modify the file in this case.
- Otherwise the record is valid: apply it to in-memory state and continue.

Token bucket math — deterministic overflow-safe integer arithmetic
Each bucket stores capacity C, refill rate R tokens per second, current tokens T, and last timestamp L in milliseconds. On set, T is set to C and L to 0.
At timestamp TS define:
    delta = max(0, TS - L)
    available = min( C , T + floor( R * delta / 1000 ) )
R * delta may overflow signed 64-bit; the implementation must saturate the multiplication so available never overflows and always lies in [0, C]. Use unsigned 128-bit intermediate or checked saturation; do not wrap.
On allow with request N at TS:
    if available >= N: consume — new T = available - N, new L = TS, append an 'S' record with updated state, print "allow" followed by newline, exit 0.
    else deny — print "deny" followed by newline, exit 3, append nothing, state unchanged. Absent key also denies with exit 3.
peek at TS computes available the same way, prints the integer followed by newline without mutating state or appending, exit 0 if bucket exists else exit 3.

Keys
Keys are byte strings that may contain any byte except NUL 0x00, TAB 0x09, and LF 0x0A; spaces and other bytes including high-bit bytes are allowed. Any command invoked with a key containing a forbidden byte must exit 2 without modifying the store. Capacity, refill, tokens, and timestamp arguments must be non-negative decimal integers fitting in signed 64-bit; otherwise exit 2.

Interface
    rlctl --db <PATH> <command> args
The database file and its parent directories are created on first use.

Command  Behavior
    set <KEY> <CAPACITY> <REFILL>  Append an 'S' record configuring bucket with tokens set to capacity and last_ts to 0, replacing any existing bucket in current view. Exit 0 on success, 2 on invalid argument or key.
    allow <KEY> <TOKENS> <TIMESTAMP_MS>  Attempt consume at timestamp. On success print "allow" newline exit 0 and append updated 'S' record. On insufficient tokens or absent key print "deny" newline exit 3 append nothing. Exit 2 on invalid argument or key.
    peek <KEY> <TIMESTAMP_MS>  Print available integer newline without consuming. Exit 0 if present, 3 if absent, 2 on invalid argument or key. No record appended.
    delete <KEY>  Append a 'D' tombstone record. Exit 0 always even if absent, 2 on invalid key. After replay key is absent.
    scan [START] [END]  Print one line per present bucket sorted by raw key bytes: KEY<TAB>CAPACITY<TAB>REFILL<TAB>TOKENS<TAB>LAST_TS newline terminated. No args prints all. One arg prints from START inclusive onward. Two args prints half-open [START, END). Keys in output are printed as raw bytes interpreted as UTF-8 replacement is acceptable for test purposes because test keys avoid forbidden bytes; sorting is bytewise. Exit 0, or 2 on invalid key argument.
    batch  Read operations from stdin, one per line, TAB-delimited fields, no extra spaces: "set<TAB>KEY<TAB>CAPACITY<TAB>REFILL" or "delete<TAB>KEY" or "allow<TAB>KEY<TAB>TOKENS<TAB>TIMESTAMP_MS". Empty lines containing zero bytes are ignored. Apply as single all-or-nothing unit: parse and simulate all lines first; if any line is malformed, has invalid key or arguments, or any allow would deny, fail the whole batch with non-zero exit and append nothing. Otherwise append one record per operation in order. Exit 0 on success, 2 on usage or validation failure.
    stats  Print exactly "live=<L>\tdead=<D>" followed by newline, exit 0. Takes no arguments. L is number of present buckets after replay of valid records. D is number of valid records in log minus L. Torn trailing partial records do not count.
    compact  Rewrite log to one 'S' record per present bucket in raw-byte key order holding latest capacity refill tokens last_ts, reclaiming dead records. Afterward stats reports dead 0 and peek/scan output is unchanged for any timestamp. Must be durable and crash-safe: write to temporary file in same directory then atomic rename, ignore any stale <PATH>.compact.tmp left by prior interrupted compaction. Exit 0, 2 on invalid usage.

Exit codes
    0 success
    2 usage error, invalid argument, or forbidden key byte
    3 denied allow or peek on absent key
    4 corrupt database detected during replay

Constraints
    Go standard library only — no external dependencies.
    Builds with go build ./... from /app/src/ with no network access.
    Half-open scan range, raw-byte key order, batch all-or-nothing, delete tombstone always appended, deny appends nothing.
    Binary log format with length prefix and CRC32 as specified is mandatory for crash consistency.
    Overflow-safe token refill arithmetic must saturate, never wrap.

Task
    Implement rlctl under /app/src/ (with go.mod and package main) so it behaves as described.
