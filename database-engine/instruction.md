Scenario
Build a persistent, ordered, log-structured key-value store exposed as a command-line tool (dbctl). The store must survive across separate process invocations against the same database path, supporting point reads, writes, deletes, range scans, batched mutations, statistics, and compaction.

The store is log-structured: put and delete append a record to a log file; current state is the log replayed with last record per key wins. A delete always appends a tombstone record, even for an absent key — exit 0, but it adds a dead record. Superseded puts and tombstones are dead records that persist until compact.

Interface
    dbctl --db <PATH> <command> args

The database file and parent directories are created on first use. Keys and values are UTF-8 text with no NUL, tab, or newline characters. The empty string is a valid value, distinct from an absent key.

Command  Behavior
    put <KEY> <VALUE>  Append a put record. Store key with value, replacing any existing value in the current view. Exit 0.
    get <KEY>  Print the value followed by a newline; a key whose value is the empty string prints just a newline. Exit 0 if found; exit 3 if absent.
    delete <KEY>  Append a tombstone record. Exit 0 always, even if the key was absent. After replay the key is absent.
    scan START END  Print KEY<TAB>VALUE lines sorted lexicographically by raw bytes. No arguments prints all entries; one argument prints from START onward inclusive; two arguments prints half-open range [START, END).
    batch  Read operations from stdin, one per line: put <KEY> <VALUE> or delete <KEY>. Append one record per operation as a single all-or-nothing unit. Blank lines are ignored; any invalid line fails the batch with non-zero exit and appends no records.
    stats  Print exactly live=<L>\tdead=<D> followed by newline, and exit 0. Takes no arguments. L is number of present keys after replay. D is total records in the log minus L.
    compact  Rewrite the log to one put record per present key in bytewise key order, reclaiming dead records. Afterward stats reports dead 0. Must not change get or scan output. Durable across processes. Exit 0. Takes no arguments.

Constraints
    Go standard library only — no external dependencies.
    Builds with go build ./... from /app/src/ with no network access.
    Half-open scan, empty-value≠absent, batch all-or-nothing, bytewise order, exit 3 on absent get are unchanged from prior version.

Task
    Implement dbctl under /app/src/ (with go.mod and package main) so it behaves as described.
