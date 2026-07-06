Scenario
Build a persistent, ordered key-value store exposed as a command-line tool (dbctl). The store must survive across separate process invocations against the same database path, supporting point reads, writes, deletes, range scans, and batched mutations.

Interface
    dbctl --db <PATH> <command> [args]

The database file (and parent directories) are created on first use. Keys and values are UTF-8 text with no NUL, tab, or newline characters.

Command	Behavior
    put <KEY> <VALUE>	Store key with value, replacing any existing value. Exit 0.
    get <KEY>	Print the value followed by a newline. Exit 0 if found; exit 3 if absent.
    delete <KEY>	Remove the key if present. Exit 0.
    scan [START] [END] — Print KEY\tVALUE lines sorted lexicographically by raw bytes. No arguments prints all entries; one argument prints from START onward (inclusive); two arguments prints the half-open range [START, END).
    batch	Read operations from stdin (one per line: put <KEY> <VALUE> or delete <KEY>). Apply all. Blank lines are ignored; any invalid line fails the batch.

Constraints
    Go standard library only — no external dependencies.
    Builds with go build ./... from /app/src/ with no network access.

Task
    Implement dbctl under /app/src/ (with go.mod and package main) so it behaves as described.
