# Turn 1: Hard Sharding Proxy in Go – Weighted, Broadcast, Integrity, Self-Healing, Ops Log, Large Value

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

Weight is optional, default 1 if missing. **Validation** must reject weight <=0 if present (exit 2). For routing, if weight missing, default 1.

We also need to handle special **broadcast keys** prefixed with `global:` – these must be replicated to **all shards** (not just one). This simulates global config that every DB needs.

We also need a **transaction log** `/app/data/ops.log` – every successful `set`/`delete` must append a JSON line for crash recovery. This will be used in Turn2 migration replay.

We focus on new traffic first, legacy ignored.

## Task – Implement Go proxy at `/app/` (module `sharding`), built via `go build -o <binary> .`

### CLI (MUST)

Global `--config` default `/app/config.json`.

Help explicitly required (hard, includes version/checksum/staging):

- Bare proxy with **no args** (`proxy` or `proxy --config X` with no command) must print help to stdout containing ALL of `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config`, `global`, `weight`, `ops.log`, `version`, `checksum`, `staging` and exit 0.
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
  For normal: write to its weighted designated shard, self-healing: clean up duplicate/misplaced copies in other shards (delete from wrong shards with version bump)
  For global: write to ALL shards (replicate)
  On success, also append to ops.log with shard_id, ts, version

proxy --config /app/config.json delete <key> -> "true"/"false", exit 0
  For normal: delete from all shards where key exists (self-healing clean duplicates), true if any deleted
  For global: delete from all shards (true if any shard had it)
  On success (true), also append to ops.log

proxy --config /app/config.json list-keys -> sorted JSON array deduped, reads all shards, exit 0
  Must include global keys once even if replicated, sorted lexicographically exact

proxy --config /app/config.json distribution -> JSON map shard_id (string) -> count, includes all ids even zero, exit 0
  Counts per shard include broadcast keys (so sum may be >= unique keys)

proxy --config /app/config.json ops-log -> prints ops.log lines as JSON array sorted by ts optional, skips invalid JSON line with warning containing "corrupt"/"invalid"/"warning", exit 0
```

Exit codes: 0 success (including help, get null), 1 I/O error, 2 invalid input (bad config, duplicate id, empty path, negative id, id>=count, weight<=0 if present, bad args, unknown command, missing key, empty key).

For invalid config, **no stdout**, only stderr.

**Empty string key handling – EXPLICIT to fix Oracle null ambiguity (hard):**

- Empty string `""` **is NOT considered legitimate** for this task and must be treated as invalid input: `get-shard-id ""`, `get ""`, `set ""`, `delete ""`, `get-shard-path ""` must **exit 2 with no stdout** (only stderr). This is distinct from missing key argument (zero args) which also exits 2. Empty string is provided (len 1, value empty) but invalid. This avoids Oracle null ambiguity.
- Missing key argument (e.g., `proxy get-shard-id` with zero args) must also exit 2 no stdout.

### Weighted Sharding Algorithm (MUST, not just mod)

- Use MD5 big-endian mod but **weighted**:
  - Each shard has `weight` default 1 if missing. If weight present, must be >0 else config invalid exit 2.
  - Total weight = sum(weights)
  - Compute hash: MD5 of key's UTF-8 bytes, interpret 16-byte digest as big-endian unsigned integer (big.Int.SetBytes), e.g., Python `int(md5(key.encode()).hexdigest(),16)`
  - Compute `weighted_index = hashInt % totalWeight`
  - Iterate shards **sorted by id ascending**, subtracting weight: for each shard in id order, if `weighted_index < shard.weight`, pick that shard id; else `weighted_index -= weight` and continue.

  Example: shards 0:w1, 1:w2, 2:w1, 3:w1 → total 5. Hash%5=0→0, 1→1, 2→1, 3→2, 4→3.

- For `global:` keys, `get-shard-id` returns -1 (broadcast indicator), not normal weighted.

### Persistence with Integrity Header + Ops Log + Self-Healing (unique, hard)

Shard file format:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```

- Canonical data JSON: **sorted keys, no spaces, without HTML escaping**: Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)`. Go's default `json.Marshal` escapes `<>&` as `\u003c` etc; you **must disable** escaping via `json.Encoder.SetEscapeHTML(false)` for checksum. Include test with `<>&`.
- On write: always new format with correct checksum, atomic via `os.CreateTemp` in same dir + `os.Rename`. **Source inspection**: must contain `CreateTemp` and `Rename` and `SetEscapeHTML`.
- On read and **initialization**: `NewShardingProxy` must **validate and repair every configured shard before any command** (not just on read). For each shard:
  - Missing/empty → empty `{}`
  - Has `data` field: require `checksum` present and non-empty, else corruption. Compute expected checksum from `data` canonical (sorted keys, no spaces, no HTML escaping). Mismatch → corruption.
  - Corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum, atomic.
  - No `data` field → old flat format, treat whole file as data, convert to new format on next write. Invalid JSON → corruption handling.
- Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair.

- **Self-healing on set/delete (hard, new):**
  - `set` for normal key: after writing to correct weighted shard, must also clean up duplicate/misplaced copies in other shards (delete from wrong shards if key exists there, with atomic write). This fixes buggy prior migration that left duplicates.
  - `delete` for normal key: must delete from all shards where key exists (to clean duplicates), true if any deleted, with atomic writes. For global, already deletes from all.
  - This requires reading all shards on set/delete to detect duplicates.

- **Transaction log** `/app/data/ops.log`: On each successful `set`/`delete` (returns true or set success), append JSON line (one per line, no array) to ops.log:
  ```
  {"op":"set","key":"...","value":...,"ts":<unix_nano>,"shard_id":<id or -1 for global>,"version":<int>}
  {"op":"delete","key":"...","ts":<unix_nano>,"shard_id":<id or -1>,"version":<int>}
  ```
  - Create file if missing, open with `O_APPEND` atomically, `SetEscapeHTML(false)`.
  - If ops.log contains invalid JSON line (corruption), skip that line on read (ops-log command) and log warning to stderr containing "corrupt"/"invalid"/"warning". Must use `bufio.Scanner` with large buffer 10*1024*1024 to handle 100KB+ lines (default 64KB fails), not `json.Decoder` which can infinite loop on invalid line.

- `list-keys` sorted, deduped, `distribution` includes zeros, counts include broadcast keys in each shard. Empty string "" is invalid, so list-keys should never include "" (since set "" fails).

- **Large value handling:** must handle 100KB+ JSON value atomically with valid checksum and no HTML escaping. Test with 100KB string via set/get.

### Config Validation

On startup, validate, else exit 2 stderr, no stdout:
- shard_count>0, len(shards)>0, ids unique, non-negative, <count, path non-empty, weight>0 if present (missing → default 1 for routing, but present <=0 invalid)
- Config missing/invalid JSON → exit 2, no stdout
- For this task, empty string key "" is invalid → exit 2 no stdout (separate from missing key arg)

### Constraints

- Go stdlib only, `go.mod` no external requires, `go list -f '{{join .Imports " "}}'` no dot imports
- Builds via `go build -o <binary> .`
- Respect `--config`
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed
- Must use `bufio.Scanner` for ops.log with big buffer, not `json.Decoder`
- Must handle empty string as invalid exit 2

### Success

- Weighted routing correct, broadcast global: works (replicates to all)
- Durable new-format files with valid checksum (no HTML escaping), ops.log appended with version, ts, shard_id
- Sorted list-keys exact, distribution includes zeros, accounts for broadcast
- Config validation exit 2 no stdout, corruption backup/recreate including missing checksum, raw-string handling, help and bare help exit 0 containing version,checksum,staging, empty string invalid exit 2
- Self-healing set/delete cleans duplicates, large 100KB value handling, atomic CreateTemp+Rename+SetEscapeHTML, staging dir mention

Legacy ignored for now.
