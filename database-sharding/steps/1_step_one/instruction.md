# Turn 1: Implement Database Sharding Proxy in Go (with integrity and validation)

## Background

Traffic has outgrown a single DB. Historical dump is in `/app/data/legacy.json` (old flat format), but we have 4 new shards provisioned:

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

We are focusing on new traffic first, but proxy must be robust against config errors and corrupted shard files.

## Task

Implement Go sharding proxy at `/app/` (`module sharding`). Built via `go build -o <binary> .`.

### CLI (MUST)

Global flag: `--config` default `/app/config.json`. Also support `--help` / `-h` printing usage containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config` and exit 0. Exit 2 for unknown command.

Commands:

```
proxy --config /app/config.json get-shard-id <key> -> int
proxy --config /app/config.json get-shard-path <key> -> path
proxy --config /app/config.json get <key> -> JSON value or "null", exit 0 even if null
proxy --config /app/config.json set <key> <value_json> -> durable, exit 0
  value_json is JSON; if not valid JSON, treat as raw string value (store as string)
proxy --config /app/config.json delete <key> -> "true"/"false", exit 0
proxy --config /app/config.json list-keys -> sorted JSON array, deduped, exit 0
proxy --config /app/config.json distribution -> JSON map shard_id (as string) -> count, includes all ids even zero, exit 0
```

Exit codes: 0 success, 1 I/O error, 2 unusable input (missing config, invalid JSON config, duplicate shard id, empty path, negative id, missing shard_count, bad args).

### Sharding Algorithm (MUST)

Use MD5 for stability. The key's UTF-8 bytes are MD5 hashed, the 16-byte digest interpreted as big-endian unsigned integer, then modulo shard_count. Must match Python's `int(md5(key.encode()).hexdigest(),16) % shard_count`. Using CRC32/FNV/randomized hash will fail.

### Persistence with Integrity Header (unique, not plain KV)

Shard file format is **not** just `{key: value}`. It is:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data"
}
```

- Canonical JSON for checksum: sort keys, e.g., Python `json.dumps(data, sort_keys=True)` or Go `json.Marshal(data)` (which sorts map keys). Then `checksum = md5_hex(canonical_json)`.
- Example: empty shard -> `{"data":{},"checksum":"99914b932bd37a50b983c5e7c90ae93b"}`
- On **write** (`set`/`delete`): always write new format with correct checksum, atomically via temp file in same dir + `os.Rename`.
- On **read** (`get`, `list-keys`, etc.):
  - If file missing or empty → treat as empty `{}`
  - If file contains `data` field → verify checksum: compute MD5 of `data` canonical JSON, compare to `checksum` field. If mismatch → corruption: backup original file to `<path>.corrupt.<nanosec>` (nanosec timestamp), log warning to stderr containing "corrupt" or "checksum", then recreate empty file with correct checksum and treat as empty.
  - If file does NOT contain `data` field (old flat format like `{}` or legacy style) → treat whole file as data dict for backward compat, and on next write convert to new format. If invalid JSON → treat as corruption same as above.
- `get` reads from disk each time (no stale cache).
- `list-keys` must be sorted lexicographically, `distribution` includes zeros.

### Config Validation (required)

On startup, validate config, else exit 2 stderr, no output:
- `shard_count` >0, `len(shards)`>0, ids unique, non-negative, < shard_count, path non-empty
- If config missing or invalid JSON → exit 2
- Tests will include bad configs to verify exit 2.

### Library shape (recommended)

```go
type Shard struct { ID int `json:"id"`; Path string `json:"path"` }
type Config struct { ShardCount int `json:"shard_count"`; Shards []Shard `json:"shards"` }
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

- Go stdlib only.
- Builds with `go build -o <binary> .`
- Respect `--config`.
- No hardcoded `/tmp/proxy` in solutions.

### Success Turn1

- Correct MD5 mod routing.
- Durable new-format shard files with valid checksum after set.
- Sorted list-keys, distribution includes zeros.
- Config validation exit 2, corruption backup handling.
- Raw-string set handling (invalid JSON value → stored as string).
- Help flag works.
- Legacy ignored.
