# codimango/database-sharding (Go)

## Overview
Multi-turn Go task simulating production incident: traffic outgrows single DB, team shards quickly but forgets legacy data.

Written in Go, tested via Python harness that builds binary with `go build`.

### Turn 1: Implement Database Sharding Proxy in Go
- Implement Go module at `/app/` (go.mod `module sharding` go 1.22) with CLI binary built via `go build -o /tmp/proxy .`
- CLI must support:
  - `proxy --config /app/config.json get-shard-id <key>` -> int
  - `get-shard-path <key>` -> path
  - `get <key>` -> JSON value or null
  - `set <key> <value_json>` -> durable
  - `delete <key>` -> true/false
  - `list-keys` -> JSON array
  - `distribution` -> JSON map shard_id->count
- Library: `ShardingProxy` struct with `NewShardingProxy`, `GetShardID` using MD5 big-endian % shard_count (matches Python md5 hex digest), `GetShardPath`, `Get`, `Set` (atomic write temp+rename), `Delete`, `GetAllKeys`, `GetShardDistribution`
- Config at `/app/config.json` 4 shards, data at `/app/data/shard_*.json`
- Tests: MD5 correctness, deterministic, persistence on disk, distribution, custom config, stdlib only.

### Turn 2: Handle Migration Properly
- Previous proxy breaks legacy reads (`/app/data/legacy.json`).
- Update proxy:
  - `Get` fallback to legacy file if shard miss (zero-downtime)
  - `list-keys` union shards+legacy
  - Support `--legacy` flag
- Implement migration subcommand:
  - `proxy --config X --legacy Y migrate [--dry-run] [--backup path] [--force]`
  - Group keys by MD5 mod, batched per-shard atomic writes, idempotent, backup, error handling, plan printing
- Tests: fallback, precedence, migration basic (30 keys), large 1000 keys, idempotent double-run, dry-run no-op, backup, missing legacy fails, preserves existing shard data, atomic no corrupt, new writes after migration, correct hashing.

### Environment
- Go 1.22 (golang:1.22-bookworm), `GOTOOLCHAIN=local`
- Initial: config.json, empty shards, legacy.json with 150 keys, skeleton main.go + go.mod

### Solutions
- `steps/1_step_one/solution/solve.sh`: writes full Turn1 Go main.go (proxy without fallback, migrate returns error)
- `steps/2_step_two/solution/solve.sh`: writes full Go proxy with fallback + migrate implementation (atomic, backup, dry-run, force)

### Why multi-turn Go?
- Turn1 agent implements naive sharding ignoring legacy.
- Turn2 forces recognition of production bug and proper migration tool with Go stdlib only (`crypto/md5`, `math/big`, `encoding/json`, `os`, etc.).
- `inherit_prior_session=true` ensures iteration.

