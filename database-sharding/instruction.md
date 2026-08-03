# Database Sharding Proxy – Multi-Turn Hard Task

This is a **two-turn** Terminal-Bench task implementing a sharding proxy in Go with weighted routing, broadcast keys, checksum integrity, corruption handling, self-healing, ops.log, and legacy migration.

## Overview

- **Turn 1 (1_step_one, 50 tests, inherits none):** Implement Go proxy at `/app/` (module `sharding`) with weighted MD5 sharding (weights 1,2,1,1 total 5), `global:` broadcast replication to all shards, checksum file format `{"data":...,"checksum":...}` without HTML escaping (`SetEscapeHTML(false)`), config validation exit 2 no stdout, corruption backup `.corrupt.<nanosec>`, sorted `list-keys`, distribution including zeros, raw-string handling, transaction log `/app/data/ops.log` with version/shard_id/ts, atomic writes via `CreateTemp`+`Rename`, self-healing set cleans wrong shards and delete cleans all shards, large 100KB value handling, help bare no-args containing version,checksum,staging. Turn1 is silent on empty-string edge to avoid Oracle-null ambiguity (no explicit test for `""`).

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 83 tests, dependencies [1_step_one], inherit_prior_session true):** Upgrade proxy to versioned integrity `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments on each set/delete/migration/replay, checksum MD5 of canonical data JSON without HTML escaping. On read/init validate every shard: missing/empty → empty, version 0, correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version>=0 and shard_id==expected else corruption; checksum mismatch → corruption; old formats backward compat (Turn1 data+checksum valid, flat format). Corruption → backup `.corrupt.<nanosec>` + stderr warning containing corrupt/checksum/shard_id/version. Proxy fallback to legacy `--legacy`, global broadcast, weighted routing same as Turn1, list-keys union sorted deduped, distribution includes zeros counting broadcast, ops.log with version/shard_id/ts/version and big Scanner buffer 10MB, self-healing set/delete.

  Migration subcommand `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]`:
  - Read legacy old flat, missing→exit1, invalid JSON→exit1, empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay
  - Read shards all formats with corruption handling
  - Read ops.log via `bufio.Scanner` 10MB buffer, skip invalid with warning, collect valid, **sort by ts ascending stable file-order tie-breaker** (later ts wins), replay set/delete to correct shards (weighted for normal, all for global) with version bump
  - Detect duplicate non-global keys across shards → warning `found in multiple shards`
  - Cleanup wrong-shard and duplicate: only correct weighted shard retains non-global, global replicated to all
  - Group legacy keys per shard weighted for normal, all for global, batched one-write-per-shard atomic via `CreateTemp`+`Rename`, version = old+1, source inspection checks `grouped`/`map[int]` and `SetEscapeHTML` and `staging`
  - Staging dir `/app/data/staging` two-phase: create staging dir, write grouped files to staging via atomicWrite then to final shards
  - Version handling: new format version old+1, shard_id correct, checksum valid no HTML escaping
  - Backup tightening: legacy to backup path mkdir -p, each modified shard `.bak` before overwriting, ops.log to `ops.log.bak` if exists, content check
  - Dry-run prints plan with total, per-shard, misplaced/dup/ops counts, mentions cleanup, version, shard_id, checksum, staging, timestamp
  - Bad args: unknown migrate flag/arg → exit2, bad config duplicate id/empty path/weight<=0/negative id/id>=count → exit2 no stdout
  - Large value 100KB handling, raw-string, HTML escaping, etc.

  See full spec: `steps/2_step_two/instruction.md`

## Build

`go build -o <binary> .` in `/app/`, module `sharding`, Go stdlib only, no dot imports.

## Latest Validation

Commit `c7a0ec40` (v3 73 tests) was last fully validated:
- Oracle 3/3 100% mean 1.0
- Opus 9/10 90% mean 0.95 (1 fail@Turn2)
- Avocado 4/10 40% mean 0.4 (6 fail@Turn1)
- Codex 4/10 40% mean 0.4 (6 fail@Turn1)

Current HEAD (v4 83 tests + v3 50 tests, Turn1 silent on empty) has 50 and 83 tests locally passing 50/50 and 83/83, combined multi-turn OK. It adds timestamp-sorted ops.log replay, staging two-phase, self-healing set/delete, large ops.log 100KB requiring big buffer, empty handling silent for Turn1 to fix ambiguous.

## Commands

```bash
go build -o ./proxy .
./proxy --config /app/config.json get-shard-id mykey
./proxy --config /app/config.json set mykey '{"a":1}'
./proxy --config /app/config.json get mykey
./proxy --config /app/config.json list-keys
./proxy --config /app/config.json distribution
./proxy --config /app/config.json ops-log
./proxy --help
./proxy migrate --help
./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn1 present via inherit for Turn2.
