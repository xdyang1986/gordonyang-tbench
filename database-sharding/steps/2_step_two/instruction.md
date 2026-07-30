# Turn 2: Migration – Weighted, Broadcast, Fallback, Versioned Integrity, Cleanup, Log Replay, Help (Loosened but Robust, Explicit Empty-String Handling)

## Background

Turn1 proxy now handles simple MD5 mod sharding with checksum integrity without HTML escaping, config validation exit 2 no stdout, corruption backup `.corrupt.<timestamp>`, sorted `list-keys`, distribution including zeros, raw-string handling, and explicit help for bare no-args (including explicit empty-string handling – see below).

Now incident: legacy file `/app/data/legacy.json` old flat format (no checksum) contains 120 users + 30 orders + 5 global configs that are not yet in shards. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys (key in wrong shard). Transaction log `/app/data/ops.log` contains recent sets/deletes that must win over legacy (tombstone handling). Turn2 adds weighted routing, broadcast, fallback, duplicate cleanup, log replay – harder than Turn1 but loosened from extra-hard versioned+staging+updated_at that gave 0/10 all models + harness crash.

## Task – Update Go code at `/app/` (module `sharding`), built via `go build -o <binary> .`, inherits Turn1

### 1. Proxy fallback + robustness (keeps Turn1 format `data`+`checksum`, no version/shard_id for this loosened version, but adds weighted and broadcast)

Shard file format remains Turn1's `data`+`checksum` (NOT versioned for this loosened version to keep it less complex than extra-hard):

```json
{
  "data": { ... },
  "checksum": "md5 hex of canonical data JSON without HTML escaping"
}
```

- Canonical: sorted keys, no spaces, no HTML escaping. Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)` disabled. Checksum = md5 hex of canonical.
- On write: always new format with correct checksum, atomic via `os.CreateTemp` in same dir + `os.Rename`. Source inspection will check for `CreateTemp` + `Rename` and `SetEscapeHTML`.
- On read and initialization: `NewShardingProxyWithLegacy` must **validate and repair every configured shard before any command** (not just on-demand). For each shard:
  - Missing/empty → empty `{}`
  - Has `data` field: require `checksum` present and non-empty, else corruption (missing checksum → corruption per earlier feedback)
  - Checksum mismatch → corruption
  - No `data` field → old flat format `{key: value}` backward compat, convert to new format on next write. Invalid JSON → corruption.
  - Corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum.
  - Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair for any corrupted shard (fixes earlier bug where missing-checksum test corrupted wrong shard never read – now we corrupt actual hashed shard and use list-keys reading all shards).

- `get`: For normal keys, check **weighted** designated shard first, then legacy fallback (`--legacy` default `/app/data/legacy.json`, old flat). For `global:` keys (prefix `global:`), check all shards in id order first found, then legacy fallback. Zero-downtime.
- Weighted routing for Turn2: totalWeight = sum(weights default 1), `hash = MD5(key)` big-endian int, `idx = hash % totalWeight`, iterate shards sorted by id subtracting weight to pick. `global:` → -1 for get-shard-id but set to all. Spec explicitly says weighted for Turn2 (Turn1 was simple mod, Turn2 upgrades to weighted to make it harder).
- `list-keys`: union shards + legacy, deduped sorted lexicographically, reads all shards.
- `distribution`: counts only shards, includes all ids even zero, counts broadcast keys in each shard.
- `set`: For normal, weighted designated shard; for `global:`, all shards. Append to ops.log on success.
- `delete`: For normal, designated shard; for `global:`, all shards (true if any). Append to ops.log on success (true).
- `ops-log`: prints ops.log as JSON array, skips invalid lines with warning containing "corrupt"/"invalid"/"warning" (previously used json.Decoder that infinite-looped on invalid line causing timeout/harness crash, now fixed to bufio.Scanner line-based).

Help explicitly required (fixes R02/R03/R08 and empty-string ambiguous concern):

- Bare proxy with **no command** (`proxy`, `proxy --config X`, `proxy --config X --legacy Y`, `proxy --config X --legacy Y --ops-log Z`) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `weight`, `global`, `ops.log`, `dry-run`, `backup`, `force` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- `migrate --help` (in any order) must print help containing `dry-run`, `backup`, `force` and exit 0.
- Unknown command or unknown migrate flag/arg → exit 2, no stdout on invalid config (stderr only).

**Empty string key handling – explicit to avoid Oracle null ambiguity (per reviewer Request changes decision):**

- **For this task, empty string "" IS NOT considered a legitimate key** and should be treated as **invalid input** (same as missing key argument, exit 2, no stdout). This avoids ambiguity: Oracle DB treats empty as null, and there is no previous data with empty key in our dataset, so human treating "" as null/invalid should not be faulted. Tests **do NOT include empty string "" as valid key** – they only test missing arg case via `test_missing_key_arg_exit_2`. The previous discriminator `test_get_shard_id_uses_md5` that included "" has been removed to avoid ambiguous expectations per Human Checks.
- Therefore:
  - `proxy get-shard-id ""` (empty string passed as `proxy get-shard-id ""`) must **exit 2** with no stdout (only stderr), not exit 0. This is same as missing arg `proxy get-shard-id` (zero args) which also exits 2.
  - `proxy set "" <value>`, `proxy get ""`, `proxy delete ""` must also exit 2, no stdout.
  - Tests do NOT include "" as valid key – they only test missing arg case. Previous failure analysis showing 41/42 for Codex failing only empty-string is now fixed by making spec explicit that empty is invalid, so models that exit 2 for "" will now **pass** instead of fail, increasing Turn1 full pass rate from 3/10 to 10/10 and moving discrimination to harder Turn2 features (weighted, global, fallback, duplicate cleanup, log replay) which is the intended differentiation per TBR Review note about harness crash dirtying results and no real Turn2 failures.
- Shell reminder: `proxy get-shard-id ""` has 1 arg (empty) vs `proxy get-shard-id` has 0 args (missing) – both exit 2 for this task (empty not legitimate). The test suite does not enforce empty as valid to avoid penalizing either interpretation, but spec explicitly says empty should be considered invalid.

### 2. Migration subcommand (same binary) – weighted, broadcast, duplicate cleanup, log replay (loosened, less complex than extra-hard)

```
proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries `<binary> --config X --legacy Y --ops-log Z migrate ...` and fallback `<binary> migrate --config X --legacy Y ...`

Requirements (harder than Turn1, loosened vs extra-hard version with version/shard_id/staging/updated_at that gave 0/10 all models + harness crash – this version keeps data+checksum only, no version, no staging, no updated_at, but keeps weighted+global+cleanup+log replay which is still hard but not too hard):

- **Read legacy**: old flat dict, missing → exit 1 stderr, invalid JSON → exit 1, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, but **still perform duplicate/wrong-shard cleanup and ops.log replay** (even empty legacy must trigger cleanup), exit 0.
- **Read shards**: support both old flat `{"k":v}` and new format `{"data":...,"checksum":...}` with corruption handling. If corrupted (invalid JSON, checksum mismatch, missing checksum), backup to `.corrupt.<timestamp>` + warning, treat as empty and continue. Initialization already repairs every shard.
- **Read ops.log**: line by line via `bufio.Scanner`, skip invalid JSON lines with warning containing "corrupt"/"invalid"/"warning", collect valid entries in file order (no need to sort by ts for loosened version).
- **Detect inconsistent state**: scan all shards, build key→[shard ids] for non-global keys. If same non-global key in multiple shards, log stderr `Warning: key "dup" found in multiple shards [0 1]`. For `global:` keys, duplicate across all shards is expected.
- **Cleanup wrong-shard and duplicate (required, hard)**: For any non-global key where `hashWeighted(key) != current shard id`, remove from wrong shard and move to correct weighted shard. If duplicate exists in multiple shards including correct, keep only correct weighted shard. After migration, no non-global shard contains key that doesn't belong. For `global:` keys, ensure **replicated to all shards**: after migration, every shard must contain each global key from legacy. If missing in some, replicate.
- **Weighted routing**: totalWeight = sum(weights default 1), `hash = MD5(key)` big-endian int, `idx = hash % totalWeight`, iterate shards sorted by id subtracting weight. `global:` → -1 for get-shard-id but migrate replicates to all.
- **Group legacy keys**: For normal keys, weighted destination; for `global:` keys, destination = all shards.
- **Batched atomic writes**: per shard needing changes (legacy + cleanup + misplaced + global replication), write once atomically via temp+Rename. Source inspection checks `CreateTemp`+`Rename` and grouping map and `SetEscapeHTML`. Direct per-key writes considered reward hacking per R07.
- **Tombstone via ops.log replay (loosened)**: After legacy + cleanup merge (writes new format), **replay ops.log in file order**, applying set/delete to correct shards (weighted for normal, all for global). This ensures deletes prevent legacy resurrection.
- **Preserve vs force**: preserve existing shard keys unless `--force`. With `--force`, overwrite with legacy values and log stderr `Overwriting key 'X' in shard Y`.
- **Dry-run**: prints plan with total legacy keys, per-shard legacy counts, misplaced/dup counts, ops count, no modifications, mentions cleanup.
- **Backup tightening**: with `--backup <path>`, copy legacy to backup path (mkdir -p) and **each relevant shard** that will be modified to `<shard>.bak` before overwriting, and ops.log to `ops.log.bak` if exists. Must create legacy backup and each modified shard `.bak` containing old data.
- **Corrupted handling**: as above, including missing checksum treated as corruption (corrupt actual hashed shard, not fixed wrong shard) and initialization repairs every shard.
- **Bad args**: unknown migrate flag, bare unknown arg after migrate → exit 2. **Migrate with bad config** (duplicate id, empty path, weight<=0, etc.) → exit 2, no stdout.
- **Invalid config no stdout**: for both proxy and migrate with bad config, stdout empty, only stderr, exit 2.
- Exit codes: 0 success (help 0, get null 0, empty legacy 0), 1 missing legacy/invalid legacy/I/O, 2 invalid config/args/unknown flags

### 3. Post-migration

- New writes work (weighted, global, ops.log)
- After migration, proxy gets all legacy keys (including global) even if legacy removed
- Shard files in new format `data, checksum` with valid checksum (no HTML escaping, sorted keys, separators)
- No wrong-shard non-global keys left; global keys replicated to all shards

### Constraints

- Stdlib only: `go.mod` no external requires and `go list -f '{{join .Imports " "}}'` no dot imports
- Build via `go build -o <binary> .`
- Same weighted MD5 for Turn2 (Turn1 simple mod), no HTML escaping, broadcast
- Atomic via `CreateTemp`+`Rename`, source inspection
- No hardcoded `/tmp/proxy`, use `/tmp/codimango`

### Example

```bash
go build -o ./proxy .

./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json set global:cfg '{"val":1}'
./proxy --config /app/config.json get global:cfg
./proxy --help
./proxy migrate --help

./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn1 present via inherit.
