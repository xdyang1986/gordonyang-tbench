# Context
You are building a lightweight embedded database engine in Go.

Provide Go source code under /app/src/ (including go.mod with module declared and package main) that compiles via go build ./... into a dbctl binary. You do not need to commit a pre-built binary.

CLI Interface
    dbctl --db <PATH> <command> [args]
        --db defaults to /app/data/store.db. Create the file and any parent directories automatically on first use.
        Keys and values are UTF-8 strings containing no NUL, tab, or newline characters. Key ordering is bytewise (lexicographic over raw bytes).

Commands:

put <KEY> <VALUE>	Insert or overwrite the entry for KEY. Return 0 on success.
get <KEY>	        Print VALUE\n to stdout if KEY exists. Return 0 if found, 3 if missing
delete <KEY>	    Remove KEY if present; no-op if absent (idempotent). Return 0 on success.
scan [START] [END]	Print KEY\tVALUE\n per entry in ascending byte order. START is inclusive, END is exclusive. No args → all entries; one arg → all entries with key ≥ START.	Return 0 on success.
Invalid usage	Print an error message to stderr.	2 (any non-zero except 3)

Persistence & Durability
Each command invocation runs as a separate process. All writes must be fully persisted — visible to subsequent invocations and durable on disk (via fsync + atomic rename) — before the write command exits with status 0.

Constraints
    Standard library only. No external dependencies: go.mod must contain no require directives, and no import path may have a dot in its first path segment.
    The project must build with no network access.
