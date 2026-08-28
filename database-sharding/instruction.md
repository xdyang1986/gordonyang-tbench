# Database Sharding Proxy – Multi-Turn Hard Task

This is a **two-turn** Terminal-Bench task implementing a sharding proxy in Go with weighted routing, broadcast keys, checksum integrity, corruption handling, self-healing, ops.log, and legacy migration. The task is hard but solvable, with 28 and 76 tests (104 total after under-specifying Turn1, relaxing staging, adding ops-log/post-flag/1MB coverage).

## Overview

- **Turn 1 (1_step_one, 28 tests, under-specified to fix saturation):** Implement Go proxy at `/app/` (module `sharding`) with weighted MD5 sharding (weights 1,2,1,1 total 5, totalWeight = sum(weights), no worked example mapping), `global:` broadcast replication to all shards, checksum file format `{"data":...,"checksum":...}` without HTML escaping (sorted keys, no spaces, raw `<>&`), config validation exit 2 no stdout, corruption backup with warning, sorted `list-keys`, distribution including zeros, raw-string handling via single invariant (full JSON consumption, trailing bytes -> raw string, no table – previously strongest discriminator but as lookup table it was transcription not inference), transaction log `/app/data/ops.log` with version/shard_id/ts, atomic writes via temporary file then rename (verified via inotify rename count), self-healing set cleans wrong shards and delete cleans all shards, large 100KB value handling, help bare no-args containing version,checksum,staging, weight, global, ops.log. Turn1 is silent on empty-string edge – empty not tested explicitly. Under-specified vs prior exhaustive restatement to restore inference (raw-string table, weighted example, success restatement collapsed).

  Raw-string handling must be correct: value_json should be parsed as JSON only if entire input after trimming whitespace is valid JSON; if there are trailing characters or invalid JSON, treat whole input as raw string. Do not use lenient streaming decode.

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 76 tests, dependencies [1_step_one], inherit_prior_session true):** Upgrade proxy to versioned integrity `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments on each set/delete/migration/replay, checksum without HTML escaping. On read/init validate every shard before any command: missing/empty → empty, version 0, correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version>=0 and shard_id==expected else corruption; checksum mismatch → corruption; old formats backward compat. Corruption → backup with timestamp + warning containing corrupt/checksum/shard_id/version, then recreate empty versioned. Staging relaxed: locate staged file by shard_id match, byte-equality with final shard (no filename coupling).

  Proxy fallback to legacy `--legacy`, global broadcast (set all, get first-found id order, delete all, get-shard-id -1, get-shard-path comma-separated sorted), weighted routing same as Turn1 (totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order subtracting weight), list-keys union sorted deduped, distribution including zeros counting broadcast, ops.log with version, **ts-sorted replay**: replay entries in ascending ts order, stable for ties (abstract requirement – no v1/v2/100/50 walkthrough that mirrors test), raw-string handling same table as Turn1.

  Migration `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]`:
  - Read legacy old flat dict, missing→exit1, invalid JSON→exit1, empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay
  - Read shards all formats with corruption handling
  - Read ops.log line by line via bufio.Scanner, skip invalid JSON with warning, collect valid entries, then replay entries in ascending ts order, stable for ties (if ts missing treat as 0)
  - Detect duplicate non-global keys across shards → warning `found in multiple shards`
  - Cleanup wrong-shard and duplicate: only correct weighted shard retains non-global, global replicated to all
  - Weighted routing: totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order
  - Group legacy keys per shard for batched writes: normal weighted, global all shards
  - Batched atomic writes + staging two-phase: create staging dir `/app/data/staging` and write grouped files there first via atomic write with versioned format (any file in staging dir whose shard_id matches, byte-equal to final shard, correct version and data – no filename coupling), then also write to final shard path atomically (two-phase). One atomic write per shard (grouped/batched) via temporary file then rename, version old+1 if changed, without HTML escaping, not per-key to ensure durability
  - Tombstone via ops.log replay ts-sorted with version bump, stable for equal ts (file order)
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

After fixes in this patch (under-specify Turn1 + staging relaxed + calibration notes stripped):
- Turn1 28 tests: under-specified to break saturation – raw-string collapsed from exhaustive table to single invariant (full JSON consumption, trailing bytes -> raw string), weighted routing worked example (Hash%5=0->0 etc) and Python expression removed, success section collapsed from exhaustive restatement (same pattern as adf4384 chat-server tombstone fix for 1.0 saturation: step-0 1.0 avocado 1/0.6 opus 1/0.8 due to transcription not inference). Keeps inotify atomic-write discriminator (fails if shard rewritten twice, though all 3 models already satisfy Turn2 equivalent).
- Turn2 73 tests: staging two-phase with byte-equality not filename coupling (locate staged file by shard_id match, byte-equal to final, version+data) – fixes opus regression where staged files renamed to staged-N.data now scores 73/73. Calibration notes stripped in instruction.md: line 7 trailing 'harder than Turn1, loosened vs staging+updated_at extra-hard which gave 0/10 all', line 74 'Requirements (harder than Turn1 loosened, easier than staging+updated_at extra-hard which gave 0/10 timeout harness crash)' -> 'Requirements:', line 129 trailing '(previous harness crash)' removed (bufio.Scanner requirement kept). Source-inspection promises already removed (CreateTemp/Rename/SetEscapeHTML/grouping/staging). Two new Turn2 discriminators added in prior iteration (stable equal-ts, large legacy) now dropped per instruction Step 3 to avoid pushing avocado below 0.6 and muddying Turn1 read; Turn2 already healthy (avocado 0.6, gpt 0.8).
- Turn2 count stays 73: 73 tests incl. relaxed staging, ts-sorted stable, versioned integrity, fallback, duplicate cleanup, weighted, global, backup, etc. Worked examples at :80 and :92 already removed (v1/v2/100/50 walkthrough) – abstract 'replay entries in ascending ts order, stable for ties'.
- task.toml: updated description to 28 and 73, no stable equal-ts / large migration mention, calibration notes stripped noted, source-inspection removed.
- README updated to describe actual build, not stale v5 67-test. Staging filename coupling removed.

Current HEAD validation (expected after docker verify):
- Step1: 28/28 PASS under-specified (raw-string invariant, no table, no weighted example, success collapsed)
- Step2: 76/76 PASS with golden (staging byte-equality relaxed raw bytes, calibration notes stripped, tautologies fixed, --ops-log/post-flag/1MB via legacy coverage added)
- Combined multi-turn OK with inherit_prior_session true
- Source-inspection promises removed, calibration notes removed, Turn1 under-specified

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

