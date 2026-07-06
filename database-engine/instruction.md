Context
A command-line key-value store (dbctl) is provided at /app/src/. It supports persistent, ordered storage with the following interface:
dbctl --db <PATH> <command> [args]

Command	Behavior
    put <KEY> <VALUE>	Store a key-value pair
    get <KEY>	Retrieve the value for a key
    delete <KEY>	Remove a key
    scan [START] [END]	Print KEY\tVALUE lines in ascending key order
The tool compiles and runs, but has two incorrect behaviors.

Bug 1 — Overwriting a key does not update the value
    $ dbctl --db /tmp/t.db put color red
    $ dbctl --db /tmp/t.db put color blue
    $ dbctl --db /tmp/t.db get color
    red          # wrong — should be "blue"
Writing to an existing key must replace the stored value.

Bug 2 — Scan includes the end bound
    $ dbctl --db /tmp/u.db put a 1
    $ dbctl --db /tmp/u.db put b 2
    $ dbctl --db /tmp/u.db put c 3
    $ dbctl --db /tmp/u.db scan a c
    a	1       # observed — includes "c"
    b	2
    c	3

    a	1       # expected — excludes "c"
    b	2

scan START END must return keys in the half-open range [START, END) — start inclusive, end exclusive.

Task
Fix both defects in /app/src/ so the tool behaves as described. The project must continue to build with go build ./... using only the Go standard library. Do not alter unrelated behavior.
