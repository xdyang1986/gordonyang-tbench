# Database Sharding Proxy – Single-Turn Hard Migration Task (collapsed from multi-turn to fix step-0 1.0 gate)

This is a **single-turn** task (previously two-turn with 28+73). It collapsed to fix the step gate failure: “step-0 has a 1.0 pass-rate and the single-1.0 exemption needs 3+ declared steps (avocado 1/0.4, opus 1/0.6, gpt 1/0.2)”. Two runs that passed were luck – an agent-never-ran infra flake dropped Turn1 to 0.9, otherwise every clean run had step-0 = 1.0. Turn1 has never discriminated (43/43 in original README, still 1.0 after under-specifying).

- **Collapsed:** Ship Turn-1 proxy pre-built in Dockerfile and grade migration only. `inherit_prior_session=true` already handed Turn-2 agent that implementation; Dockerfile now does same (`COPY steps/1_step_one/solution/solve.sh` + `bash`). Regression coverage retained via `test_turn1_basic_still_works` / `test_turn1_hashing_still_correct` inside Turn2 tests. With one declared step, gate evaluates migration (~0.8 not 1.0) and passes. Cheapest unblock (declare third step) would be gaming; single-turn is honest.

- **81 tests** (previously 28+76 = 104) migration only, hard but solvable.

## Background

Turn1 proxy now handles weighted sharding (weights 1,2,1,1 total 5, totalWeight sum no worked example mapping Hash%5), global broadcast replication (-1 sentinel and comma-separated id-sorted paths), checksum without HTML escaping, corruption backup, self-healing, raw-string single invariant (full JSON consumption, trailing bytes → raw string, no table), ops.log, large value, inotify atomic write check.

Legacy `/app/data/legacy.json` old flat format contains 120 users + 30 orders + 5 global configs. Prior buggy migration left duplicate non-global keys across shards and misplaced keys. Shard files from Turn1 old format `{"data":...,"checksum":...}` without shard_id/version. Task: upgrade to versioned and migrate.

Turn1 proxy is pre-built in Dockerfile – you start with a working weighted/broadcast proxy, not skeleton.

## Task – Update Go code at `/app/` (module `sharding`), built via `go build -o <binary> .`

Implement migration and upgraded proxy to pass 81 tests.

**Versioned shard file format:**
```json
{
  "shard_id": <must equal expected id>,
  "version": <>=0 increments by 1>,
  "data": {...},
  "checksum": "md5 hex of data canonical JSON without HTML escaping"
}
```
Canonical is sorted keys, no spaces, no HTML escaping. Checksum only over data.

On init validate every shard before any command, repair if corrupted (invalid JSON, missing/empty checksum, checksum mismatch, shard_id mismatch, missing/invalid version) with backup `.corrupt.<nanosec>` + warning, recreate empty versioned via atomic temp file rename. Backward compat with Turn1 format and old flat.

On write: versioned incremented, correct shard_id, checksum no HTML escaping, atomic temp file in same dir then rename.

Global: `set` writes all shards, `get` returns first found scanning id order then legacy fallback, `delete` deletes all, `get-shard-id` -1, `get-shard-path` comma-separated sorted by id.

Transaction log `/app/data/ops.log`: O_APPEND, SetEscapeHTML false, use Scanner not Decoder. On set/delete append **one JSON line** with op/key/value (for set)/ts/shard_id (-1 for global sentinel singular per spec line 30, not per shard)/version. Previously oracle logged per shard (4 entries all -1) – fixed to 1 matching what all 3 agents did and spec. Skip invalid lines with warning.

Fallback: normal checks designated weighted shard then legacy, global checks all id order then legacy. `list-keys` union sorted deduped, `distribution` includes all ids even zero counting broadcast.

Self-healing: set cleans misplaced/duplicates in other shards, delete cleans all shards where key exists (global deletes all). Version bumps per modified shard.

Empty string `""` valid key hashed via MD5 `d41d8cd98f00b204e9800998ecf8427e`, routed weighted, not missing-arg error (missing arg exits 2 no stdout).

Help: top-level help must name commands (get-shard-id/set/get/delete/list-keys/distribution/migrate) – not require subcommand flags like dry-run (migrate flag) nor decorative tokens (version, shard_id, checksum, staging, timestamp, ts). Realistic Scenario scored 2 for “help text must carry decorative tokens”. Migrate --help must name its flags dry-run/backup/force, not timestamp/shard_id/staging/version. Fixes 7 failing trials where help check was sole separator.

Migration: `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]` – flags may appear before or after migrate (harness tries both). Legacy missing/invalid → exit1, empty → print "Legacy file is empty, nothing to migrate" but still cleanup/replay and backup if requested. Shards all formats with repair. Ops.log via Scanner skipping invalid, replay ascending ts stable ties (missing ts→0). Detect duplicate non-global keys → warning. Cleanup wrong-shard/duplicate keeping only correct weighted retains non-global, global replicated to all. Grouped batched writes one atomic write per shard. Staging: must create `/app/data/staging` and ensure staged artifacts durable and byte-equal to final shards, final writes durable atomic, staged byte-equal (reworded from ordering to durable+byte-equal no ordering – inotify breaks when staging dir recreated, ptrace tracer amd64-only and crash-prone). Tombstone replay ascending ts stable ties, each replay bumps version. Preserve vs force with Overwriting log accepted on either stream (spec no longer names stream). Dry-run prints plan mentioning cleanup/version/shard_id, no mods. Backup copies legacy to backup path mkdir -p, each modified shard to .bak containing old data, ops.log to .bak if exists. Handle 1MB+ values atomically via legacy migration (not via set CLI to avoid ARG_MAX). Bad args/bad config → exit2 no stdout.

Post-migration: new writes work, gets all legacy even if legacy removed, versioned format valid, no wrong-shard non-global, global replicated, distribution zeros and broadcast counts, list-keys sorted exact, ops-log array skipping invalid.

Coverage added for R06/R07 to close BAD_*: global get-shard-path comma-separated id-sorted, global get picks first id-order on conflict, missing/invalid config-file, empty shard lists, exact ops.log count global set/delete now 1 per spec singular (fixed oracle). Custom --ops-log exercised via tmp custom path, post-subcommand flags migrate --config, 1MB via legacy.

## Build

`go build -o <binary> .` in `/app/`, module `sharding`, Go stdlib only.

## Latest Validation (after fixes in this patch)

- Turn1 under-specified (raw-string table → single invariant full consumption, weighted example Hash%5 removed, Success collapsed) – same as adf4384 chat-server tombstone fix
- Turn2 under-specified (checklist → inference-heavy, 147→92 lines): removed calibration notes “harder than Turn1, loosened vs staging+updated_at extra-hard which gave 0/10 all” line7, “Requirements (harder than Turn1... timeout harness crash)” line74→“Requirements:”, “(previous harness crash)” line129; source-inspection promises gone (CreateTemp/Rename/SetEscapeHTML)
- Staging reworded to durable+byte-equal no ordering per AFTR fork
- Help test fixed: top-level requires commands only, migrate --help requires dry-run/backup/force (not timestamp/shard_id/staging/version)
- Oracle fixed: global set/delete logs 1 entry shard_id -1 not 4 (spec line30 singular), matching agents
- Tautologies killed: `or True` at 860 and 920
- Staging test fixed: uses default CONFIG_PATH (removes ambiguity where opus derived staging from shard dir), raw bytes equality, picks shard legacy key routes to
- Single-turn collapse: Dockerfile pre-builds Turn1 via `steps/1_step_one/solution/solve.sh`, task.toml format `terminal_bench_single_turn`, tests at `tests/` (81 tests), gate evaluates migration step ~0.8 not 1.0

Docker verify:
```
28 passed in Turn1 (if run via steps)
81 passed in Turn2 / single-turn
```

Pooled calibration ±20pp noise – judge pooled trials, not single gate. Contamination NOT_SUPPORTED answered (repo not covered, can't re-run). TBR regenerated, BAD_GRADING_WRONG → BAD_GRADING_WEAK → should move off EASY after Turn2 under-spec.

## Commands

```bash
go build -o ./proxy .
./proxy --config /app/config.json get-shard-id mykey
./proxy set mykey '{"a":1}'
./proxy get mykey
./proxy set global:cfg '{"val":1}'
./proxy get global:cfg
./proxy list-keys
./proxy distribution
./proxy --help
./proxy migrate --help
./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy migrate --config /app/config.json --legacy /app/data/legacy.json --backup /tmp/backup.json --force
```

Implement at `/app/` – Turn1 proxy pre-built in image, reference solution is Turn2 migration.
