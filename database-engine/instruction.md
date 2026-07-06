# Context

  Implement a small persistent, ordered key-value store in Go, exposed as a
  command-line tool `dbctl`. Put your Go source under `/app/src/` (with `go.mod`
  and `package main`). It is built with `go build ./...` from `/app/src` and graded
  by running the resulting binary.

  # CLI

      dbctl --db <PATH> <command> [args]

  parent directories on first use. State must persist across separate invocations
  against the same path. Keys and values are UTF-8 text containing no NUL, tab, or
  newline characters.

  Commands:

  - `put <KEY> <VALUE>` — store `KEY` with `VALUE`, replacing any existing value. Exit 0.
  - `get <KEY>` — print the stored value followed by a newline. Exit 0 if the key
    exists; exit 3 if it does not exist.
  - `delete <KEY>` — remove `KEY` if present. Exit 0.
  - `scan [START] [END]` — print matching entries, one per line as `KEY<TAB>VALUE`,
    in ascending key order. With no arguments, print every entry; with one
    argument, print entries from `START` onward; with two, print entries within the
    `START`..`END` range.
  - `batch` — read operations from standard input, one per line, each either
    `put <KEY> <VALUE>` or `delete <KEY>`, and apply them to the store. Blank lines
    are ignored. If any line is not a valid operation, the batch fails.

  # Constraints

  - Standard library only: `go.mod` with no external `require`s, and no import path
    whose first segment contains a dot.
  - Builds with `go build ./...` and no network access.

  # Task

  Implement `dbctl` under `/app/src/` so it behaves as described.
