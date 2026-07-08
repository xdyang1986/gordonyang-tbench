Scenario
A service mesh requires a routing layer that maps request keys to backend nodes through consistent hashing. The design must satisfy two goals: minimize key reassignment when backends join or leave the cluster, and distribute a key's replicas across distinct failure zones. Implement this layer as a command-line tool called router.

CLI Interface
router --config <PATH> --requests <PATH>
Cluster Configuration
The --config flag points to a JSON document that defines the cluster topology:
{
  "replicas": 3,
  "nodes": [
    {"id": "n1", "weight": 100, "zone": "rack-a", "status": "up"},
    {"id": "n2", "weight": 50,  "zone": "rack-b", "status": "down"}
  ]
}
replicas — how many distinct nodes each key should be routed to (the primary destination plus its ordered backups).

nodes[].id — a unique, non-empty identifier for the node.

nodes[].weight — a non-negative integer controlling how many virtual nodes this node places on the ring. Greater weight yields a proportionally larger slice of the key space.

nodes[].zone — an optional string representing the node's failure zone (e.g., a rack). If absent or empty, the node is considered to occupy its own unique zone equal to its id.

nodes[].status — must be "up" or "down".

Any node that is "down" or whose weight equals 0 is ineligible for traffic.

Request Input
The --requests flag points to a plain-text file with one request key per line.

Hash Ring
Routing operates on a 32-bit consistent-hash ring covering the range [0, 2³²). Let H(s) denote the CRC-32 checksum of the UTF-8 encoding of string s, using the IEEE polynomial (identical to Go's crc32.ChecksumIEEE).

For each traffic-eligible node with identifier ID, place weight virtual nodes on the ring. The virtual node at index i (0 ≤ i < weight) occupies position H("ID#i") — the node ID, followed by #, followed by the decimal representation of i.

Each request key K occupies position H(K).

When two positions on the ring are identical, ties are broken first by node ID (lexicographic order), then by virtual-node index (ascending).

Zone-Aware Key Routing
Given a key, find its ring position and walk clockwise (toward increasing positions, wrapping at the end) to produce an ordered sequence of distinct nodes as they are first encountered. From this sequence, build the route using zone-diversity selection:

Traverse the sequence in order. Accept a node if its zone has not already been used by a previously accepted node. If its zone is already taken, set the node aside.

Continue until replicas nodes have been accepted.

If the full sequence is exhausted with fewer than replicas accepted, backfill from the set-aside nodes in the same clockwise encounter order until replicas nodes are chosen or no candidates remain.

The first accepted node is the primary; the remainder are backups in selection order.

Output
For every request key, in the order given, emit one line to stdout: a JSON array of the selected node IDs.
["n3","n1","n5"]
Exit Codes
Code	Meaning
0	Every request was successfully routed to replicas distinct nodes.
1	Degraded — at least one request could not be assigned the full replicas count of distinct nodes.
2	Invalid input — the config is unreadable or violates the specification (e.g., invalid JSON, replicas < 1, missing/empty/duplicate node IDs, negative weight, unrecognized status value), or the requests file cannot be read. No output should be produced.
Constraints
Go standard library only; no external dependencies.

Must compile successfully via go build ./... from /app/src/ with no network access.

Deliverable
Place the full implementation under /app/src/, including a go.mod and a package main entry point.
