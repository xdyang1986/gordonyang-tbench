# Turn 2: Handle Existing Data Migration Properly (Go) – Integrity, Duplicates, Fallback

## Background

Turn1 proxy works for new writes but breaks historical reads (`/app/data/legacy.json` old flat format). Production also has:
- Corrupted shard files (invalid JSON or checksum mismatch) from crash.
- Duplicate keys across multiple shards from prior buggy migration (same key in shard 0 and shard 2, but should only be in MD5-designated shard).
- Users complaining, need zero-downtime migration with integrity.

## Task

### 1. Update proxy for legacy fallback + robustness (keeps Turn1 integrity format)

Shard file format remains:
```json
{"data": { ... }, "checksum": "md5_hex_of_canonical_data_json"}
```
- Canonical data JSON: sorted keys, e.g., `json.Marshal(data)` (Go sorts map keys). Checksum = md5 hex.

- On read: if file has `data` field, verify checksum (MD5 of canonical `data` JSON). Mismatch → corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt" or "checksum", recreate empty with valid checksum, treat as empty. If file has no `data` field, treat whole file as old flat dict (backward compat). If invalid JSON → corruption handling.
- On write: always write new format with correct checksum, atomically via temp+rename in same dir.
- Config validation exit 2 as Turn1.

- `get` must: check designated shard first; if not found, check legacy file (`--legacy` default `/app/data/legacy.json`, old flat format) if exists, return value. This provides zero-downtime.
- `list-keys` must return union of shard keys + legacy keys, deduped and sorted lexicographically.
- `distribution` counts only shards, includes all ids even zero.

CLI now supports `--legacy` flag and **help requirement** (fixes R02/R03/R08):

```
proxy --help / -h / help -> prints usage containing get-shard-id, set, get, delete, list-keys, distribution, migrate, config, legacy, dry-run, backup, force and exits 0
proxy migrate --help -> same, must include dry-run, backup, force and exit 0
proxy --config X --legacy Y get <key>
```

Both help forms must exit 0 and print to stdout containing required words.

### 2. Migration subcommand (same binary) with duplicate cleanup

```
proxy --config /app/config.json --legacy /app/data/legacy.json migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries:
- `<binary> --config X --legacy Y migrate ...`
- fallback `<binary> migrate --config X --legacy Y ...`

Requirements beyond generic template:

- **Read legacy** JSON dict (old flat format), missing → exit 1 stderr, invalid JSON → exit 1 stderr, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, exit 0.
- **Detect inconsistent state before migration**: scan all shards (both old flat and new checksum formats). Build map key → list of shard ids where it appears. If same key in multiple shards, log warning to stderr containing key and shards, e.g., `Warning: key "dup" found in multiple shards [0 1]`. This is the duplicate bug.
- **Cleanup wrong-shard copies**: migration must **remove wrong-shard duplicate copies and leave only correct shard copy** (unless --force says overwrite values). For any key found in a shard whose id != MD5-designated shard id, remove it from wrong shard (i.e., after migration, only the MD5-correct shard retains it). If key exists in multiple shards including correct shard, keep only in correct shard.
  - Example: key `user:1` hashes to shard 2, but exists in shard 0 and shard 1 due to bug. After migration, shard 0 and 1 should no longer have `user:1`, only shard 2 should.
  - This is required for regression test: seed same key in multiple shards, assert only correct shard retains it.
- **Group legacy keys** by destination using same MD5 big-endian mod.
- **Batched atomic writes**: per shard that needs changes (new legacy keys + cleanup of misplaced keys + duplicate removal), load current data, merge, write once via atomic rename.
- **Preserve vs force**: preserve existing shard keys unless `--force`. With `--force`, overwrite existing keys with legacy values and log to stderr `Overwriting key 'X' in shard Y` for each overwritten.
- **Idempotency**: second run identical contents, no growth.
- **No data loss**: after migration, every legacy key reachable via `proxy get`.
- **Dry-run**: with `--dry-run`, print plan:
  ```
  Migration plan: 30 keys from /app/data/legacy.json -> 4 shards
    shard 0 (/app/data/shard_0.json): 8 keys
  Dry-run enabled, not writing any shard files
  ```
  No modifications. Include total and per-shard counts.
- **Backup**: with `--backup <path>`, copy legacy to backup path (mkdir -p parent), and backup each shard as `<shard>.bak` before overwriting. At minimum legacy backup required, but also test checks shard `.bak` creation and legacy+shard backup together.
- **Corrupted shard handling during migration**: if a shard file is invalid JSON or checksum mismatch, backup to `.corrupt.<timestamp>` (as proxy does), treat as empty, then migrate into it successfully (should not fail).
- **Empty/invalid legacy**: empty → message + backup if requested, exit 0; invalid JSON → exit 1 stderr.
- Exit codes: 0 success, 1 I/O/missing legacy, 2 invalid args/config (duplicate shard id, etc.)

### 3. Post-migration

- New writes still work.
- After migration, proxy gets all legacy keys even if legacy removed.
- Shard files must be in new checksum format with valid checksum after migration.
- No shard files left with wrong-shard keys after successful migration (duplicate cleanup).

### Exit codes summary

- 0 success (help also 0, get returning null is 0)
- 1 not found/I/O (missing legacy for migrate, etc.)
- 2 unusable input (bad config, bad args)

### Constraints

- Stdlib only, `go.mod` no external requires.
- Build via `go build -o <binary> .`
- Same MD5 big-endian mod for proxy/migration.
- Atomic writes, validation, corruption backup, duplicate cleanup.
- Do not hardcode `/tmp/proxy`, use `/tmp/codimango/...` for any tmp artifacts.

### Example

```bash
go build -o ./proxy .

./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json get user:1
./proxy --config /app/config.json get-shard-id user:1
./proxy --help

./proxy --config /app/config.json --legacy /app/data/legacy.json get user:2
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
./proxy migrate --help
```

Implement at `/app/` – Turn1 code present via inherit_prior_session.
