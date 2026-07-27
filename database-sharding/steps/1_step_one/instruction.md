# Turn 1: Implement Database Sharding Proxy in Go

## Background

Our service's traffic has outgrown a single database. Historical data lives in `/app/data/legacy.json`, but we have provisioned 4 new shards on disk for the new path:

- `/app/data/shard_0.json`
- `/app/data/shard_1.json`
- `/app/data/shard_2.json`
- `/app/data/shard_3.json`

Topology is defined in `/app/config.json`:
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

We are focused on new traffic first. Legacy handling will be addressed next iteration, but the proxy must be robust.

## Task

Implement Go sharding proxy at `/app/` (module `sharding`). Your code will be built via `go build -o <binary> .` from `/app`.

### Required CLI Interface

Your binary must support `--config` flag (default `/app/config.json`) and commands:

```
proxy --config /app/config.json get-shard-id <key>
  -> prints integer shard id, exit 0

proxy --config /app/config.json get-shard-path <key>
  -> prints path, exit 0

proxy --config /app/config.json get <key>
  -> prints JSON-encoded value if found
  -> prints "null" if not found, exit 0

proxy --config /app/config.json set <key> <value_json>
  -> value_json is JSON (e.g., '{"name":"Alice"}', '"hello"', '123')
  -> if value_json is not valid JSON, treat as raw string value
  -> exit 0 on success, non-zero on unusable config/shard IO

proxy --config /app/config.json delete <key>
  -> prints "true" if existed, "false" otherwise, exit 0

proxy --config /app/config.json list-keys
  -> prints JSON array of all keys across shards, sorted lexicographically for determinism, exit 0

proxy --config /app/config.json distribution
  -> prints JSON object mapping shard_id -> count, includes all shard ids even if 0, e.g., {"0":5,"1":3,"2":2,"3":0}, exit 0

Exit codes:
  0 = success
  1 = I/O error
  2 = unusable input: missing config, invalid JSON config, duplicate shard ids, empty path, negative id, missing shard_count, unreadable args
```

### Sharding Algorithm (MUST)

- Use MD5 hashing for stability. Compute MD5 of UTF-8 key, interpret the 16-byte digest as a big-endian unsigned integer, then `shard_id = integer mod shard_count`.
- This must match Python's behavior: `int(hashlib.md5(key.encode()).hexdigest(),16) % shard_count`.
- The conversion must be big-endian; using CRC32, FNV, or random hash will fail.

### Persistence & Robustness (unique to this task)

- Each shard file is a JSON object `{key: value}`.
- On startup, ensure all shard files exist (create `{}` if missing) and parent dirs exist. Validate config:
  - `shard_count` >0, ids in `[0, shard_count-1]` unique, path non-empty, no negative ids. If invalid, exit 2 with stderr.
  - If config missing or invalid JSON, exit 2.
- `set`/`delete` must be durable and atomic: write to temp file in same directory then `os.Rename`. Handle empty/missing shard as `{}`.
- If shard file contains invalid JSON (corruption), treat as empty but backup to `<path>.corrupt.<timestamp>` and log warning to stderr, then recreate `{}`.
- `list-keys` must deduplicate and sort.
- `distribution` must include all shard ids even zero, sum equals total keys.

### Library shape (recommended)

```go
type Shard struct { ID int; Path string }
type Config struct { ShardCount int; Shards []Shard }

type ShardingProxy struct { ... }
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

- Go standard library only.
- Code under `/app/` builds with `go build -o <binary> .`
- Must respect `--config` for custom configs.
- Do not hardcode `/tmp/proxy` binary path.

### Success

- Correct MD5 mod routing.
- Durable correct shard file placement.
- Sorted list-keys, distribution including zeros.
- Config validation exit 2, corruption backup handling.
- Legacy ignored for now.
