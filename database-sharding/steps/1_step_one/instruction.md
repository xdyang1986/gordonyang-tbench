# Turn 1: Implement Database Sharding Proxy in Go (with integrity and validation)

## Background

Traffic has outgrown single DB. Historical dump is in `/app/data/legacy.json` (old flat format without checksum), but 4 new shards are provisioned:

- `/app/data/shard_0.json`
- `/app/data/shard_1.json`
- `/app/data/shard_2.json`
- `/app/data/shard_3.json`

Topology in `/app/config.json`:
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json"},
    {"id": 1, "path": "/app/data/shard_1.json"},
    {"id": 2, "path": "/app/data/shard_2.json"},
    {"id": 3, "path": "/app/data/shard_3.json"}
  ]
}
```

We focus on new traffic first, but proxy must be robust.

## Task

Implement Go sharding proxy at `/app/` (`module sharding`). Built via `go build -o <binary> .`.

### CLI (MUST)

Global flag: `--config` default `/app/config.json`.

Help is explicitly required (fixes spec-test alignment R02/R03/R08):

- Bare proxy with **no args** (`proxy` or with only `--config X` and no command) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- Unknown command must exit 2.

Commands:

```
proxy --config /app/config.json get-shard-id <key> -> int, exit 0
proxy --config /app/config.json get-shard-path <key> -> path, exit 0
proxy --config /app/config.json get <key> -> JSON value or "null", exit 0 even if null, no stdout for invalid config case
proxy --config /app/config.json set <key> <value_json> -> durable, exit 0
  value_json is JSON; if not valid JSON, treat as raw string value (store as string), exit 0
proxy --config /app/config.json delete <key> -> "true"/"false", exit 0
proxy --config /app/config.json list-keys -> sorted JSON array, deduped, sorted lexicographically, exit 0
proxy --config /app/config.json distribution -> JSON map shard_id (as string) -> count, includes all ids even zero, exit 0
```

Exit codes:
- 0 = success (including help and get returning null)
- 1 = I/O error (shard write failure etc.)
- 2 = unusable input: missing config file, invalid JSON config, duplicate shard id, empty path, negative id, id >= shard_count, missing shard_count, bad args, unknown command, missing <key> etc. For invalid config, **produce no stdout**, only stderr message.

### Sharding Algorithm (MUST)

Use MD5 for stability. Key's UTF-8 bytes are MD5 hashed, digest interpreted as big-endian unsigned integer mod shard_count. Must match Python: `int(hashlib.md5(key.encode()).hexdigest(),16) % shard_count`. Do not use CRC32/FNV/random.

### Persistence with Integrity Header (unique, not plain KV)

Shard file format is:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data"
}
```

- **Canonical JSON**: sorted keys, no spaces, must match Go `json.Marshal(data)` and Python `json.dumps(data, sort_keys=True, separators=(',', ':'))`. Then `checksum = md5_hex(canonical_json)`.
  - Example: empty shard canonical `{"data":{}}`? Actually empty data `{"data":{},"checksum":"..."}` where data's canonical for empty is `{}` → MD5 `99914b932bd37a50b983c5e7c90ae93b`. So empty shard file = `{"data":{},"checksum":"99914b932bd37a50b983c5e7c90ae93b"}` or similar depending on data length.
  - Python must use `json.dumps(data, sort_keys=True, separators=(',', ':'))` to match Go `json.Marshal`.
- On **write**: always new format with correct checksum, atomic temp file in same dir + `os.Rename`.
- On **read**:
  - If file missing/empty → empty `{}`
  - If file contains `data` field:
    - If `checksum` field missing or empty → treat as **corruption** (per feedback requirement)
    - Else compute expected checksum from `data` canonical JSON, compare to stored `checksum`. Mismatch → corruption.
    - Corruption handling: backup original to `<path>.corrupt.<nanosec>` (nanosec timestamp), log warning to stderr containing "corrupt" or "checksum", then recreate empty file with valid checksum and treat as empty.
  - If file has no `data` field (old flat format like `{}` or `{"a":1}`) → treat whole file as data dict for backward compat, convert to new format on next write. If invalid JSON → corruption handling same.
- `get` reads from disk each time.
- `list-keys` sorted, deduped.
- `distribution` includes all ids even zero.

### Config Validation (required, expanded coverage)

On startup, validate config, else exit 2 stderr, **no stdout**:
- shard_count >0
- len(shards)>0
- ids unique, non-negative, < shard_count
- path non-empty
- If config file missing or invalid JSON → exit 2, no stdout

Tests will include bad configs, corruption, raw-string, sorted exact, zero-count checks.

### Library shape (recommended)

```go
type Shard struct { ID int; Path string }
type Config struct { ShardCount int; Shards []Shard }
type ShardingProxy struct { ConfigPath string; Config Config }

func NewShardingProxy(configPath string) (*ShardingProxy, error)
func (p *ShardingProxy) GetShardID(key string) int
func (p *ShardingProxy) GetShardPath(key string) (string, error)
func (p *ShardingProxy) Get(key string) (interface{}, bool)
func (p *ShardingProxy) Set(key string, value interface{}) error
func (p *ShardingProxy) Delete(key string) (bool, error)
func (p *ShardingProxy) GetAllKeys() ([]string, error)
func (p *ShardingProxy) GetShardDistribution() (map[int]int, error)
```

### Constraints

- Go stdlib only (`go.mod` no external requires, and `go list -f '{{.Imports}}'` must not show external paths).
- Builds with `go build -o <binary> .`
- Respect `--config`.
- No hardcoded `/tmp/proxy` in solutions; use `/tmp/codimango` if tmp needed.

### Success Turn1

- Correct MD5 mod routing.
- Durable new-format files with valid checksum.
- Sorted list-keys, distribution including zeros.
- Config validation exit 2 with no stdout, corruption backup/recreate, raw-string handling, help flag and bare help exit 0.
- Legacy ignored.
