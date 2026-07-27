# Turn 2: Handle Existing Data Migration Properly (Go)

## Background

Turn1 proxy works for new writes but breaks historical reads. All old data lives in `/app/data/legacy.json`. New proxy only checks shard files → old users get `null`. Production also has an incident: some shard files were partially corrupted during crash, and a prior buggy migration left duplicate keys across multiple shards.

## Task

1. **Update proxy for legacy fallback + robustness**

   - `get` must: check designated shard first; if not found, check legacy file (`--legacy` flag default `/app/data/legacy.json`) if exists.
   - `list-keys` must return union of shard keys + legacy keys, deduplicated and sorted.
   - `distribution` still counts only shards, includes zeros.
   - Corruption handling: invalid JSON shard → backup to `<path>.corrupt.<timestamp>` + stderr warning + recreate `{}`
   - Config validation exit 2.

   CLI now supports `--legacy`:
   ```
   proxy --config /app/config.json --legacy /app/data/legacy.json get <key>
   ```

2. **Implement migration subcommand** (same binary)

   ```
   proxy --config /app/config.json --legacy /app/data/legacy.json migrate [--dry-run] [--backup /path/to/backup.json] [--force]
   ```

   Harness tries `<binary> --config X --legacy Y migrate ...` and fallback `<binary> migrate --config X --legacy Y ...`

   Requirements beyond generic template:

   - Read legacy JSON dict, missing → exit 1 stderr, invalid JSON → exit 1.
   - **Detect inconsistent state**: if same key exists in multiple shards, print warning to stderr listing duplicates and deduplicate during migration, keep correct shard unless `--force`.
   - Group by destination using same MD5 big-endian mod as proxy.
   - Batched atomic writes per shard, preserve existing unless `--force`.
   - Idempotent second run identical.
   - No data loss: after migration, every legacy key reachable via `get`.
   - Dry-run prints plan e.g.:
     ```
     Migration plan: 30 keys from /app/data/legacy.json -> 4 shards
       shard 0 (/app/data/shard_0.json): 8 keys
     Dry-run enabled, not writing any shard files
     ```
     No modifications.
   - Backup: with `--backup`, copy legacy to backup path (mkdir -p) and backup each shard as `<shard>.bak`.
   - Force logging: with `--force`, when overwriting, log to stderr `Overwriting key 'X' in shard Y`.
   - Empty legacy: print "Legacy file is empty, nothing to migrate", still backup if requested, exit 0.
   - Exit codes: 0 success, 1 I/O/missing legacy, 2 invalid args/config.

3. **Post-migration**

   - New writes still work.
   - Legacy may be kept; after migration, proxy gets all legacy keys even if legacy removed, shard files contain correct hash placement.

### Exit codes

- 0 success, 1 not found/I/O, 2 unusable input (bad config, duplicate id, etc.)

### Constraints

- Stdlib only.
- Build via `go build -o <binary> .`
- Same MD5 hashing for proxy/migration.
- Atomic writes, validation, corruption backup.
- Do not hardcode `/tmp/proxy`.

### Example

```bash
go build -o ./proxy .

./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json get user:1
./proxy --config /app/config.json get-shard-id user:1
./proxy --config /app/config.json list-keys
./proxy --config /app/config.json distribution

# Turn2
./proxy --config /app/config.json --legacy /app/data/legacy.json get user:2
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/backup.json --force
```

Implement at `/app/` – Turn1 code present via inherit_prior_session.
