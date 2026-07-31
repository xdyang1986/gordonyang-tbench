# Turn 2: Hard Migration – Weighted, Broadcast, Versioned Integrity, Cleanup, Log Replay (Hard)

## Background

Turn1 proxy now handles weighted sharding (weights 1,2,1,1 total 5), `global:` broadcast replication to all shards, checksum integrity without HTML escaping (`SetEscapeHTML(false)`), config validation exit 2 no stdout, corruption backup `.corrupt.<timestamp>`, sorted `list-keys`, distribution including zeros, raw-string handling, transaction log `/app/data/ops.log`.

Now incident: legacy file `/app/data/legacy.json` old flat format (no checksum) contains 120 users + 30 orders + 5 global configs that are not yet in shards. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys (key in wrong weighted shard). Shard files from Turn1 are in old format `{"data":...,"checksum":...}` without `shard_id`/`version`. Turn2 must upgrade to new versioned format and fix migration – harder than Turn1.

## Task – Update Go code at `/app/` (module `sharding`), built via `go build -o <binary> .`, inherits Turn1

### 1. Proxy fallback + robustness (upgrades to versioned format with shard_id+version)

**Shard file format upgraded in Turn2:**

```json
{
  "shard_id": <int, must equal shard id from config>,
  "version": <int, >=0, increments by 1 on each successful set/delete>,
  "data": { ... },
  "checksum": "md5 hex of canonical data JSON without HTML escaping"
}
```

- Canonical data JSON for checksum: **sorted keys, no spaces, without HTML escaping**. Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)` disabled (Go default escapes `<>&` as `\u003c` etc – must disable). Checksum = md5 hex of canonical data JSON only (version and shard_id not in checksum, only `data`).

- **On read and initialization**: `NewShardingProxyWithLegacy` must **validate and repair every configured shard before any command**. For each shard path:
  - Missing/empty → empty `{}`, version 0, correct shard_id
  - Invalid JSON → corruption
  - Has `data` field:
    - Missing `checksum` → corruption
    - If has `shard_id` field: must have `checksum` and `version` present and valid, and `shard_id` must equal expected id from config, else corruption. Missing `version` or version not int or <0 when `shard_id` present → corruption.
    - If has `data` field but no `shard_id` and no `version` (Turn1 format) → treat as valid old format for backward compat: version default 0, shard_id from config, checksum must still be valid, else corruption. This allows Turn1 files to be read.
    - Checksum mismatch (no HTML escaping) → corruption
  - No `data` field → old flat format `{key: value}` backward compat, treat whole file as data, version 0, shard_id from config, convert to new format on next write. Invalid JSON → corruption.
  - Corruption handling: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt" or "checksum" or "shard_id" or "version", then recreate empty with version 0, correct shard_id, valid checksum.

- **On write** (`set`/`delete`):
  - Always write **new format** with incremented version (read current version, +1), correct shard_id, checksum without HTML escaping, atomic via `os.CreateTemp` in same dir + `os.Rename`. Source inspection will check for `CreateTemp` + `Rename` and `SetEscapeHTML`.
  - For `global:` broadcast keys (prefix `global:`): set writes to **all shards** (each increments), get checks all shards in id order first found, delete deletes from all shards, `get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted list.
  - Transaction log: on each successful `set`/`delete` (delete true), append JSON line to `/app/data/ops.log`:
    ```
    {"op":"set","key":"...","value":...,"ts":unix_nano,"shard_id":id or -1,"version":new_version}
    {"op":"delete","key":"...","ts":unix_nano,"shard_id":id or -1,"version":new_version}
    ```
    Must use `O_APPEND`, create if missing. If ops.log has invalid JSON line, skip with warning containing "corrupt"/"invalid"/"warning".

- `get` fallback: for normal keys, check weighted designated shard first, then legacy fallback (`--legacy` default `/app/data/legacy.json`, old flat). For `global:` keys, check all shards in id order first found, then legacy fallback. Zero-downtime.
- `list-keys` union shards + legacy, deduped sorted lexicographically, reads all shards (triggers init repair).
- `distribution` counts only shards, includes all ids even zero, counts broadcast keys in each shard, reads all shards.
- `ops-log` command: prints ops.log as JSON array, skips invalid lines with warning.

**Empty string key handling – explicit to avoid Oracle null ambiguity (per reviewer Request changes):**

- An empty string `""` **IS a valid, provided key** for this task and must be hashed via MD5, MD5("") = `d41d8cd98f00b204e9800998ecf8427e`, routed via weighted algorithm, and support `set ""` / `get ""`. This is distinct from missing key argument. Although Oracle DB treats empty as null, for this task empty is NOT null and NOT missing.
- `get-shard-id ""` (empty string passed as `proxy get-shard-id ""`) must succeed exit 0 and compute weighted shard id, not exit 2.
- Missing key argument (e.g., `proxy get-shard-id` with zero args) must exit 2 with **no stdout**, tested separately in `test_missing_key_arg_exit_2`. Empty string `""` is distinct: it IS an argument (len 1, value empty) and must be valid.

**Help explicitly required (fixes R02/R03/R08 and empty-string concern):**

- Bare proxy with **no command** (`proxy`, `proxy --config X`, `proxy --config X --legacy Y`, `proxy --config X --legacy Y --ops-log Z`) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `weight`, `global`, `ops.log`, `dry-run`, `backup`, `force`, `version`, `shard_id`, `checksum` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- `migrate --help` (any order) must print help containing `dry-run`, `backup`, `force`, `version`, `shard_id` and exit 0.
- Unknown command or unknown migrate flag/arg must exit 2, no stdout on invalid config (only stderr).

### 2. Hard Migration subcommand (same binary) – weighted, broadcast, versioned integrity, duplicate/wrong-shard cleanup, log replay (hard but not staging/updated_at extra-hard)

```
proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries both `<binary> --config X --legacy Y --ops-log Z migrate ...` and `<binary> migrate --config X --legacy Y ...`

Requirements (harder than Turn1, loosened from extra-hard version with staging dir and updated_at timestamp logic which gave 0/10 – this version keeps version/shard_id but removes staging and updated_at to be hard but solvable):

- **Read legacy**: old flat dict, missing → exit 1 stderr, invalid JSON → exit 1, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, but **still perform duplicate/wrong-shard cleanup and ops.log replay** (even empty legacy must trigger cleanup), exit 0.
- **Read shards**: support all formats: old flat `{"k":v}`, Turn1 format `{"data":...,"checksum":...}`, Turn2 format `{"shard_id":..., "version":..., "data":..., "checksum":...}` with corruption handling (invalid JSON, checksum mismatch, missing checksum, shard_id mismatch, missing version when shard_id present) → backup `.corrupt.<timestamp>` + warning, treat as empty version 0.
- **Read ops.log**: line by line, skip invalid JSON lines with warning containing "corrupt"/"invalid"/"warning", collect valid entries in file order.
- **Detect inconsistent state**: scan all shards, build key→[shard ids] for non-global keys. If same non-global key in multiple shards, log stderr `Warning: key "dup" found in multiple shards [...]`. For `global:` keys, duplicate across all shards is expected.
- **Cleanup wrong-shard and duplicate (required, hard)**: For any non-global key where `hashWeighted(key) != current shard id`, remove from wrong shard and move to correct shard. If non-global key exists in multiple shards including correct, keep only in correct weighted shard. After migration, no non-global shard contains key that doesn't belong. For `global:` keys, ensure they are **replicated to all shards**: after migration, every shard must contain each global key from legacy.
- **Weighted routing**: totalWeight = sum(weights default 1). `hash = MD5(key)` big-endian int, `idx = hash % totalWeight`, iterate shards sorted by id subtracting weight to pick. `global:` → -1 for get-shard-id but migrate replicates to all.
- **Group legacy keys**: For normal keys, weighted destination; for `global:` keys, destination = all shards.
- **Batched atomic writes**: per shard needing changes (legacy + cleanup + misplaced + global replication), write once atomically via temp+Rename, version increments (old version +1 if changed). Source inspection checks `CreateTemp`+`Rename` and grouping map and `SetEscapeHTML`. Direct per-key writes considered reward hacking.
- **Tombstone via ops.log replay**: After legacy + cleanup merge (writes new versioned format), **replay ops.log in file order**, applying set/delete to correct shards (weighted for normal, all for global). This ensures deletes prevent legacy resurrection.
- **Version handling**: New shard format has `version` incremented. Migration must write new format with `shard_id` correct, version = old version +1 (or + changes). After migration, version >= previous, and `shard_id` matches, `checksum` valid (no HTML escaping, sorted keys, separators).
- **Backup tightening**: with `--backup <path>`, copy legacy to backup path (mkdir -p) and **each relevant shard** that will be modified to `<shard>.bak` before overwriting, and ops.log to `ops.log.bak` if exists. Must create legacy backup and each modified shard `.bak`.
- **Dry-run**: prints plan with total legacy keys, per-shard legacy counts, misplaced/dup counts, ops count, no modifications, mentions cleanup.
- **Bad args**: unknown migrate flag, bare unknown arg after migrate → exit 2. **Migrate with bad config** (duplicate id, empty path, weight<=0, etc.) → exit 2, no stdout.
- **Invalid config no stdout**: for both proxy and migrate with bad config, stdout empty, only stderr, exit 2.
- Exit codes: 0 success (help 0, get null 0, empty legacy 0), 1 missing legacy/invalid legacy/I/O, 2 invalid config/args/unknown flags

### 3. Post-migration

- New writes work (weighted, global, version increment, ops.log)
- After migration, proxy gets all legacy keys (including global) even if legacy removed
- Shard files in new versioned format `shard_id, version, data, checksum` with valid checksum (no HTML escaping) and version incremented
- No wrong-shard non-global keys left; global keys replicated to all shards

### Constraints

- Stdlib only: `go.mod` no external requires and `go list -f '{{join .Imports " "}}'` must not show external dot paths
- Build via `go build -o <binary> .`
- Same weighted MD5, no HTML escaping, broadcast, versioned integrity
- Atomic via `CreateTemp`+`Rename`, source inspection
- No hardcoded `/tmp/proxy`, use `/tmp/codimango`

### Example

```bash
go build -o ./proxy .

./proxy set user:1 '{"id":1}'
./proxy set global:cfg '{"val":1}'
./proxy get global:cfg
./proxy --help
./proxy migrate --help

./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn1 present via inherit.
