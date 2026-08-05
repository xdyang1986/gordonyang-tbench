# Turn 2: Hard Migration – Weighted, Broadcast, Versioned Integrity (shard_id+version), Cleanup, Log Replay

## Background

Turn1 proxy now handles weighted sharding (weights 1,2,1,1 total 5), `global:` broadcast replication to all shards, checksum integrity without HTML escaping, config validation exit 2 no stdout, corruption backup, sorted `list-keys`, distribution including zeros, raw-string handling, transaction log.

Now incident: legacy file `/app/data/legacy.json` old flat format (no checksum) contains 120 users + 30 orders + 5 global configs that are not yet in shards. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys (key in wrong weighted shard). Shard files from Turn1 are in old format `{"data":...,"checksum":...}` without `shard_id`/`version`. Turn2 must upgrade to new versioned format and fix migration – harder than Turn1.

## Task – Update Go code at `/app/` (module `sharding`), built via `go build -o <binary> .`, inherits Turn1

### 1. Proxy fallback + robustness (UPGRADED to versioned format with shard_id+version for HARD, but easier than 83-test)

**Shard file format upgraded in Turn2 – MUST be versioned:**

```json
{
  "shard_id": <int, must equal shard id from config>,
  "version": <int, >=0, increments by 1 on each successful set/delete and migration>,
  "data": { ... },
  "checksum": "md5 hex of canonical data JSON without HTML escaping"
}
```

- Canonical data JSON for checksum: **sorted keys, no spaces, without HTML escaping**. Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go JSON output that does not escape `<>&` (Go's default escapes those, must be disabled). Checksum = md5 hex of canonical data JSON only (version and shard_id NOT in checksum, only `data`).
- **On read and initialization**: `NewShardingProxyWithLegacy` must **validate and repair every configured shard before any command** (not just on-demand). For each shard path with expected id `sid`:
  - Missing/empty → empty `{}`, version 0, correct shard_id `sid`
  - Invalid JSON → corruption
  - Has `data` field:
    - Missing `checksum` or empty checksum → corruption
    - If has `shard_id` field present → require `checksum` present, `version` present and int >=0, and `shard_id == expected sid`, else corruption. Missing `version` or version <0 or not int when `shard_id` present → corruption.
    - Checksum mismatch (computed with no HTML escaping, sorted keys, separators `,`, `:`) → corruption
    - If has `data`+`checksum` but NO `shard_id` and NO `version` (Turn1 format) → treat as valid old format for backward compat: version default 0, shard_id from config, checksum must still be valid, else corruption.
  - No `data` field → old flat format `{key: value}` backward compat, treat whole file as data, version 0, shard_id from config, convert to new format on next write. Invalid JSON → corruption.
  - **Corruption handling**: backup to `<path>.corrupt.<nanosec>` where nanosec is current time nanos, then recreate empty with version 0, correct shard_id, valid checksum, atomic write via temporary file then rename.
  - Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair of all.

- **On write** (`set`/`delete`):
  - Always write **new versioned format** with incremented version (read current version, +1), correct shard_id, checksum without HTML escaping, atomic write via temporary file in same directory then rename to final.
  - For `global:` broadcast keys (prefix `global:`): set writes to **all shards** (each increments version individually), get checks all shards in id order first found, delete deletes from all shards, `get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted list of all paths by id.
  - Transaction log: on each successful `set`/`delete` (delete true), append JSON line to `/app/data/ops.log`:
    ```json
    {"op":"set","key":"...","value":...,"ts":<unix_nano>,"shard_id":<id or -1>,"version":<new_version>}
    {"op":"delete","key":"...","ts":<unix_nano>,"shard_id":<id or -1>,"version":<new_version>}
    ```
    Create file if missing, append atomically, without HTML escaping. If ops.log has invalid JSON line, skip with warning containing "corrupt"/"invalid"/"warning" and avoid infinite loop on invalid line.

- `get` fallback: for normal keys, check weighted designated shard first (weighted algorithm), then legacy fallback (`--legacy` default `/app/data/legacy.json`, old flat). For `global:` keys, check all shards in id order first found, then legacy fallback. Zero-downtime.
- `list-keys` union shards + legacy, deduped sorted lexicographically exact, reads all shards (triggers init repair). Must include global keys once even if replicated.
- `distribution` counts only shards, includes all ids even zero (explicit 4 keys), counts broadcast keys in each shard (so sum >= unique).
- `ops-log` command: prints ops.log as JSON array, skips invalid lines with warning.
- `set` value_json: JSON; if not valid JSON, treat as raw string value.
- `delete` returns "true"/"false"

**Empty string key handling – EXPLICIT:**

- Empty string `""` **IS valid** for this task and must be hashed via MD5, MD5("") = `d41d8cd98f00b204e9800998ecf8427e`, routed via weighted algorithm, and support `set ""`, `get ""`. This is distinct from missing key argument. `get-shard-id ""` must succeed exit 0 and compute weighted shard id, not exit 2. Missing key argument (zero args) must exit 2, no stdout. Must handle `""` as valid in all commands.

**Help explicitly required:**

- Bare proxy with **no command** (`proxy`, `proxy --config X`, `proxy --config X --legacy Y`, `proxy --config X --legacy Y --ops-log Z`) must print help to stdout containing ALL of: `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `weight`, `global`, `ops.log`, `dry-run`, `backup`, `force`, `version`, `shard_id`, `checksum` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- `migrate --help` (any order) must print help containing `dry-run`, `backup`, `force`, `version`, `shard_id` and exit 0.
- Unknown command or unknown migrate flag/arg must exit 2, no stdout on invalid config (only stderr).

### 2. Hard Migration subcommand (same binary) – weighted, broadcast, versioned, duplicate cleanup, log replay

```
proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries both `<binary> --config X --legacy Y --ops-log Z migrate ...` and `<binary> migrate --config X --legacy Y ...`

Requirements:

- **Read legacy**: old flat dict, missing → exit 1 stderr (not 2), invalid JSON → exit 1, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, but **still perform duplicate/wrong-shard cleanup and ops.log replay** (even empty legacy must trigger cleanup/replay), exit 0. Must handle large legacy (1000 keys).

- **Read shards**: support ALL formats: old flat `{"k":v}`, Turn1 format `{"data":...,"checksum":...}`, Turn2 versioned `{"shard_id":..., "version":..., "data":..., "checksum":...}` with corruption handling: invalid JSON, checksum mismatch, missing checksum, shard_id mismatch, missing version when shard_id present, version <0 → backup `.corrupt.<timestamp>` + warning containing corrupt/checksum/shard_id/version, treat as empty version 0.

- **Read ops.log**: read line by line, skip invalid JSON lines with stderr warning containing "corrupt"/"invalid"/"warning", collect valid entries in file order (no timestamp sort for easier version). Must handle corrupted lines and large lines, avoiding infinite loop on invalid.

- **Detect inconsistent state**: scan all shards, build key→[shard ids] for non-global keys. If same non-global key in multiple shards, log stderr `Warning: key "dup" found in multiple shards [0 1]` and also `Detected duplicate keys...` (global duplicates expected). Must log warning for each duplicate.

- **Cleanup wrong-shard and duplicate (required, hard)**: For any non-global key where `hashWeighted(key) != current shard id`, remove from wrong shard and move to correct weighted shard. If non-global key exists in multiple shards including correct, keep only in correct weighted shard. After migration, no non-global shard contains key that doesn't belong. For `global:` keys, ensure **replicated to all shards**: after migration, every shard must contain each global key from legacy (and existing global keys replicated). If missing in some, replicate.

- **Weighted routing**: totalWeight = sum(weights default 1). `hash = MD5(key)` big-endian int, `idx = hash % totalWeight`, iterate shards sorted by id ascending subtracting weight to pick. `global:` → -1 for get-shard-id but migrate replicates to all.

- **Group legacy keys**: For normal keys, weighted destination; for `global:` keys, destination = all shards. Must group per shard for batched writes.

- **Batched atomic writes**: per shard needing changes (legacy + cleanup + misplaced + global replication), write **once** atomically via temporary file in same directory then rename to final path, version = old version +1 if changed. Must be one write per shard (grouped/batched), not per-key, to ensure durability and avoid partial states. Must also increment version on ops.log replay.

- **Tombstone via ops.log replay**: After legacy + cleanup merge (writes new versioned format), **replay ops.log in file order**, applying set/delete to correct shards (weighted for normal, all for global), each replay increments version. Ensures deletes prevent legacy resurrection. Example: legacy has `k=v`, ops.log has `delete k` → after migration, `k` deleted. Must also handle global keys in replay.

- **Version handling**: new format has `version` incremented. Migration must write new format with `shard_id` correct, version = old version +1. After migration, version >= previous, shard_id matches expected, checksum valid (no HTML escaping, sorted keys, separators), data contains migrated keys.

- **Preserve vs force**: preserve existing shard keys unless `--force`. With `--force`, overwrite with legacy values and log stderr `Overwriting key 'X' in shard Y`.

- **Dry-run**: prints plan with total legacy keys, per-shard legacy counts, misplaced/dup counts, ops count, no modifications, mentions `cleanup`, `version`, `shard_id`. Must not modify any shard file.

- **Backup tightening**: with `--backup <path>`, copy legacy to backup path (mkdir -p) and **each relevant shard** that will be modified to `<shard>.bak` before overwriting (must contain old pre-existing data), and ops.log to `ops.log.bak` if exists.

- **Corrupted handling**: as above, during migration, corrupted shard → backup `.corrupt.` + warning, treat as empty version 0 and continue migration.

- **Large value handling**: must handle 100KB JSON value atomically with valid checksum and no HTML escaping.

- **Bad args**: unknown migrate flag, bare unknown arg after migrate → exit 2. **Migrate with bad config** (duplicate id, empty path, weight<=0, negative id, id>=count) → exit 2, no stdout (only stderr).

- **Invalid config no stdout**: for both proxy and migrate with bad config, stdout empty, only stderr, exit 2.

- Exit codes: 0 success (help 0, get null 0, empty legacy 0), 1 missing legacy/invalid legacy/I/O, 2 invalid config/args/unknown flags

### 3. Post-migration

- New writes work (weighted, global, version increment, ops.log with version)
- After migration, proxy gets all legacy keys (including global) even if legacy removed
- Shard files in new versioned format `shard_id, version, data, checksum` with valid checksum (no HTML escaping) and version incremented, shard_id matches
- No wrong-shard non-global keys left; global keys replicated to all shards
- `distribution` includes zero counts, counts include broadcast keys, length == shard_count
- `list-keys` sorted exact, deduped, includes legacy+shards
- `ops-log` prints JSON array skipping invalid lines with warning

### Constraints

- Go stdlib only: `go.mod` no external requires and `go list` imports no dot imports
- Build via `go build -o <binary> .`
- Same weighted MD5 big-endian, no HTML escaping, broadcast, versioned integrity
- Atomic durability via temporary file then rename, one write per shard grouped/batched, not per-key
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed
- Must handle large ops.log lines and corrupted lines, skipping invalid with warning and avoiding infinite loop

### Example

```bash
go build -o ./proxy .

./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json set global:cfg '{"val":1}'
./proxy --config /app/config.json get global:cfg
./proxy --config /app/config.json get-shard-id ""
./proxy --help
./proxy migrate --help

./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn1 present via inherit.
