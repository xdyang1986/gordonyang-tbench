# Turn 1: Hard Sharding Proxy in Go – Weighted, Broadcast, Integrity, Ops Log

## Background

Traffic outgrown single DB. Historical dump `/app/data/legacy.json` is old flat format, but 4 new shards are provisioned with **weights**:

- `/app/data/shard_0.json` weight 1
- `/app/data/shard_1.json` weight 2
- `/app/data/shard_2.json` weight 1
- `/app/data/shard_3.json` weight 1

Topology `/app/config.json`:
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}
  ]
}
```

Weight is optional, default 1 if missing or <=0? Actually **validation** must reject weight <=0 if present (exit 2). For routing, if weight missing, default 1.

We also need to handle special **broadcast keys** prefixed with `global:` – these must be replicated to **all shards** (not just one). This simulates global config that every DB needs.

We also need a **transaction log** `/app/data/ops.log` – every successful `set`/`delete` must append a JSON line for crash recovery. This will be used in Turn2 migration replay.

We focus on new traffic first, legacy ignored.

## Task – Implement Go proxy at `/app/` (module `sharding`), built via `go build -o <binary> .`

### CLI (MUST)

Global `--config` default `/app/config.json`.

Help explicitly required:

- Bare proxy with **no args** (`proxy` or `proxy --config X` with no command) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config`, `global`, `weight`, `ops.log` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- Unknown command → exit 2.

Commands:

```
proxy --config /app/config.json get-shard-id <key> -> int, exit 0
  For global: keys (prefix global:), return -1 to indicate broadcast (special)
  For normal keys, return weighted shard id

proxy --config /app/config.json get-shard-path <key> -> path(s), exit 0
  For normal: single path of designated shard
  For global: comma-separated sorted list of all shard paths (by id)

proxy --config /app/config.json get <key> -> JSON value or "null", exit 0
  For normal: check its designated shard only
  For global: check all shards in id order, return first found, or null

proxy --config /app/config.json set <key> <value_json> -> durable, exit 0
  value_json is JSON; if not valid JSON, treat as raw string value
  For normal: write to its weighted designated shard
  For global: write to ALL shards (replicate)
  On success, also append to ops.log

proxy --config /app/config.json delete <key> -> "true"/"false", exit 0
  For normal: delete from designated shard
  For global: delete from all shards (true if any shard had it)
  On success (true), also append to ops.log

proxy --config /app/config.json list-keys -> sorted JSON array deduped, reads all shards, exit 0
  Must include global keys once even if replicated

proxy --config /app/config.json distribution -> JSON map shard_id (string) -> count, includes all ids even zero, exit 0
  Counts per shard include broadcast keys (so sum may be >= unique keys)

procxy --config /app/config.json ops-log -> prints ops.log lines as JSON array or raw, exit 0 (optional but helpful)
```

Exit codes: 0 success (including help, get null), 1 I/O error, 2 invalid input (bad config, duplicate id, empty path, negative id, id>=count, weight<=0 if present, bad args, unknown command, missing key).

For invalid config, **no stdout**, only stderr.

### Weighted Sharding Algorithm (MUST, not just mod)

- Use MD5 big-endian mod but **weighted**:
  - Each shard has `weight` default 1 if missing. If weight present, must be >0 else config invalid exit 2.
  - Total weight = sum(weights)
  - Compute hash: MD5 of key's UTF-8 bytes, interpret 16-byte digest as big-endian unsigned integer (big.Int.SetBytes), e.g., Python `int(md5(key.encode()).hexdigest(),16)`
  - Compute `weighted_index = hashInt % totalWeight`
  - Iterate shards **sorted by id ascending**, subtracting weight: for each shard in id order, if `weighted_index < shard.weight`, pick that shard id; else `weighted_index -= weight` and continue.

  Example: shards 0:w1, 1:w2, 2:w1, 3:w1 → total 5. Hash%5=0→0, 1→1, 2→1, 3→2, 4→3.

- For `global:` keys, `get-shard-id` returns -1 (broadcast indicator), not normal weighted.

### Persistence with Integrity Header + Ops Log (unique, hard)

Shard file format:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```

- Canonical data JSON: **sorted keys, no spaces, without HTML escaping**: Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)`. Go's default `json.Marshal` escapes `<>&` as `\u003c` etc; you **must disable** escaping via `json.Encoder.SetEscapeHTML(false)` for checksum. Include test with `<>&`.
- On write: always new format with correct checksum, atomic via `os.CreateTemp` in same dir + `os.Rename`. **Source inspection**: must contain `CreateTemp` and `Rename`.
- On read and **initialization**: `NewShardingProxy` must **validate and repair every configured shard before any command** (not just on read). For each shard:
  - Missing/empty → empty `{}`
  - Has `data` field: require `checksum` present and non-empty, else corruption. Compute expected checksum from `data` canonical (sorted keys, no spaces, no HTML escaping). Mismatch → corruption.
  - Corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum.
  - No `data` field → old flat format, treat whole file as data, convert to new format on next write. Invalid JSON → corruption handling.
- Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair.

- **Transaction log** `/app/data/ops.log`: On each successful `set`/`delete` (returns true or set success), append JSON line (one per line, no array) to ops.log:
  ```
  {"op":"set","key":"...","value":...,"ts":<unix_nano>,"shard_id":<id or -1 for global>}
  {"op":"delete","key":"...","ts":<unix_nano>,"shard_id":<id or -1>}
  ```
  - Create file if missing, open with `O_APPEND` atomically.
  - If ops.log contains invalid JSON line (corruption), skip that line on read (if you implement ops-log command) and log warning to stderr.

- `list-keys` sorted, deduped, `distribution` includes zeros, counts include broadcast keys in each shard.

### Config Validation

On startup, validate, else exit 2 stderr, no stdout:
- shard_count>0, len(shards)>0, ids unique, non-negative, <count, path non-empty, weight>0 if present (missing → default 1 for routing, but present <=0 invalid)
- Config missing/invalid JSON → exit 2, no stdout

### Constraints

- Go stdlib only, `go.mod` no external requires, `go list -f '{{join .Imports " "}}'` no dot imports
- Builds via `go build -o <binary> .`
- Respect `--config`
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed

### Success

- Weighted routing correct, broadcast global: works (replicates to all)
- Durable new-format files with valid checksum (no HTML escaping), ops.log appended
- Sorted list-keys exact, distribution includes zeros, accounts for broadcast
- Config validation exit 2 no stdout, corruption backup/recreate including missing checksum, raw-string handling, help and bare help exit 0

Legacy ignored for now.
