# codimango/database-sharding (Go) - Multi-turn Sharding Proxy with Integrity

## Overview
Multi-turn Go task simulating production incident: traffic outgrows single DB, team shards quickly but forgets legacy data, plus new edge cases (corrupted shards, checksum integrity, duplicate keys across shards from buggy prior migration). Designed to mitigate standard-project-template memorization risk via unique integrity header and duplicate cleanup.

Written in Go, tested via Python harness that builds binary with `go build -o <binary> .`.

### Turn 1: Implement Database Sharding Proxy in Go (31 tests)
- Implement Go module at `/app/` (`module sharding` go 1.22) with CLI binary built via `go build -o <binary> .`
- CLI supports `--config` (default `/app/config.json`) and `--help`/`-h`/`help` which must print usage containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config` and exit 0. Unknown command → exit 2.
- Commands:
  - `get-shard-id <key>` → int, exit 0
  - `get-shard-path <key>` → path
  - `get <key>` → JSON value or `null`, exit 0
  - `set <key> <value_json>` → durable atomic; if value_json not valid JSON, store as raw string
  - `delete <key>` → `true`/`false`
  - `list-keys` → sorted JSON array, deduped
  - `distribution` → JSON map shard_id (string) → count, includes all ids even zero

- **Sharding algorithm (MUST)**: Use MD5 for stability, interpret 16-byte digest as big-endian unsigned integer mod shard_count, must match Python `int(md5(key.encode()).hexdigest(),16)%count`. No CRC32/FNV.

- **Persistence with integrity header (unique)**:
  - Shard file format: `{"data": {key: value, ...}, "checksum": "md5_hex_of_canonical_data_json"}`
  - Canonical data JSON: `json.Marshal(data)` (Go sorts map keys) → `checksum = md5_hex(canonical)`. Python equivalent must use `separators=(',',':')` to match Go (no spaces) + sorted keys.
  - Write: always new format with correct checksum, atomic temp+rename.
  - Read: supports both new format and old flat format `{key: value}` for backward compat. If file has `data` field, verify checksum; mismatch → corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum, treat as empty. If invalid JSON → same backup/recreate.
  - `get` reads from disk each time, no stale cache.
  - `list-keys` sorted, `distribution` includes zeros.

- **Config validation (exit 2)**:
  - shard_count>0, len(shards)>0, ids unique, non-negative, < shard_count, path non-empty. Missing config or invalid JSON → exit 2 stderr.

- Tests expanded per R06: config validation (duplicate id, empty path, negative id, missing count, invalid JSON, missing file, unknown command), corruption backup/recreate (invalid JSON + checksum mismatch), raw-string handling, exact sorted list-keys, zero-count distribution, custom config, stdlib-only go.mod, atomic no corruption with checksum verification, help flag.

### Turn 2: Handle Migration Properly (24 tests) – Integrity, Duplicates, Fallback, Help

- Previous proxy breaks legacy reads (`/app/data/legacy.json` old flat format without checksum).

- Update proxy:
  - `get` fallback to legacy file if shard miss (zero-downtime), `--legacy` flag default `/app/data/legacy.json`
  - `list-keys` union shards + legacy, deduped sorted
  - `distribution` counts only shards, includes zeros
  - Keep validation and corruption handling
  - Help: `proxy --help`, `-h`, `help` → exit 0 containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`; `migrate --help` → exit 0 containing `dry-run`, `backup`, `force`

- Migration subcommand (same binary):
  ```
  proxy --config X --legacy Y migrate [--dry-run] [--backup path] [--force]
  ```
  Harness tries both `--config X --legacy Y migrate` and `migrate --config X --legacy Y`

  Requirements beyond generic template:
  - Read legacy JSON dict (old flat), missing → exit 1 stderr, invalid JSON → exit 1, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, exit 0
  - **Detect inconsistent state**: scan all shards (both old flat and new checksum formats, with corruption backup handling). Build key→[shard ids]. If same key in multiple shards, log stderr `Warning: key "..." found in multiple shards [...]` and `Detected duplicate keys...`
  - **Cleanup wrong-shard duplicates**: Remove wrong-shard copies and leave only correct MD5-designated shard. For any key in shard where `hash != shard id`, remove from wrong shard and move to correct shard (if not already present). This fixes buggy prior migration. Regression test seeds same key in all shards and asserts only correct shard retains it after migration.
  - Group legacy keys by destination MD5 mod, batched atomic writes per shard, preserve existing unless `--force`
  - Force logging: with `--force`, when overwriting, stderr `Overwriting key 'X' in shard Y`
  - Dry-run: prints plan with total and per-shard counts, `Dry-run enabled, not writing...`, no modifications
  - Backup: with `--backup <path>`, copy legacy to path (mkdir -p) and each shard to `<shard>.bak` before overwriting. Test checks both legacy backup and shard `.bak` creation
  - Corrupted shard handling: if shard file invalid JSON or checksum mismatch, backup to `.corrupt.<timestamp>` then treat as empty and migrate into it successfully
  - Idempotent second run identical, no data loss after migration, new writes after migration work, correct hashing placement, checksum valid after migration

- Post-migration: shard files in new checksum format with valid checksum, no wrong-shard keys left.

- Exit codes: 0 success (help 0, get null 0), 1 I/O/missing legacy, 2 invalid config/args

### Environment (self-contained verifier per R09)

- `golang:1.22-bookworm`, `GOTOOLCHAIN=local`, `GOCACHE=/tmp/codimango/gocache`, `GOPATH=/tmp/codimango/gopath`
- Dockerfile installs pytest via `pip3 install pytest==8.4.1 pytest-json-ctrf==0.3.5 --break-system-packages` so `test.sh` does NOT run `apt-get update` or download `uv` installer during grading – it just runs `pytest` binary (fallback to `python3 -m pytest`)
- Initial: `config.json` 4 shards, empty shards in new checksum format, legacy 150 keys old flat format, skeleton `main.go` + `go.mod`
- All /tmp artifacts under `/tmp/codimango` to avoid hardcoded `/tmp/proxy` warning; solutions build to `./proxy_bin`

### Solutions

- `steps/1_step_one/solution/solve.sh`: full Go proxy with checksum integrity, validation exit 2, corruption backup with timestamp, sorted keys, raw-string handling, help
- `steps/2_step_two/solution/solve.sh`: proxy with legacy fallback + robust migrate (duplicate detection, misplaced key cleanup moving to correct shard, force logging, backup legacy+shards, dry-run, empty/invalid legacy, corrupted shard handling)

### Why mitigates HIGH memorization risk

- Standard template is `hash router + JSON KV + CLI + idempotent migrate` – each top-recall. This task adds **integrity header** (`data`+`checksum` with MD5 of canonical JSON), **corruption backup with timestamp**, **config validation with specific exit 2**, **sorted list-keys**, **duplicate across shards cleanup (remove wrong-shard copies)**, **misplaced key moving**, **force overwrite stderr logging**, **help containing specific words** – composition is non-routine, not just gluing top-N snippets.
- Hashing described conceptually, not handed as exact Go snippet, so agent must derive `big.Int.SetBytes` logic.
- Migration golden solution now **actually removes wrong-shard duplicates**, leaving only correct shard – regression test `test_migrate_cleans_duplicate_across_shards` asserts this.

Validation: **Turn1 31/31**, **Turn2 24/24** direct pytest and harness reward 1, docker builds, skeleton fails as expected.
