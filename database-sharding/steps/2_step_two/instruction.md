# Turn 2: Hard Migration – Weighted, Broadcast, Versioned Integrity, Timestamp Conflicts, Tombstones, Staging Atomicity, Global Consistency

## Background

Turn1 proxy now handles weighted sharding, `global:` broadcast replication, checksum integrity without HTML escaping, config validation exit 2 no stdout, corruption backup `.corrupt.<timestamp>`, sorted `list-keys`, distribution including zeros, raw-string handling, and transaction log `/app/data/ops.log`.

Now production incident: legacy file `/app/data/legacy.json` (old flat format without checksum) contains 120 users + 30 orders + 5 global configs, but also has outdated entries. Some shard files corrupted (invalid JSON / checksum mismatch / missing checksum / wrong `shard_id`). Prior buggy migration left duplicate non-global keys across multiple shards (should only be in weighted correct shard). Transaction log `ops.log` contains recent sets/deletes that must win over legacy (tombstone handling). Global keys must be consistent across all shards.

We need a **hard, production-grade migration** that is significantly harder than Turn1.

## Task – Update Go code at `/app/` (module `sharding`), built via `go build -o <binary> .`, inherits Turn1 via `inherit_prior_session=true`

### 1. Proxy enhancements (keep Turn1, add harder integrity + fallback)

**Shard file format upgraded in Turn2** (Turn1 had `{"data":...,"checksum":...}`):

```
{
  "shard_id": <int, must equal shard id from config>,
  "version": <int, >=0, increments by 1 on each successful set/delete>,
  "data": { ... },
  "checksum": "md5 hex of canonical data JSON without HTML escaping"
}
```

- Canonical data JSON for checksum: Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)` disabled (Go default escapes `<>&` as `\u003c` etc – must disable). Checksum = `md5_hex(canonical_data_json)`. Version and shard_id are **NOT** included in checksum, only `data`.

- **On read and initialization**: `NewShardingProxyWithLegacy` must **validate and repair every shard before any command** (not just on-demand). For each shard path:
  - Missing/empty → empty data `{}`, version 0, correct shard_id
  - Invalid JSON → corruption
  - Has `data` field:
    - Missing `checksum` → corruption per feedback
    - Missing `shard_id` or `shard_id` != expected id from config → corruption (wrong shard file)
    - Missing `version` or `version` not int or <0 → corruption
    - Checksum mismatch (expected vs stored, no HTML escaping) → corruption
  - No `data` field (old flat like `{}` or Turn1 format `{"data":...,"checksum":...}` without shard_id/version) → treat as old format backward compat: whole file as data if no `data` field, or if Turn1 format (has data+checksum but no shard_id/version) treat `data` as data, version default 0, shard_id from config, then convert to new format on next write. Invalid JSON → corruption.
  - Corruption handling: backup original to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt" or "checksum" or "shard_id" or "version", then recreate empty with version 0, correct shard_id, valid checksum.

- **On write** (`set`/`delete`):
  - Always write new format with incremented version (read current version, +1), correct shard_id, checksum without HTML escaping, atomic via `os.CreateTemp` in same dir + `os.Rename`. Source inspection will check for `CreateTemp` + `Rename`.
  - For `global:` broadcast keys (prefix `global:`): set writes to **all shards** (each shard increments its own version), get checks all shards in id order first found, delete deletes from all shards, `get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted list of all shard paths.
  - Transaction log: on each successful `set`/`delete` (delete true), append JSON line to `/app/data/ops.log`:
    ```
    {"op":"set","key":"...","value":...,"ts":unix_nano,"shard_id":id or -1,"version":new_version}
    {"op":"delete","key":"...","ts":unix_nano,"shard_id":id or -1,"version":new_version}
    ```
    Must use `O_APPEND`, create if missing. If ops.log has invalid JSON line, skip with stderr warning containing "corrupt"/"invalid"/"warning".

- `get` fallback: for normal keys, check designated **weighted** shard first (see weighted algorithm), then legacy fallback (`--legacy` default `/app/data/legacy.json`, old flat). For `global:` keys, check all shards in id order first found, then legacy fallback. Zero-downtime.
- `list-keys` union shards + legacy, deduped sorted.
- `distribution` counts only shards, includes zeros, counts broadcast keys in each shard.
- `ops-log` command: prints ops.log as JSON array (skip invalid lines with warning).

**Help explicitly required** (fixes R02/R03/R08):

- Bare proxy with **no command** (`proxy`, `proxy --config X`, `proxy --config X --legacy Y`) → help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `weight`, `global`, `ops.log`, `dry-run`, `backup`, `force`, `version`, `shard_id`, `checksum` and exit 0.
- `--help`, `-h`, `help` → same help exit 0
- `migrate --help` etc → help containing `dry-run`, `backup`, `force`, `version`, `shard_id` and exit 0
- Unknown command or unknown migrate flag/arg → exit 2, no stdout on invalid config (only stderr)

### 2. Hard Migration subcommand (same binary)

```
proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries both `<binary> --config X --legacy Y --ops-log Z migrate ...` and `<binary> migrate --config X --legacy Y ...`

**Hard requirements beyond generic template:**

- **Read legacy**: old flat dict, missing → exit 1 stderr, invalid JSON → exit 1, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, but **still perform duplicate/misplaced cleanup and ops.log replay**, exit 0.

- **Read shards**: support all formats: old flat `{"k":v}`, Turn1 format `{"data":...,"checksum":...}`, Turn2 format `{"shard_id":..., "version":..., "data":..., "checksum":...}`. Corruption: invalid JSON, checksum mismatch, missing checksum, missing shard_id, shard_id mismatch, missing version → backup `.corrupt.<timestamp>` + warning, treat as empty version 0.

- **Read ops.log**: line by line, skip invalid JSON with warning, collect valid entries. Sort entries by `ts` ascending for replay (if ts missing, treat as 0 and preserve original order). Must handle `<>&` values without HTML escaping.

- **Detect inconsistent state**: scan all shards, build key→[shard ids] for non-global keys. If same non-global key in multiple shards, stderr `Warning: key "dup" found in multiple shards [...]`. For `global:` keys, duplicate across all shards is expected, not inconsistency.

- **Cleanup wrong-shard and duplicate (HARD)**: **Must remove wrong-shard duplicate copies and leave only correct weighted shard copy** unless `--force` overwrites values. For any non-global key where `hashWeighted(key) != current shard id`, remove from wrong shard and move to correct shard (if not already there). For duplicate non-global key in multiple shards including correct, keep only in correct shard. After migration, no non-global shard contains key that doesn't hash to it. For `global:` keys, they must be **consistent across all shards**: after migration, every shard must contain same value for each global key. If global key has different values across shards before migration (inconsistent), resolve to value with latest `updated_at` if values are objects containing `updated_at` integer, else latest `ts` from ops.log if present, else value from legacy if present, else first shard's value.

- **Weighted routing**: totalWeight = sum(weights default 1). Compute hash MD5 big-endian int mod totalWeight, iterate shards sorted by id subtracting weight to pick. Must be used for both proxy and migration. `global:` keys return -1 for get-shard-id but migrate replicates to all shards.

- **Timestamp-based conflict resolution (HARD)**: If legacy and shard both have same key and values are objects containing `updated_at` field (int), keep the one with larger `updated_at`, unless `--force` overwrites with legacy. If values are not objects or have no `updated_at`, preserve existing unless `--force`. Example: shard has `user:1={"id":1,"updated_at":200}`, legacy has `user:1={"id":1,"updated_at":100}` → keep shard (newer). If legacy newer, it should win (unless already handled by ops.log replay).

- **Tombstone handling via ops.log replay (HARD)**: After legacy + cleanup merge, **replay ops.log in timestamp order**, applying set/delete to correct shards (weighted for normal, all for global). This ensures deletes create tombstones and prevent legacy resurrection. Example: legacy has `k=v`, ops.log has `delete k` after legacy timestamp → after migration, `k` should be deleted (not resurrected). Also test: set, delete, set sequence in log must be respected.

- **Global consistency (HARD)**: After migration + log replay, for each `global:` key, all shards must have identical value. If before migration global values inconsistent across shards, resolve to latest `updated_at` or latest ops.log ts.

- **Version handling (HARD)**: New shard format has `version` that increments by 1 on each successful set/delete. Migration must preserve/increment versions: when writing new shard file during migration, new version = old version + number of changes applied to that shard (legacy keys added + misplaced moved + duplicate cleaned + global replicated). If shard was corrupted and treated as empty, version starts at 0 + changes. After migration, version must be >= previous version, and at least number of changes. Check `version` field present and `shard_id` matches.

- **Staging atomicity across all shards (HARD)**: Migration must use staging directory `/app/data/staging/` – write each new shard file to staging as `shard_<id>.json.tmp` first, with correct new format, then after **all** staging files prepared, atomically rename each to final path. If any staging write fails, rollback: restore from `.bak` if backup was requested, clean staging, exit 1. Staging dir must be cleaned up after success (empty or removed). Test will check staging dir exists during migration? Actually we will check that after successful migration, staging dir is empty or removed, and that files are moved atomically.

- **Batched writes**: per shard write once, not per key. Source inspection will check for grouping map and single `atomicWrite` per shard.

- **Dry-run**: prints plan with total legacy keys, per-shard legacy counts, misplaced count, duplicate groups, ops entries count, no modifications, mentions cleanup in stderr.

- **Backup tightening**: with `--backup <path>`, copy legacy to backup path (mkdir -p), **each relevant shard** that will be modified to `<shard>.bak` before overwriting, and ops.log to `ops.log.bak`. Must create legacy backup and each modified shard `.bak` containing old data. Test checks existence and content of each `.bak`.

- **Corrupted handling**: as above, for both shards and ops.log.

- **Bad args**: unknown flag, bare unknown arg → exit 2. **Migrate with bad config** (duplicate id, empty path, weight<=0, etc.) → exit 2, no stdout (only stderr).

- **Invalid config no stdout**: for both proxy and migrate with bad config, stdout empty, only stderr.

- Exit codes: 0 success (help 0, get null 0, empty legacy 0), 1 I/O/missing legacy/invalid legacy, 2 invalid config/args

### 3. Post-migration

- New writes work (weighted, global, version increment, ops.log append)
- After migration, proxy gets all legacy keys (including global) even if legacy removed
- Shard files in new format `shard_id, version, data, checksum` with valid checksum (no HTML escaping, sorted keys) and version incremented
- No wrong-shard non-global keys left; global keys consistent across all shards

### Constraints

- Stdlib only, `go list` no dot imports
- Build via `go build -o <binary> .`
- Same weighted MD5 big-endian mod, no HTML escaping, broadcast, versioned integrity
- Atomic via `CreateTemp`+`Rename`, staging dir, source inspection
- No hardcoded `/tmp/proxy`, use `/tmp/codimango`

### Example

```bash
go build -o ./proxy .

./proxy set user:1 '{"id":1,"updated_at":100}'
./proxy set global:cfg '{"val":1,"updated_at":200}'
./proxy get global:cfg
./proxy --help
./proxy migrate --help

./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn1 present via inherit.
