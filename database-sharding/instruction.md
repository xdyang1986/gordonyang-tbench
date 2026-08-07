# Database Sharding Proxy – Multi-Turn Hard Task

This is a **two-turn** Terminal-Bench task implementing a sharding proxy in Go with weighted routing, broadcast keys, checksum integrity, corruption handling, self-healing, ops.log, and legacy migration. The task is hard but solvable, with 27 and 73 tests (100 total after trimming Turn1).

## Overview

- **Turn 1 (1_step_one, 27 tests, trimmed from 45 to reduce warm-up budget):** Implement Go proxy at `/app/` (module `sharding`) with weighted MD5 sharding (weights 1,2,1,1 total 5), `global:` broadcast replication to all shards, checksum file format `{"data":...,"checksum":...}` without HTML escaping (sorted keys, no spaces, raw `<>&`), config validation exit 2 no stdout, corruption backup with warning, sorted `list-keys`, distribution including zeros, raw-string handling with explicit table (strongest discriminator – Go trap streaming vs full), transaction log `/app/data/ops.log` with version/shard_id/ts, atomic writes via temporary file then rename, self-healing set cleans wrong shards and delete cleans all shards, large 100KB value handling, help bare no-args containing version,checksum,staging, weight, global, ops.log. Turn1 is silent on empty-string edge – empty not tested explicitly to avoid ambiguous expectations. Trimmed from 45 pure warm-up to 27 discriminator-focused tests to avoid spending half trial budget on a turn nobody fails.

  Raw-string handling must be correct (do not use lenient streaming decode that ignores trailing bytes):
  - `123abc` → value is string "123abc", not number 123
  - `{"a":1} x` → string "{\"a\":1} x", not object
  - `nullx` → string "nullx", not null
  - `[1,2` → string "[1,2", not array (invalid JSON)
  - `  7   ` → number 7 (whitespace trimmed then valid)
  - `{"a":1}` → object

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 73 tests, dependencies [1_step_one], inherit_prior_session true):** Upgrade proxy to versioned integrity `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments on each set/delete/migration/replay, checksum without HTML escaping. On read/init validate every shard before any command: missing/empty → empty, version 0, correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version>=0 and shard_id==expected else corruption; checksum mismatch → corruption; old formats backward compat. Corruption → backup with timestamp + warning containing corrupt/checksum/shard_id/version, then recreate empty versioned.

  Proxy fallback to legacy `--legacy`, global broadcast (set all, get first-found id order, delete all, get-shard-id -1, get-shard-path comma-separated sorted), weighted routing same as Turn1 (totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order subtracting weight), list-keys union sorted deduped, distribution including zeros counting broadcast, ops.log with version, **ts-sorted replay**: replay entries in ascending ts order, stable for ties (abstract requirement – no v1/v2/100/50 walkthrough that mirrors test), raw-string handling same table as Turn1.

  Migration `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]`:
  - Read legacy old flat dict, missing→exit1, invalid JSON→exit1, empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay
  - Read shards all formats with corruption handling
  - Read ops.log line by line via bufio.Scanner, skip invalid JSON with warning, collect valid entries, then replay entries in ascending ts order, stable for ties (if ts missing treat as 0)
  - Detect duplicate non-global keys across shards → warning `found in multiple shards`
  - Cleanup wrong-shard and duplicate: only correct weighted shard retains non-global, global replicated to all
  - Weighted routing: totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order
  - Group legacy keys per shard for batched writes: normal weighted, global all shards
  - Batched atomic writes + staging two-phase: create staging dir `/app/data/staging` and write grouped files there first via atomic write (`staging/shard_<id>.json` with versioned format), then also write to final shard path atomically (two-phase). One atomic write per shard (grouped/batched) via temporary file then rename, version old+1 if changed, without HTML escaping, not per-key to ensure durability
  - Tombstone via ops.log replay ts-sorted with version bump
  - Version handling: version old+1, shard_id correct, checksum valid
  - Preserve vs force: preserve unless --force, with --force overwrite and log stderr `Overwriting key 'X' in shard Y`
  - Dry-run prints plan with total, per-shard, misplaced/dup/ops counts, no modifications, mentions cleanup, version, shard_id, checksum (no requirement to contain word "dry" – removed trivial substring check)
  - Backup tightening: legacy to backup path mkdir -p and each modified shard `.bak` before overwriting containing old data, and ops.log to `.bak` if exists
  - Large value 100KB handling, bad args unknown flag/arg → exit2, bad config duplicate/empty path/weight<=0/negative/id>=count → exit2 no stdout
  - Post-migration: new writes work, gets all legacy even if legacy removed, shard files versioned format valid, no wrong-shard non-global, global replicated, distribution zeros and broadcast counts, list-keys sorted exact.

  See full spec: `steps/2_step_two/instruction.md`

## Build

`go build -o <binary> .` in `/app/`, module `sharding`, Go stdlib only, no dot imports.

## Latest Validation

Pooled calibration (not single 10-trial gate read): Single 10-trial read on this task has ±20pp noise – e.g., two identical rounds gave avocado 8/10 and 4/10. Judge on pooled trials.

Current HEAD is **b1bce20 shipped: 73-test staging two-phase + ts-sorted** (opposite of stale README v5 67-test file-order/no-staging description). v5 was 67-test Turn2 file-order replay, no staging dir, which is opposite of what b1bce20 shipped.

After fixes in this patch:
- Turn1 trimmed from 45 to 27 discriminator-focused tests (removed pure warm-up redundancy: binary_builds, go_mod_exists, config existence, duplicate config variations, deterministic, trivial get/delete nonexistent, etc.) – keeps weighted, global, checksum, corruption, self-healing, large, distribution, raw-string full table (strongest discriminator), help, config validation representative.
- Turn2 73 tests: staging two-phase + ts-sorted replay (ascending ts, stable for ties), versioned integrity, fallback, duplicate cleanup, weighted, global, backup, etc. Removed worked examples at :80 and :92 that mirrored test (v1/v2/100/50 walkthrough) – now abstract "replay entries in ascending ts order, stable for ties".
- Deleted "dry" substring check in dry-run combined assertion (bugfix that raises gpt to ~19/20, making point 1 more urgent).
- task.toml steps[1] inherit_prior_session set to true so agent sees prior step terminal state.
- README updated to describe actual b1bce20 build (73 tests), not stale v5 67-test.

Current HEAD validation:
- Step1: 27/27 PASS trimmed
- Step2: 73/73 PASS with golden (staging + ts-sorted)
- Combined multi-turn OK with inherit_prior_session true

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

Implement at `/app/` – Turn2 reference solution includes Turn1 functionality and works standalone, with inherit_prior_session true.

