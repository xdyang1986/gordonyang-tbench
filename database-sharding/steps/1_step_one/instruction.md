# Turn 1: Implement Database Sharding Proxy in Go

## Background

Our service's traffic has outgrown a single database. All data currently lives in one JSON file (`/app/data/legacy.json` is the old monolithic dump, but the new system should use sharded storage). We have provisioned 4 database shards on disk:

- `/app/data/shard_0.json`
- `/app/data/shard_1.json`
- `/app/data/shard_2.json`
- `/app/data/shard_3.json`

The shard topology is defined in `/app/config.json`:

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

You need to implement a sharding proxy in Go that routes reads/writes to the correct shard.

We are focused on getting sharding working for **new traffic** first. Handling existing legacy data will be addressed in the next iteration.

## Task

Implement Go sharding proxy at `/app/` (module `sharding`). Your code will be built via `go build -o /tmp/proxy .` and tested via CLI.

### Required CLI Interface

Your binary must support:

```
proxy --config /app/config.json get-shard-id <key>
  -> prints integer shard id (0 <= id < shard_count), e.g., "2\n"

proxy --config /app/config.json get-shard-path <key>
  -> prints filesystem path of the shard that owns the key

proxy --config /app/config.json get <key>
  -> prints JSON-encoded value if found, or "null" if not found

proxy --config /app/config.json set <key> <value_json>
  -> value_json is a JSON string (e.g., '{"name":"Alice"}', '"hello"', '123')
  -> must persist into correct shard file

proxy --config /app/config.json delete <key>
  -> prints "true" if existed and deleted, "false" otherwise

proxy --config /app/config.json list-keys
  -> prints JSON array of all keys across shards, e.g., ["a","b"]

proxy --config /app/config.json distribution
  -> prints JSON object mapping shard_id (as string) -> count, e.g., {"0":5,"1":3,"2":2,"3":0}
```

### Library Requirement (for code quality)

In addition to CLI, implement a reusable type in Go (any file, `package main` or `package sharding` is okay as long as binary works):

```go
type Shard struct { ID int `json:"id"`; Path string `json:"path"` }
type Config struct { ShardCount int `json:"shard_count"`; Shards []Shard `json:"shards"` }

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

Tests will primarily drive CLI, but having this struct makes implementation cleaner.

### Sharding Algorithm (MUST)

- Use **MD5** hashing for stability:
  ```
  hash = md5(key)
  hashInt = big-endian integer of hash bytes (big.Int.SetBytes)
  shard_id = hashInt % shard_count
  ```
  In Go:
  ```go
  import (
    "crypto/md5"
    "math/big"
  )
  func hashKey(key string) int {
    h := md5.Sum([]byte(key))
    bi := new(big.Int).SetBytes(h[:])
    mod := new(big.Int).Mod(bi, big.NewInt(int64(shardCount)))
    return int(mod.Int64())
  }
  ```
  This matches Python's `int(md5(key.encode()).hexdigest(),16) % shard_count`.

### Persistence Requirements

- Each shard file is a JSON object `{key: value}`.
- On `NewShardingProxy`, ensure all shard files exist (create with `{}` if missing) and parent dirs exist.
- `set`/`delete` must be **durable**: after call returns, shard file on disk must contain update.
  - Use atomic write: write to temp file in same dir, then `os.Rename`.
  - Handle empty / missing shard file as `{}`.
- `get` reads current shard file from disk (no stale in-memory cache, or cache must be invalidated per operation).

### Constraints

- Go standard library only (no external deps). `go.mod` must have no external requires.
- Code placed under `/app/` must build via `go build -o /tmp/proxy .` (or with subpackage containing main).
- Binary must work with `/app/config.json` default but `--config` flag must allow custom path for testing.
- Ensure `go.mod` exists: `module sharding` `go 1.22`

### What success looks like (for this turn)

- CLI correctly routes using MD5 mod.
- New keys via `set` can be read via `get` and located in correct shard file on disk.
- `delete`, `list-keys`, `distribution` work.
- Existing legacy data at `/app/data/legacy.json` is **NOT** required yet (ignore in this turn).

