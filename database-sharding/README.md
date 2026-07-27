# codimango/database-sharding (Go) - Multi-turn Sharding Proxy

## Overview
Multi-turn Go task simulating production incident: traffic outgrows single DB, team shards quickly but forgets legacy data, plus new edge cases (corrupted shards, duplicate keys across shards from buggy prior migration).

Written in Go, tested via Python harness that builds binary with `go build`.

### Turn 1: Implement Database Sharding Proxy in Go
- Implement Go module at `/app/` (go.mod `module sharding` go 1.22) with CLI binary built via `go build -o <binary> .`
- CLI must support `--config` flag and commands:
  - `get-shard-id <key>` -> int, exit 0
  - `get-shard-path <key>` -> path
  - `get <key>` -> JSON value or `null`
  - `set <key> <value_json>` -> durable atomic
  - `delete <key>` -> true/false
  - `list-keys` -> sorted JSON array
  - `distribution` -> JSON map including zeros
- Exit codes: 0 success, 1 I/O, 2 invalid config/input
- Library: `ShardingProxy` with validation:
  - Config validation: shard_count>0, ids unique, in [0,count), path non-empty, negative check → exit 2
  - MD5 hashing: digest as big-endian integer mod shard_count, must match Python `int(md5.hexdigest,16)%count`
  - Corruption handling: invalid JSON shard → backup to `<path>.corrupt.<timestamp>` + stderr warning + recreate `{}`
  - Atomic write via temp+rename, sorted keys, distribution includes zeros
- Tests: MD5 correctness, determinism, persistence, distribution, custom config, stdlib only, validation, corruption backup.

### Turn 2: Handle Migration Properly
- Previous proxy breaks legacy reads (`/app/data/legacy.json`).
- Update proxy:
  - `Get` fallback to legacy if shard miss (zero-downtime)
  - `list-keys` union shards+legacy sorted
  - Support `--legacy` flag
  - Keep corruption handling and validation
- Implement migration subcommand:
  - `proxy --config X --legacy Y migrate [--dry-run] [--backup path] [--force]`
  - Detect duplicate keys across shards (inconsistent state) → stderr warning
  - Group by MD5 mod, batched atomic per-shard writes, preserve unless --force (log overwriting to stderr)
  - Idempotent, backup legacy + shards as `.bak`, dry-run plan, empty legacy handling, no data loss
- Tests: fallback, precedence, migration basic 30 keys, large 1000 keys, idempotent, dry-run, backup, missing legacy fails, preserves shard data, atomic no corrupt, new writes after migration, correct hashing, duplicate detection, force logging.

### Environment
- Go 1.22 (`golang:1.22-bookworm`), `GOTOOLCHAIN=local`, `GOCACHE=/tmp/codimango/gocache`
- Initial: config.json (4 shards), empty shards, legacy.json 150 keys, skeleton main.go + go.mod
- All /tmp usage under `/tmp/codimango` to avoid structural check warnings, no hardcoded `/tmp/proxy` in solutions

### Solutions
- `steps/1_step_one/solution/solve.sh`: full Turn1 Go implementation with validation, corruption backup, sorted keys, atomic writes, builds to `./proxy_bin`
- `steps/2_step_two/solution/solve.sh`: full proxy with fallback + robust migrate (duplicate detection, force logging, backup, dry-run)

### Why multi-turn Go? Mitigating Memorization Risk
- Turn1 alone is template CRUD, but added unique requirements (config validation exit 2, corruption handling with timestamped backup, sorted list-keys, distribution includes zeros, atomic rename) make it non-trivial.
- Turn2 adds non-standard incident: duplicate keys across shards detection, force overwrite logging, empty legacy handling, backup of both legacy and shards, union sorted keys – not top-N memorizable glue.
- Hashing described conceptually (MD5 big-endian mod) without handing exact Go snippet, so agent must derive big.Int logic, not copy-paste.
- `inherit_prior_session=true` forces iteration, not rewrite.

Validation: direct pytest 20/20 Turn1, 16/16 Turn2, harness reward 1 both, docker build succeeds.
