# codimango/database-sharding (Go) - Weighted Sharding Proxy with Broadcast and Robust Migration

## Description

This is a **two-turn** Terminal-Bench task with `inherit_prior_session=true`, 27 and 73 tests (100 total after trim), hard but solvable, top-level `instruction.md` present.

**Turn 1 – 27 tests (trimmed from 45 pure warm-up) – Sharding Proxy with Integrity, Validation, Corruption Repair, Weighted Routing, Broadcast, Self-Healing, Ops Log, Large Value:**

Traffic outgrown single DB, 4 shards provisioned in `/app/config.json` with optional `weight` (default 1, weight<=0 if present → invalid config exit 2). Weighted MD5 `shard_id = MD5(key) big-endian % totalWeight` where totalWeight = sum(weights) iterating shards sorted by ID subtracting weight (not simple mod). Broadcast keys prefixed `global:` must be replicated to all shards (`get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted paths, `set` writes to all, `get` checks all in id order first-found, `delete` deletes from all). Shard files use checksum integrity `{"data":{...},"checksum":md5_hex(canonical)}` where canonical is sorted keys, no spaces, without HTML escaping. Atomic writes via temporary file in same directory then rename to final path, without HTML escaping. Corruption handling (invalid JSON, checksum mismatch, missing checksum → backup `<path>.corrupt.<nanosec>` + warning, recreate empty), sorted exact `list-keys` deduped, distribution including zero counts (4 keys, counts include broadcast), raw-string handling with full table as strongest discriminator: value_json must be valid JSON fully consumed; if not valid JSON, treat as raw string (do not use lenient streaming decode). Full table: `123abc`→string "123abc" not number 123, `{"a":1} x`→string, `nullx`→string, `[1,2`→string, `  7   `→number 7, `{"a":1}`→object. Transaction log `/app/data/ops.log` append-only with version/shard_id/ts, `ops-log` skips invalid with warning, handles large lines and avoids infinite loop. Self-healing set cleans wrong shard, delete cleans all. Large 100KB. Help bare no-args must contain `get-shard-id,set,get,delete,list-keys,distribution,config,global,weight,ops.log,version,checksum,staging` exit 0. Trimmed from 45 to 27 to avoid spending half trial budget on warm-up that nobody fails; kept genuine discriminators (weighted, global, checksum no-escape, corruption, self-healing, raw-string table). Empty-string silent (not tested in Turn1 to avoid ambiguous expectations, tested in Turn2).

**Turn 2 – 73 tests – Robust Legacy Migration with Weighted, Broadcast, Fallback, Versioned Integrity, Staging Two-Phase, TS-Sorted Replay:**

Turn1 proxy breaks historical reads. Legacy file `/app/data/legacy.json` old flat format contains 120 users + 30 orders + 5 global configs. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys.

- Versioned integrity: `{"shard_id":id,"version":ver,"data":...,"checksum":...}` where shard_id must match expected id, version increments by 1, checksum MD5 of canonical data JSON without HTML escaping (version and shard_id not in checksum). On read/init validate every shard before any command: missing/empty → empty version 0 correct shard_id; invalid JSON → corruption; missing checksum → corruption; shard_id present → require checksum and version present valid and shard_id==expected else corruption; missing version or version<0 when shard_id present → corruption; Turn1 old format `{"data":...,"checksum":...}` without shard_id/version valid backward compat version 0; no data field → old flat format backward compat; corruption → backup `.corrupt.<nanosec>` + warning containing corrupt/checksum/shard_id/version, then recreate empty versioned.
- Fallback: `get` checks weighted designated shard first then legacy fallback `--legacy`; `global:` checks all shards id order then legacy. `list-keys` union shards+legacy sorted deduped, `distribution` includes zeros counting broadcast keys, `set` for normal weighted, for `global:` all shards, `delete` all shards to clean duplicates, append to ops.log with version,ts,shard_id.
- Help: bare no-args must contain `get-shard-id,set,get,delete,list-keys,distribution,migrate,config,legacy,weight,global,ops.log,dry-run,backup,force,version,shard_id,checksum,staging,timestamp,ts` exit 0; `migrate --help` must contain `dry-run,backup,force,version,shard_id,staging,timestamp` exit 0; unknown command/flag → exit 2, no stdout on invalid config.
- Migration: `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]` – read legacy old flat missing→exit1 invalid JSON→exit1 empty→print "Legacy file is empty, nothing to migrate" but still perform cleanup and ops.log replay. Read shards all formats with corruption handling, read ops.log line by line via `bufio.Scanner` skipping invalid with warning, collect valid entries, then **replay entries in ascending ts order, stable for ties** (abstract requirement – no worked examples at :80 and :92 that mirrored test with v1/v2/100/50 walkthrough; inference restored). Must handle corrupted lines and out-of-order timestamps. Detect duplicate non-global keys → warning `found in multiple shards`. Cleanup wrong-shard and duplicate: only correct weighted retains non-global, global replicated to all. Weighted routing: totalWeight sum, hash MD5 big-endian mod totalWeight iterate id order. Group legacy keys per shard weighted for normal all for global, batched one-write-per-shard atomic via temporary file then rename, version old+1, plus staging two-phase: create staging dir `/app/data/staging` and write grouped files there first via atomic write (`staging/shard_<id>.json` versioned), then also write to final shard path atomically. Version exact increment old+1, preserve vs force overwrites logging Overwriting, dry-run prints plan with total, per-shard, misplaced/dup/ops counts and mentions cleanup, version, shard_id, checksum (no requirement to contain word "dry" – removed trivial substring check that was bug), backup tightening legacy to backup path mkdir -p and each modified shard `.bak` before overwriting and ops.log to `.bak` if exists, corrupted handling, large 100KB value, bad args → exit2, bad config → exit2 no stdout. Post-migration new writes work, gets all legacy even if legacy removed, versioned format valid, no wrong-shard non-global, global replicated, distribution zeros and broadcast counts, list-keys sorted exact.
- TS-sorted hard: ops.log entries must be replayed in ascending ts order, stable for ties, not file order. Example walkthroughs removed from spec to restore inference – models must deduce ts ordering from abstract requirement.
- Staging hard: two-phase via `/app/data/staging` required, source inspection checks CreateTemp+Rename+SetEscapeHTML+staging.

## Completion Rates

Latest online validation – commit `718dc32`, Nest jobs 4365681–84, completed 2026-08-08. **Validation status: passing** (all 6 gates green).

| Stage | Agent | Result |
| --- | --- | --- |
| oracle | oracle | 3/3 (100%) |
| metacode | avocado `meta/avocado-5.14-code` | 7/10 (70%) |
| agent | claude-code `claude-opus-4-8` | 9/10 (90%) |
| codex | `gpt-5.5` | 10/10 (100%) |

Balance gate verdict: *"avocado not trivial and ≥1 agent solved"*. Structural 10/10, contamination MEDIUM, novelty MEDIUM, embedding dedup max-sim 0.471, provenance CLEAN.

- **Turn 1 is non-discriminating: 43/43 trials passed it.** Every failing trial scored `step1=1, step2=0`. The 27 Turn1 tests currently contribute zero signal and consume roughly half the trial budget.
- **All difficulty sits in a few Turn2 tests, and 3 of 4 failures were single-test misses out of 73:**

  | Trial | Agent | Turn2 | Failed test(s) |
  | --- | --- | --- | --- |
  | 22701352 | avocado | 72/73 | `test_migrate_empty_legacy` |
  | 22696007 | avocado | 72/73 | `test_migrate_empty_legacy` |
  | 22712566 | opus | 72/73 | `test_version_exact_increment_after_migration` |
  | 22695354 | avocado | 69/73 | `test_versioned_format_after_set`, `test_version_increment_on_set_delete`, `test_proxy_after_migration_new_writes`, `test_global_broadcast_step2` |

- **Discriminators are fair, not spec⇄test gaps.** `test_migrate_empty_legacy` is backed by explicit spec text at `steps/2_step_two/instruction.md:76` ("even empty legacy must trigger cleanup/replay"); the version tests are backed by `:90`. Agents fail by taking the early-return shortcut, not by guessing an unstated rule.
- **Not a timeout artifact.** Harness runs with `agent_timeout_multiplier=36.0` (effective ≈21600s/step) against actual runs of 600–3000s.
- Calibration caveat: a single 10-trial read on this task carries roughly ±20pp noise – two identical-commit rounds have differed by 4 trials. Judge on pooled reads, not one gate result.
- `tbdReviewStatus=fail` with `tbdReviewDetails=null` is non-gating here: the task carries `tbrNotFinalized=true` (multi-turn TBR is preview and advisory), and an empty review payload is a known false-fail class on Nest. `evalGtNotFinalized=false`, but oracle is 3/3 so Eval GT is clear.

## Model Analysis

- Turn1 gate after trim: 27 tests discriminator-focused. Raw-string full table (123abc→string, {"a":1} x→string, nullx→string, [1,2→string, whitespace trimmed number) is best discriminator – Go trap streaming Decode vs full Unmarshal with trailing check. Weighted routing with brute-force target indices also discriminates. Global broadcast, self-healing, corruption, checksum no-escape remain. Empirically none of these now separate models: 43/43 pass.
- Turn2 73: versioned integrity (shard_id must match, version exact increment old+1), staging two-phase, ts-sorted replay abstract (no worked example mirroring test), duplicate/wrong-shard cleanup, fallback legacy, backup tightening .bak and ops.log.bak content check, distribution global after migration, large value, empty legacy still triggers cleanup, help contains timestamp/ts/version/shard_id/checksum/staging.
- The only discriminators that fired online are **empty-legacy cleanup/replay** and **exact version-increment semantics**. Everything else is solved by all three model families.
- Signal quality is thin at the top: opus 9/10 and gpt 10/10 are at ceiling, so avocado is the only agent still producing separation. Binary scoring over 73 tests means the outcome usually hinges on one edge case, which is the source of the run-to-run variance.
- Pooled trials: use pooled mean across multiple 10-trial runs for calibration, not single gate read, due to ±20pp noise observed.

## Anti-Cheating Analysis

- Hardcoded: Legacy random via python random no seed, tests use custom temp legacy files with brute-force keys for weighted indices, not Dockerfile random legacy. Checksum computed independently and compared to file's checksum field and raw text containing `<` not escaped. Ops.log has random ts nanosec and version.
- Overfitting: Tests use custom config paths with temp dirs and brute-force keys for weighted indices. List-keys sorted exactly, distribution includes all ids even zero. Raw-string test invalid JSON stored as string with full table without naming Go API, checks Unmarshal trailing-data validation. Backup tightening requires each relevant shard `.bak` containing old data and legacy backup and ops.log.bak content. Missing-checksum test corrupts actual hashed shard via MD5. Migrate bad config tests assert exit 2 stdout empty. Unknown flag/arg tests assert exit 2. Duplicate cleanup seeds same non-global key in all shards and asserts only correct weighted retains after migration with --force. Force overwrite checks stderr overwriting. Empty legacy still does cleanup. Stdlib-only inspects `go list` imports.
- Source inspection (CreateTemp/Rename, SetEscapeHTML, staging) is intentional anti-reward-hacking to ensure batched atomic writes two-phase, plus grouping check. Dry-run "dry" substring check removed – was trivial and bug that allowed passing without proper plan; now checks for counts and mentions cleanup/version/shard_id.
- Worked examples removed: previously :80 and :92 contained v1/v2/100/50 walkthrough that mirrored exact test `test_ops_log_replay_sorted_by_ts` (late-ts-100 vs early-ts-50). Now spec only says "replay entries in ascending ts order, stable for ties" – inference restored.
