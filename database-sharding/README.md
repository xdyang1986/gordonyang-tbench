# codimango/database-sharding (Go) - Weighted Sharding Proxy with Broadcast and Robust Migration

## Description

This is a **two-turn** Terminal-Bench task simulating a real production incident where a single DB is outgrown and a quick sharding attempt forgets existing data.

**Turn 1 – 50 tests – Sharding Proxy with Integrity, Validation, Corruption Repair, Weighted Routing, Broadcast, Self-Healing, Ops Log, Large Value (Hard, silent on empty-string to avoid Oracle-null ambiguity):**
Traffic outgrown single DB, 4 shards provisioned in `/app/config.json` with optional `weight` (default 1 if missing, weight<=0 if present → invalid config exit 2). For Turn1, use **weighted MD5** `shard_id = MD5(key) big-endian % totalWeight` where totalWeight = sum(weights) iterating shards sorted by ID subtracting weight (not simple mod for hard). Special **broadcast keys** prefixed `global:` must be replicated to **all shards** (`get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted paths, `set` writes to all, `get` checks all in id order first-found, `delete` deletes from all). Shard files use **checksum integrity format** `{"data":{...},"checksum":md5_hex(canonical)}` where canonical is **sorted keys, no spaces, without HTML escaping**: Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)` disabled. Atomic writes via `os.CreateTemp` in same dir + `os.Rename` (source-inspected, plus `SetEscapeHTML`), corruption handling (invalid JSON, checksum mismatch, **missing checksum** → backup `<path>.corrupt.<nanosec>` + stderr warning containing corrupt/checksum, recreate empty), sorted exact `list-keys` (deduplicated lexicographically, reads all shards triggers repair of every shard), distribution including zero counts (explicit 4 keys, counts include broadcast keys), raw-string handling (invalid JSON value stored as string), transaction log `/app/data/ops.log` append-only with version/shard_id/ts, `ops-log` command skips invalid lines with warning using `bufio.Scanner` with 10MB buffer (not `json.Decoder` which can infinite loop). **Self-healing**: `set` for normal key after writing to correct weighted shard must clean duplicate/misplaced copies in other shards, `delete` for normal must delete from all shards where exists. **Large value**: 100KB string via set/get atomically. **Help**: bare proxy with no args must print help containing `get-shard-id,set,get,delete,list-keys,distribution,config,global,weight,ops.log,version,checksum,staging` and exit 0, as must `--help`/`-h`/`help`; unknown command → exit 2. Config validation exit 2 no stdout for duplicate id, empty path, negative id, id>=count, weight<=0, missing shard_count, invalid json, missing file. **Empty string handling for Turn1: silent on empty-string edge to avoid Oracle-null ambiguity – empty `""` is NOT tested explicitly** (no test for `""` as valid nor invalid), so implementations may treat it as valid hash or invalid exit 2 and still pass Turn1.

**Turn 2 – 83 tests – Robust Legacy Migration with Weighted, Broadcast, Fallback, Versioned Integrity, Timestamp-Sorted Replay, Staging, Self-Healing:**
Turn1 proxy breaks historical reads. Legacy file `/app/data/legacy.json` old flat format (no checksum) contains 120 users + 30 orders + 5 `global:` configs. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys. `ops.log` contains recent sets/deletes that must win over legacy with timestamp-sorted replay.

- **Versioned integrity**: Shard file format upgraded to `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments by 1 on each successful set/delete/migration/replay, checksum MD5 of canonical data JSON without HTML escaping (version and shard_id not in checksum). On read/init, validate every shard before any command: missing/empty → empty `{}`, version 0, correct shard_id; invalid JSON → corruption; missing checksum → corruption; has shard_id present → require checksum and version present and valid and shard_id==expected else corruption; missing version or version<0 when shard_id present → corruption; Turn1 old format `{"data":...,"checksum":...}` without shard_id/version valid backward compat (version default 0, shard_id from config); no data field → old flat format backward compat; corruption → backup `.corrupt.<nanosec>` + warning containing corrupt/checksum/shard_id/version.
- **Fallback**: `get` checks weighted designated shard first (weighted algorithm totalWeight sum weights, hash big-endian int mod totalWeight iterate id order subtracting weight; `global:` → -1) then legacy fallback `--legacy`; `global:` checks all shards id order then legacy. `list-keys` union shards+legacy sorted deduped, `distribution` includes zeros counting broadcast keys, `set` for normal weighted + self-healing clean wrong shards, for `global:` all shards, `delete` similarly cleans all shards for normal to remove duplicates, all shards for global, append to ops.log with version,ts,shard_id, `ops-log` prints JSON array skipping invalid lines with warning using `bufio.Scanner` 10MB buffer.
- **Help**: bare no-args must contain `get-shard-id,set,get,delete,list-keys,distribution,migrate,config,legacy,weight,global,ops.log,dry-run,backup,force,version,shard_id,checksum,staging,timestamp,ts` exit 0; `migrate --help` must contain `dry-run,backup,force,version,shard_id,staging,timestamp` exit 0; unknown command/flag → exit 2, no stdout on invalid config.
- **Migration**: `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]`
  - Read legacy old flat dict, missing→exit1, invalid JSON→exit1, empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay
  - Read shards all formats with corruption handling
  - Read ops.log via `bufio.Scanner` 10MB buffer, skip invalid with warning, collect valid, **sort by ts ascending stable file-order tie-breaker** (later ts wins), replay set/delete to correct shards weighted for normal all for global with version bump (tombstone)
  - Detect duplicate non-global keys across shards → warning `found in multiple shards`
  - Cleanup wrong-shard and duplicate: only correct weighted shard retains non-global, global replicated to all shards
  - Group legacy keys per shard weighted for normal all for global, batched one-write-per-shard atomic via `CreateTemp`+`Rename` and `grouped`/`map[int]` and `SetEscapeHTML` and `staging` source inspection
  - Staging dir `/app/data/staging` two-phase: create staging dir, write grouped files to staging via atomicWrite then to final shards
  - Version handling: version old+1 if changed, shard_id correct, checksum valid no HTML escaping
  - Backup tightening: legacy to backup path mkdir -p, each modified shard `.bak` before overwriting containing old data, ops.log to `ops.log.bak` if exists with content check, nested dir mkdir -p, duplicate-warning stderr
  - Dry-run prints plan with total, per-shard, misplaced/dup/ops counts, no modifications, mentions cleanup, version, shard_id, checksum, staging, timestamp
  - Bad args: unknown migrate flag/arg → exit2, bad config duplicate id/empty path/weight<=0/negative id/id>=count → exit2 no stdout
  - Large value 100KB handling, large ops.log replay 100KB requiring big buffer, float many decimals, etc.
- Post-migration: new writes work, gets all legacy even if legacy removed, shard files versioned format valid, no wrong-shard non-global, global replicated to all shards, distribution zeros and broadcast counts.

## Completion Rates

Latest fully validated online — commit `c7a0ec40` (v3 hard, 73 tests, ts-sorted + staging + self-healing):

| Gate | Model | Full pass | Mean reward |
|------|-------|-----------|-------------|
| Oracle | oracle | 3/3 (100%) | 1.00 |
| Metacode | meta/avocado-5.14-code | 4/10 (40%) | 0.40 |
| Agent | claude-opus-4-8 | 9/10 (90%) | 0.95 |
| Codex | gpt-5.5 | 4/10 (40%) | 0.40 |

Turn1 was the discriminator (6 fail@Turn1 for avocado and codex). Current HEAD is **v4 83 tests for Turn2 + 50 tests for Turn1 after removing empty ambiguous (silent on empty)**:
- Turn1 50 tests: weighted, global, checksum, corruption, self-healing set/delete cleans duplicates, large 100KB, distribution zeros+global, list-keys sorted with global, ops.log version/shard_id/ts big buffer, help with version,checksum,staging, no empty-string test
- Turn2 83 tests: versioned integrity shard_id+version, ts-sorted replay later-ts wins, staging two-phase, self-healing, large ops.log 100KB big buffer, empty in list-keys sorted first for step2 valid MD5 d41d8cd98f00b204e9800998ecf8427e7, etc.

Current HEAD has been validated locally:
- Step1: **50/50 PASS** with golden solution (weighted, global, self-healing, large value, empty silent)
- Step2: **83/83 PASS** with golden solution (versioned, ts-sorted, staging, self-healing, large ops.log)
- Combined multi-turn OK

The v4 83-test version adds material hardening (43→83 tests, staging, ts-sorted, self-healing, large ops.log) over v3 73 tests, expected to bring Opus down from 9/10 toward 4-5/10 target for hard.

## Model Analysis

From c7a0ec40 run:
- Turn1 gate blocks weaker models ~60% (weighted routing, checksum without HTML escaping, atomic writes, corruption repair)
- Codex 4/10 mean 0.40: 4 full pass, 6 fail@Turn1
- Avocado 4/10 mean 0.40: same shape
- Opus 9/10 mean 0.95: clears Turn1, 1 fail@Turn2
- Oracle 3/3 mean 1.00

v4 83 tests adds timestamp-sorted ops.log replay out-of-order (later ts must win) and staging dir and self-healing set/delete and large ops.log buffer (100KB line requires 10MB Scanner buffer, default 64KB fails) which are known hard for Opus.

## Anti-Cheating Analysis

- Hardcoded outputs: Legacy random via python `random.randint` no seed, tests use custom temp legacy files with deterministic keys via brute-force for weighted indices, not Dockerfile random legacy. Shard checksum computed independently and compared to file's checksum field and raw text containing `<` not escaped. Ops.log has random ts nanosec and version.
- Overfitting: Tests use custom config paths with random temp dirs and brute-force keys for weighted indices. List-keys must be sorted exactly, distribution must include all ids even zero. Raw-string test invalid JSON stored as string. HTML escaping test with `<>&` checks SetEscapeHTML false. Backup tightening requires each relevant shard `.bak` containing old data and legacy backup and ops.log.bak content check and duplicate-warning stderr. Missing-checksum test corrupts actual hashed shard via MD5, not fixed shard. Migrate bad config tests assert exit 2 and stdout empty. Unknown flag/arg tests assert exit 2. Corrupted shard migration tests seed corrupted file then migrate assert backup `.corrupt.` created and migration still succeeds. Duplicate cleanup seeds same non-global key in all shards and asserts only correct weighted shard retains after migration with --force. Force overwrite checks stderr overwriting. Empty legacy still does cleanup. Stdlib-only inspects `go list` imports for dot imports. Self-healing set/delete tests seed duplicate in wrong shard and assert cleaned after set/delete. Staging tests assert `/app/data/staging` exists versioned and files match final. Timestamp-sorted replay test creates out-of-order file vs ts and asserts later ts wins. Large ops.log replay 100KB test requires big Scanner buffer.
- Modifying test files: tests mounted at /tests self-contained pytest baked via Dockerfile, reward written even if pytest fails via `set +e`, CTRF JSON shows collected count. Source inspection checks CreateTemp+Rename+SetEscapeHTML+grouped/map[int]+staging verifies code shape. Verifier self-contained cannot be broken by removing internet.
- Bypassing: Intended Go ShardingProxy weighted with MD5 big-endian, broadcast global, checksum without HTML escaping, atomic writes, corruption backup, fallback to legacy, migration duplicate/wrong-shard cleanup only correct weighted retains non-global global replicated to all, ops.log ts-sorted replay tombstone, help explicit, corruption init repairs every shard, missing checksum treated as corruption, versioned format shard_id+version, staging two-phase, self-healing.
