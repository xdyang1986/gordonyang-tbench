# Database Sharding Proxy – Multi-Turn Hard Task

This is a **two-turn** Terminal-Bench task implementing a sharding proxy in Go with weighted routing, broadcast keys, checksum integrity, corruption handling, self-healing, ops.log, and legacy migration.

## Overview

- **Turn 1 (1_step_one, 80 tests, inherits none, harder after removing empty ambiguous, 42→71→80 to fix too easy):** Implement Go proxy at `/app/` (module `sharding`) with weighted MD5 sharding (weights 1,2,1,1 total 5), `global:` broadcast replication to all shards, checksum file format `{"data":...,"checksum":...}` without HTML escaping (`SetEscapeHTML(false)`), config validation exit 2 no stdout, corruption backup `.corrupt.<nanosec>`, sorted `list-keys`, distribution including zeros, raw-string handling, transaction log `/app/data/ops.log` with version/shard_id/ts, atomic writes via `CreateTemp`+`Rename`, self-healing set cleans wrong shards and delete cleans all shards, large 100KB value handling, help bare no-args containing version,checksum,staging. Turn1 is silent on empty-string edge to avoid Oracle-null ambiguity (no explicit test for `""`).

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 62 tests, dependencies [1_step_one], inherit_prior_session true, easier than 83-test too-hard version):** Upgrade proxy to versioned integrity `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments on each set/delete/migration/replay, checksum MD5 of canonical data JSON without HTML escaping. On read/init validate every shard: missing/empty → empty, version 0, correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version>=0 and shard_id==expected else corruption; checksum mismatch → corruption; old formats backward compat (Turn1 data+checksum valid, flat format). Corruption → backup `.corrupt.<nanosec>` + stderr warning containing corrupt/checksum/shard_id/version. Proxy fallback to legacy `--legacy`, global broadcast, weighted routing same as Turn1, list-keys union sorted deduped, distribution includes zeros counting broadcast, ops.log with version/shard_id/ts/version and big Scanner buffer 10MB, self-healing set/delete.

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

Current HEAD (v5 easier 62 tests for Turn2 + v4 80 tests for Turn1, silent on empty) has 80 and 62 tests locally passing 80/80 and 62/62, combined multi-turn OK. Turn1 42→71→80 adds self-healing multiple wrong, nested large json, float many decimals, distribution global after multiple sets/deletes, list-keys custom config weighted, special chars in key, ops.log multiple appends version increment, corruption backup contains old data, empty obj/array, zero/false, list-keys empty after deleting all, many keys same shard, get-shard-path single path, custom config missing weight defaults to 1, persistence zero/false, ops.log valid array, help atomic/createtemp, etc., to fix too easy after removing empty ambiguous (Opus 9/10 90% on 42 tests). Turn2 83→62 removes tricky staging/self-healing/large-buffer/ts-sorted etc. to make easier – file-order, no staging, no self-healing set/delete, no large buffer, help without staging/timestamp/ts – but keeps core versioned integrity, fallback, duplicate cleanup, weighted, global, backup tightening. 58 tests gave 8/10 Opus 80% mean 0.9 sweet spot, so 62 tests balanced.

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
