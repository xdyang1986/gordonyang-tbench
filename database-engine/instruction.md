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

Scenario 1: Sequential inserts
$ dbctl --db /tmp/t.db put k01 a
$ dbctl --db /tmp/t.db put k02 b
$ dbctl --db /tmp/t.db put k03 c
$ dbctl --db /tmp/t.db put k04 d
$ dbctl --db /tmp/t.db put k05 e
$ dbctl --db /tmp/t.db get k03
              # observed: not found (exit 3)
c             # expected


Scenario 2: Mixed inserts and deletes
$ dbctl --db /tmp/u.db put c 3
$ dbctl --db /tmp/u.db put d 4
$ dbctl --db /tmp/u.db put e 5
$ dbctl --db /tmp/u.db put f 6
$ dbctl --db /tmp/u.db put g 7
$ dbctl --db /tmp/u.db put a 1
$ dbctl --db /tmp/u.db delete f
$ dbctl --db /tmp/u.db delete g
$ dbctl --db /tmp/u.db get d
              # observed: not found (exit 3)
4             # expected

$ dbctl --db /tmp/u.db scan
a	1
c	3
d	4         # scan sees it, but get cannot find it
e	5
In both cases scan still lists the key correctly, but point lookups via get fail once the tree has split beyond a single node.

Task
Fix the defects in /app/src/ so that every stored key is retrievable with get and results stay consistent with scan, across node splits and deletions. The project must continue to build with go build ./... using only the Go standard library. Do not change unrelated behavior.
