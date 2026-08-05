# Database Sharding Proxy – Multi-Turn Hard Task

This is a **two-turn** Terminal-Bench task implementing a sharding proxy in Go with weighted routing, broadcast keys, checksum integrity, corruption handling, self-healing, ops.log, and legacy migration. The task is hard but solvable, with 45 and 67 tests.

## Overview

- **Turn 1 (1_step_one, 45 tests):** Implement Go proxy at `/app/` (module `sharding`) with weighted MD5 sharding (weights 1,2,1,1 total 5), `global:` broadcast replication to all shards, checksum file format `{"data":...,"checksum":...}` without HTML escaping (sorted keys, no spaces, raw `<>&`), config validation exit 2 no stdout, corruption backup with warning, sorted `list-keys`, distribution including zeros, raw-string handling with explicit table, transaction log `/app/data/ops.log` with version/shard_id/ts, atomic writes via temporary file then rename, self-healing set cleans wrong shards and delete cleans all shards, large 100KB value handling, help bare no-args containing version,checksum,staging, weight, global, ops.log. Turn1 is silent on empty-string edge – empty not tested explicitly to avoid ambiguous expectations.

  Example raw-string handling that must be correct (do not use lenient streaming decode that ignores trailing bytes):
  - `123abc` → value is string "123abc", not number 123
  - `{"a":1} x` → string "{\"a\":1} x", not object
  - `nullx` → string "nullx", not null
  - `[1,2` → string "[1,2", not array (invalid JSON)
  - `  7   ` → number 7 (whitespace trimmed then valid)
  - `{"a":1}` → object

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 67 tests, dependencies [1_step_one], inherit_prior_session false – fixed to avoid oracle resume bug `Agent 'oracle' does not support resume`):** Upgrade proxy to versioned integrity `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments on each set/delete/migration/replay, checksum without HTML escaping. On read/init validate every shard before any command: missing/empty → empty, version 0, correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version>=0 and shard_id==expected else corruption; checksum mismatch → corruption; old formats backward compat. Corruption → backup with timestamp + warning containing corrupt/checksum/shard_id/version, then recreate empty versioned.

  Proxy fallback to legacy `--legacy`, global broadcast (set all, get first-found id order, delete all, get-shard-id -1, get-shard-path comma-separated sorted), weighted routing same as Turn1 (totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order subtracting weight), list-keys union sorted deduped, distribution including zeros counting broadcast, ops.log with version, file-order replay (no timestamp sort for 67 easier version), raw-string handling same table as Turn1.

  Migration `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]`:
  - Read legacy old flat dict, missing→exit1, invalid JSON→exit1, empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay
  - Read shards all formats with corruption handling
  - Read ops.log line by line, skip invalid JSON with warning containing corrupt/invalid/warning, collect valid entries in file order, handling corrupted lines and large lines
  - Detect duplicate non-global keys across shards → warning `found in multiple shards`
  - Cleanup wrong-shard and duplicate: only correct weighted shard retains non-global, global replicated to all
  - Weighted routing: totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order
  - Group legacy keys per shard for batched writes: normal weighted, global all shards
  - Batched atomic writes: one atomic write per shard (grouped/batched) via temporary file then rename, version old+1 if changed, without HTML escaping, not per-key to ensure durability
  - Tombstone via ops.log replay file order with version bump
  - Version handling: version old+1, shard_id correct, checksum valid
  - Preserve vs force: preserve unless --force, with --force overwrite and log stderr `Overwriting key 'X' in shard Y`
  - Dry-run prints plan with total, per-shard, misplaced/dup/ops counts, no modifications, mentions cleanup, version, shard_id, checksum
  - Backup tightening: legacy to backup path mkdir -p and each modified shard `.bak` before overwriting containing old data, and ops.log to `.bak` if exists
  - Large value 100KB handling, bad args unknown flag/arg → exit2, bad config duplicate/empty path/weight<=0/negative/id>=count → exit2 no stdout
  - Post-migration: new writes work, gets all legacy even if legacy removed, shard files versioned format valid, no wrong-shard non-global, global replicated, distribution zeros and broadcast counts, list-keys sorted exact.

  See full spec: `steps/2_step_two/instruction.md`

## Build

`go build -o <binary> .` in `/app/`, module `sharding`, Go stdlib only, no dot imports.

## Latest Validation

Commit `c7a0ec40` (73 tests, 42/73) before fix: Oracle 3/3 100%, Opus 9/10 90% mean 0.95, Avocado 4/10 40%, Codex 4/10 40%. Turn1 gate.

Current HEAD is **v5 67 tests Turn2 + 45 tests Turn1 = 112 total** after removing empty ambiguous (silent) and easing Step2 from 83 too-hard (staging/self-healing/large buffer/ts-sorted) to 62 too-easy then 67 balanced (file-order, no staging/self-healing for set/delete, but with moderate version exact, backup content, distribution global after migration):
- Turn1 45 tests: weighted, global, checksum, corruption, self-healing, large 100KB, raw-string table (best discriminator, Go trap streaming vs full), unknown command exit 2, list-keys empty after deleting all, migration cluster – all spec-derivable, no Go API names, ~40 tests per review, inference not implementation
- Turn2 67: versioned integrity, file-order replay, no staging, no self-healing set/delete, no large buffer, but keeps versioned, fallback, duplicate cleanup, weighted, global, backup, etc.

Current HEAD has been validated locally:
- Step1: **45/45 PASS** with golden solution (weighted, global, self-healing, large value, empty silent, no Go API names)
- Step2: **67/67 PASS** with golden solution (versioned, file-order, no staging/self-healing/large buffer/ts-sorted tricky)
- Combined multi-turn OK

## Commands

```bash
go build -o ./proxy .
./proxy --config /app/config.json get-shard-id mykey
./proxy --config /app/config.json set mykey '{"a":1}'
./proxy --config /app/config.json get mykey
./proxy --config /app/config.json set global:cfg '{"val":1}'
./proxy --config /app/config.json get global:cfg
./proxy --config /app/config.json list-keys
./proxy --config /app/config.json distribution
./proxy --config /app/config.json ops-log
./proxy --help
./proxy migrate --help
./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn2 reference solution includes Turn1 functionality and works standalone (inherit_prior_session false to fix oracle `does not support resume` – previously true caused 0/3 oracle fail).
