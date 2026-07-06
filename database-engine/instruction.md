Context
A persistent, ordered key-value store (dbctl) is provided at /app/src/. It is backed by an on-disk B+ tree and builds with go build ./....

The tool works correctly for small datasets. Once enough keys are inserted to trigger node splits, stored keys become unretrievable via get — even though scan still lists them.

Interface
dbctl --db <PATH> <command> [args]

Command	Behavior
    put <KEY> <VALUE>	Store a key-value pair
    get <KEY>	Retrieve value for a key (exit 3 if not found)
    delete <KEY>	Remove a key
    scan [START] [END]	Print all KEY\tVALUE lines in ascending order
    Bug — Keys become unreachable after the tree grows

Problem
The store loses internal consistency as it grows. While small, everything works. After enough inserts and deletes, get and scan disagree: scan lists a key with its value, but get reports that same key as missing.

Task
Fix the defect(s) in /app/src/ so that the store remains internally consistent: any key visible to scan must be retrievable with get, and vice versa, across arbitrary sequences of inserts and deletes. The project must continue to build with go build ./... using only the Go standard library. Do not change unrelated behavior.
