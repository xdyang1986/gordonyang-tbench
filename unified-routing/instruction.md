Scenario
A SaaS platform fronts multiple third-party cloud API providers offering the same capability but with different latency, cost, error rate, region placement, and capacity. You must build a routing layer that returns, per request, an ordered failover chain of providers spread across regions, AND durably records every decision to a crash-consistent journal, so that if the process is killed mid-run it can be restarted with --resume and continue exactly where it stopped — without losing, duplicating, or corrupting decisions, and without double-spending provider capacity.

Implement this layer, from scratch in Go, as a command-line tool called router.

CLI Interface
router --config <PATH> --requests <PATH> --journal <PATH> [--resume]

--config, --requests, and --journal are required. --resume is an optional flag.

Cluster Configuration
The --config flag points to a JSON document:

{
  "strategy": "latency",
  "max_replicas": 2,
  "providers": [
    {
      "id": "aws-us-east",
      "region": "us-east",
      "latency_ms": 45,
      "cost_per_1k": 0.012,
      "error_rate": 0.005,
      "capacity_rps": 1200,
      "status": "up"
    }
  ]
}

strategy — one of "latency", "cost", "balanced". Determines scoring weights.
max_replicas — optional integer >= 1 (default 1). The number of providers to return per request.
providers[].id — unique non-empty string identifier.
providers[].region — string naming provider region, e.g. "us-east".
providers[].latency_ms — non-negative integer base latency in milliseconds.
providers[].cost_per_1k — non-negative number cost in USD per 1000 requests.
providers[].error_rate — number in [0,1] representing observed error fraction.
providers[].capacity_rps — integer >= 0 total requests this provider can serve across the whole file; consumed in request order. Zero means ineligible.
providers[].status — must be "up" or "down". Only "up" providers are eligible.

Request Input
The --requests flag points to a UTF-8 file of newline-delimited JSON, one request object per line:

{"id":"r1","user_region":"us-east","sla_ms":100}

Fields:
id — required request identifier string, used for ordering and journal validation.
user_region — string region of the caller.
sla_ms — optional integer maximum acceptable latency. If omitted, no SLA filter applies.

Blank lines and whitespace-only lines are ignored. A trailing newline does not create an extra request.

Routing Logic (stateful, per request in input order)
Maintain a remaining capacity per provider, initialized from capacity_rps. Process requests in order. For each request:
1. Eligible providers are those with status "up", remaining capacity > 0, and — if sla_ms is present —
   latency_ms <= sla_ms.
2. Score each eligible provider (lower is better):
   effective_latency = latency_ms * 0.5 if provider.region == user_region else latency_ms
   score = effective_latency * w_lat + cost_per_1k * w_cost + error_rate * w_err
   Weights by strategy:
     latency:  w_lat=1.0,  w_cost=100.0,  w_err=10000.0
     cost:     w_lat=0.1,  w_cost=1000.0, w_err=10000.0
     balanced: w_lat=1.0,  w_cost=500.0,  w_err=10000.0
   Order eligible providers by ascending score, breaking ties by lexicographically smallest id.
3. Select an ordered failover chain of up to max_replicas distinct providers, ordered by preference,
   that is spread across regions as much as possible: prefer not to place two providers from the same
   region in the chain while a provider from a not-yet-used region is still available. Reaching
   max_replicas distinct providers is the priority (see Exit Codes).
4. The decision is that ordered chain of provider ids (an empty chain if no provider is eligible).
5. Consume capacity: decrement the remaining capacity of the primary (first provider) in the chain by 1.
   The other providers in the chain do not consume capacity. An empty chain consumes nothing.

Journal Format (byte-exact)
The journal is a binary append-only file. All multi-byte integers are unsigned big-endian.

File header: exactly the 8 ASCII bytes "URJRNL01", written once when the file is first created.

After the header, one record per decision, in sequence order starting at 0:
  seq        uint32   0-based index of the request this decision is for
  id_len     uint16   byte length of the request id (its UTF-8 encoding, not the rune count)
  id         id_len bytes, the request id UTF-8
  n_prov     uint16   number of providers in the chain (0 for an empty chain)
  then, repeated n_prov times, in chain order:
    prov_len uint16   byte length of the provider id (UTF-8)
    prov     prov_len bytes, the provider id UTF-8
  crc32      uint32   CRC-32 (IEEE, the polynomial used by Go's hash/crc32.ChecksumIEEE) computed over
                      every byte of this record from seq through the last prov (all bytes before crc32)

Each record must be flushed to stable storage (fsync / File.Sync) before the next record is written, so
that a crash can leave at most one partially-written trailing record.

Durability and Recovery
Without --resume:
- If the journal file already exists and is non-empty, exit 2 without modifying it and without producing
  output (refuse to overwrite).
- Otherwise create the file, write the 8-byte header, fsync, and process all requests from sequence 0,
  appending one fsync'd record per request.

With --resume:
- If the journal does not exist or is empty, behave as a fresh run (write the header, start at seq 0).
- Otherwise read the existing journal. If it is shorter than 8 bytes or its first 8 bytes are not
  "URJRNL01", exit 3.
- Parse records sequentially from offset 8, tracking the expected sequence 0,1,2,...:
  * If the remaining bytes are too few to hold a complete record, that is a torn trailing record from a
    crash mid-write: truncate the file at the start of this record and stop parsing.
  * If a complete record's crc32 does not match: if the record ends exactly at end-of-file, it is a
    torn trailing record — truncate at its start and stop; otherwise (more bytes follow it) the journal
    is corrupt — exit 3.
  * If an intact record's seq does not equal the expected sequence, exit 3. If its seq is at or beyond
    the number of requests, or its id does not equal the id of the request at that index, exit 3.
  * An intact record advances the expected sequence and contributes its recorded chain.
- Reconstruct remaining capacity from the recovered chains: for every recovered non-empty chain,
  decrement the remaining capacity of its primary (first) provider by 1. This must happen before
  routing any further requests.
- Continue processing requests from the next unwritten sequence, appending fsync'd records. Re-running
  --resume on a fully-written, consistent journal appends no new records.

Output
After processing (fresh or resumed), write to stdout the full list of decisions in sequence order, one
per line: a JSON array of provider ids (e.g. ["aws-us-east","gcp-us-central"]), or [] for an empty
chain. Use compact JSON (no spaces).

Exit Codes
0 — Every request received exactly max_replicas providers.
1 — Degraded — at least one request received fewer than max_replicas providers (including an empty
    chain).
2 — Invalid input — config unreadable or violating the specification (invalid JSON, missing/unknown
    strategy, max_replicas < 1, missing providers array, duplicate/empty provider id, non-integer or
    negative latency, negative cost, error_rate outside [0,1], negative capacity, unrecognized status),
    requests file unreadable or containing an invalid JSON line or a line without a string id, missing
    required arguments, or a non-empty journal without --resume. No output is produced.
3 — Corrupt journal — an existing journal has a bad header, a complete record with a bad CRC that is not
    the final record, a sequence gap, or a record whose id does not match the corresponding request.
    No output is produced.

Constraints
Go, standard library only — no third-party modules (go.mod must declare no external requires, and no
imported package may be outside the standard library).
Place the implementation under /app/src as a buildable Go module (include go.mod); it will be built with
`go build ./...` and the resulting `router` binary invoked directly.
Must run without network access.

Deliverable
Running `router --config config.json --requests requests.jsonl --journal journal.bin` writes the
byte-exact journal and prints the region-diverse failover chains, and re-running with --resume after an
interrupted run continues correctly with capacity already consumed by earlier decisions accounted for.
