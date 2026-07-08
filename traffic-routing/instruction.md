Overview
A service mesh requires a routing layer that assigns request keys to backend nodes via consistent hashing, minimizing key redistribution when nodes are added or removed. Build this layer as a CLI tool named router.

Usage
text

Copy
router --config <PATH> --requests <PATH>
Configuration
The --config flag accepts a path to a JSON file describing the cluster topology:

json

Copy
{
  "replicas": 3,
  "nodes": [
    {"id": "n1", "weight": 100, "status": "up"},
    {"id": "n2", "weight": 50,  "status": "down"}
  ]
}
replicas — the number of distinct nodes each key must be assigned to (one primary plus backup replicas).

nodes[].id — a unique, non-empty string identifying the node.

nodes[].weight — a non-negative integer specifying how many virtual nodes this node contributes to the hash ring. Higher weight means a proportionally larger share of the key space.

nodes[].status — either "up" or "down".

Nodes marked as "down" or with a weight of 0 are excluded from receiving traffic.

Request Input
The --requests flag accepts a path to a plain-text file listing request keys, one per line.

Hash Ring Construction
The system uses a 32-bit consistent-hash ring spanning the range [0, 2³²). Define H(s) as the CRC-32 checksum (IEEE polynomial, equivalent to Go's crc32.ChecksumIEEE) of the UTF-8 byte representation of string s.

For every node eligible to receive traffic, generate weight virtual nodes on the ring. Virtual node at index i (where i ranges from 0 to weight−1) for a node with identifier ID is placed at position H("ID#i") — that is, the node ID concatenated with # and the decimal index.

Each request key K maps to ring position H(K).

When two ring positions collide, break ties first by node ID (lexicographic), then by virtual-node index (ascending).

Key Routing Algorithm
For a given key, locate its position on the ring, then traverse clockwise (ascending positions, wrapping around) collecting distinct nodes in the order they are first encountered. Skip any node already selected. Stop once replicas distinct nodes have been gathered. The first collected node is the primary; the remainder are ordered backups.

Output Format
For each request key, in input order, print a single line to stdout: a JSON array of the selected node IDs. Example:

text

Copy
["n3","n1","n5"]
Exit Codes
Code	Meaning
0	All requests were successfully routed to the full replicas count of distinct nodes.
1	Degraded routing — at least one request could not reach replicas distinct nodes.
2	Invalid input — the config file is unreadable or violates the spec (e.g., malformed JSON, replicas < 1, missing/empty/duplicate node IDs, negative weight, invalid status), or the requests file is unreadable. Produce no output in this case.
Constraints
Go standard library only — no third-party dependencies.

Must compile cleanly with go build ./... from /app/src/ without network access.

Deliverable
Implement the router tool under /app/src/, including a go.mod file and a package main entry point.
