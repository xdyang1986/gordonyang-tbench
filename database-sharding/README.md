# codimango/database-sharding (Go) - Weighted Sharding Proxy with Broadcast and Robust Migration

## Description

This is a **two-turn** Terminal-Bench task with `inherit_prior_session=true`, 80 and 67 tests, hard but solvable, top-level `instruction.md` present.

**Turn 1 – 80 tests – Sharding Proxy with Integrity, Validation, Corruption Repair, Weighted Routing, Broadcast, Self-Healing, Ops Log, Large Value, Config Validation (Hard, silent on empty-string to avoid Oracle-null ambiguity, 42→80 to fix too easy after removing empty):**
Traffic outgrown single DB, 4 shards provisioned in `/app/config.json` with optional `weight` (default 1, weight<=0 if present → invalid config exit 2). Weighted MD5 `shard_id = MD5(key) big-endian % totalWeight` where totalWeight = sum(weights) iterating shards sorted by ID subtracting weight. Broadcast keys prefixed `global:` must be replicated to all shards (`get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted paths, `set` writes to all, `get` checks all in id order first-found, `delete` deletes from all). Shard files use checksum integrity `{"data":{...},"checksum":md5_hex(canonical)}` where canonical is sorted keys, no spaces, without HTML escaping: Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)`. Atomic writes via `os.CreateTemp` in same dir + `os.Rename` (source-inspected, plus `SetEscapeHTML`), corruption handling (invalid JSON, checksum mismatch, missing checksum → backup `<path>.corrupt.<nanosec>` + warning, recreate empty), sorted exact `list-keys` deduped, distribution including zero counts (4 keys, counts include broadcast), raw-string handling, transaction log `/app/data/ops.log` append-only with version/shard_id/ts, `ops-log` command skips invalid lines with warning using `bufio.Scanner` 10MB buffer (not `json.Decoder`). Self-healing: `set` for normal key after writing to correct weighted shard must clean duplicate/misplaced copies in other shards, `delete` for normal must delete from all shards where exists. Large value 100KB string via set/get atomically. Help bare no-args must print help containing `get-shard-id,set,get,delete,list-keys,distribution,config,global,weight,ops.log,version,checksum,staging` and exit 0. Config validation exit 2 no stdout for duplicate id, empty path, negative id, id>=count, weight<=0, missing shard_count, invalid json, missing file, unknown command, missing key arg, set missing value arg. **Empty handling for Turn1: silent on empty-string edge to avoid Oracle-null ambiguity – empty not tested explicitly.**

**Turn 2 – 67 tests – Robust Legacy Migration with Weighted, Broadcast, Fallback, Versioned Integrity, File-Order Replay (Balanced, 62→67 harder than 62 too easy, down from 70/83 too hard):**
Turn1 proxy breaks historical reads. Legacy file `/app/data/legacy.json` old flat format contains 120 users + 30 orders + 5 global configs. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys. `ops.log` contains recent sets/deletes.
- Versioned integrity: `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments by 1, checksum MD5 of canonical data JSON without HTML escaping (version and shard_id not in checksum). On read/init validate every shard before any command: missing/empty → empty version 0 correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version present valid and shard_id==expected else corruption; missing version or version<0 when shard_id present → corruption; Turn1 old format `{"data":...,"checksum":...}` without shard_id/version valid backward compat version 0; no data field → old flat format backward compat; corruption → backup `.corrupt.<nanosec>` + warning containing corrupt/checksum/shard_id/version.
- Fallback: `get` checks weighted designated shard first (weighted algorithm) then legacy fallback `--legacy`; `global:` checks all shards id order then legacy. `list-keys` union shards+legacy sorted deduped, `distribution` includes zeros counting broadcast keys, `set` for normal weighted, for `global:` all shards, `delete` all shards to clean duplicates, append to ops.log with version,ts,shard_id.
- Help: bare no-args must contain `get-shard-id,set,get,delete,list-keys,distribution,migrate,config,legacy,weight,global,ops.log,dry-run,backup,force,version,shard_id,checksum` exit 0; `migrate --help` must contain `dry-run,backup,force,version,shard_id` exit 0; unknown command/flag → exit 2, no stdout on invalid config.
- Migration: `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]` – read legacy old flat missing→exit1 invalid JSON→exit1 empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay. Read shards all formats with corruption handling, read ops.log via `bufio.Scanner` 10MB buffer skip invalid with warning, collect valid entries in file order (no ts sort for 62/67 easier version), detect duplicate non-global keys across shards → warning `found in multiple shards`, cleanup wrong-shard and duplicate only correct weighted retains non-global global replicated to all, group legacy keys per shard weighted for normal all for global, batched one-write-per-shard atomic via `CreateTemp`+`Rename` and `grouped`/`map[int]` and `SetEscapeHTML`, version old+1, preserve vs force overwrites logging Overwriting, dry-run prints plan with total, per-shard, misplaced/dup/ops counts and mentions cleanup, version, shard_id, checksum, backup tightening legacy to backup path mkdir -p and each modified shard `.bak` before overwriting and ops.log to `.bak` if exists, corrupted handling, large 100KB value, bad args unknown flag/arg → exit2, bad config → exit2 no stdout. **No tricky staging dir two-phase, no self-healing set/delete for normal beyond migration, no large ops.log 100KB buffer test requiring 10MB, no true ts-sorted later-ts wins (file-order easier), no nested backup mkdir-p tricky, no version exact increment tricky? Actually 67 includes version exact increment old+1 moderate, missing_count, distribution global after migration, backup ops.log.bak content check, file-order ts early – moderate not tricky.**

## Completion Rates

Last fully validated online – commit `c7a0ec40` (73 tests, 42/73, ts-sorted + staging + self-healing) before fix:

| Gate | Model | Full pass | Mean reward |
|------|-------|-----------|-------------|
| Oracle | oracle | 3/3 (100%) | 1.00 |
| Metacode | meta/avocado-5.14-code | 4/10 (40%) | 0.40 |
| Agent | claude-opus-4-8 | 9/10 (90%) | 0.95 |
| Codex | gpt-5.5 | 4/10 (40%) | 0.40 |

Turn1 was discriminator (6 fail@Turn1). Rerun at 21:48 same commit showed all-fail 0/10,1/10,2/10 due to one tricky help test `test_help_contains_timestamp_ts_and_version` requiring exact word `timestamp` (agent had `ts` but not `timestamp`) – 61 passed 1 failed → Turn1 reward 0. Fixed by removing timestamp requirement.

Current HEAD is **v5 67 tests Turn2 + 80 tests Turn1 = 147 total** after removing empty ambiguous (silent on empty) and easing Step2 from 83 too-hard (staging/self-healing/large buffer/ts-sorted) to 62 too-easy then 67 balanced:
- Turn1 80 tests: weighted, global, checksum, corruption, self-healing set/delete multiple wrong, large 100KB, nested large json, float many decimals, zero/false, empty obj/array, special chars, raw special, distribution zeros/global/after delete/many keys same shard/multiple global, list-keys sorted exact custom weighted/after set-delete/empty after deleting all, ops.log version/shard_id/ts big buffer
- Turn2 67 tests: versioned integrity, file-order replay (no ts-sort), no staging dir, no self-healing set/delete, no large ops.log buffer, but keeps versioned, fallback, duplicate/wrong-shard cleanup in migration, weighted, global, backup .bak existence and ops.log.bak content, version exact increment old+1, distribution global after migration, config missing count, file-order ts early, large 100KB, empty valid MD5 d41d8cd98f00b204e9800998ecf8427e7, unknown command/missing args

Current HEAD has been validated locally:
- Step1: **80/80 PASS** with golden solution (weighted, global, self-healing, large value, empty silent)
- Step2: **67/67 PASS** with golden solution (versioned, file-order, no staging/self-healing/large buffer/ts-sorted tricky)
- Combined multi-turn OK
- Oracle would pass 3/3, not too complex. Latest difficulty report for 80/67: total 147, passed 55, passRate 37% – Opus 55/147 37% in sweet spot challenging but solvable, Oracle 45/48 94% GOOD

## Model Analysis

From c7a0ec40 73-test run:
- Turn1 gate blocks weaker models ~60%
- Codex 4/10, Avocado 4/10, Opus 9/10, Oracle 3/3

v4 83-test version was too hard (Codex 1/10, plus help timestamp all-fail 0/10, staging/self-healing). v5 62-test easy version was too easy (Opus 8-10/10). v5 67-test balanced (5 moderate added) is harder than 62 but easier than 70/83.

## Anti-Cheating Analysis

- Hardcoded: Legacy random via python random no seed, tests use custom temp legacy files with brute-force keys for weighted indices, not Dockerfile random legacy. Checksum computed independently and compared to file's checksum field and raw text containing `<` not escaped. Ops.log has random ts nanosec and version.
- Overfitting: Tests use custom config paths with temp dirs and brute-force keys for weighted indices. List-keys sorted exactly, distribution includes all ids even zero. Raw-string test invalid JSON stored as string. HTML escaping test with `<>&` checks SetEscapeHTML false. Backup tightening requires each relevant shard `.bak` containing old data and legacy backup and ops.log.bak content check. Missing-checksum test corrupts actual hashed shard via MD5. Migrate bad config tests assert exit 2 stdout empty. Unknown flag/arg tests assert exit 2. Duplicate cleanup seeds same non-global key in all shards and asserts only correct weighted retains after migration with --force. Force overwrite checks stderr overwriting. Empty legacy still does cleanup. Stdlib-only inspects `go list` imports.
- Source inspection (CreateTemp/Rename, grouped/map[int], SetEscapeHTML) is intentional anti-reward-hacking to ensure batched atomic writes.
