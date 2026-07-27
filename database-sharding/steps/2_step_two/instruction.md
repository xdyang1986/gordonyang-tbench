# Turn 2: Handle Existing Data Migration Properly (Go)

## Background

Your Turn 1 sharding proxy works for new writes, but production is broken! We forgot about **existing data**.

- All historical data still lives in `/app/data/legacy.json`, a large JSON dump from the old single-DB instance (e.g. `{"user:1": {...}, "user:2": {...}}`).
- New proxy only looks at shard files, so reads for old users return `null` → data appears lost.
- Users are complaining. On-call wants zero-downtime migration.

We need to fix this properly in Go.

## Task

1. **Update Go proxy to handle legacy data gracefully**:

   Your existing `ShardingProxy` (in Go) should implement **fallback reading**:
     1. Check its designated shard file first.
     2. If not found, check `/app/data/legacy.json` (if it exists, configurable via `--legacy` flag) and return value from there if present.
   - This provides zero-downtime reads during migration window.
   - `list-keys` should return union of keys from all shards **plus** legacy file (if exists).
   - `distribution` should still only count shard files (legacy not counted as shard), but remain correct after migration.

   CLI must now support optional `--legacy` flag (default `/app/data/legacy.json`):
   ```
   proxy --config /app/config.json --legacy /app/data/legacy.json get <key>
   ```

2. **Implement migration subcommand** in same binary:

   ```
   proxy --config /app/config.json --legacy /app/data/legacy.json migrate [--dry-run] [--backup /path/to/backup.json] [--force]
   ```

   Or as separate binary if you prefer, but the test harness will try:
   - First: `<binary> migrate --legacy ... --config ... [flags]`
   - If not, it will look for a binary built from a file containing "migrate" main or try `go run` with migrate args.
   - Simplest: implement `migrate` as a subcommand of same binary (recommended).

   Requirements for `migrate`:

   - **Read legacy file** (JSON dict). If missing, exit non-zero with error to stderr.
   - **Group keys by destination shard** using SAME MD5 mod algorithm.
   - **Write per shard atomically**: for each shard receiving keys, load current shard data, merge legacy keys, write to temp file then `os.Rename`. Must work even if shard already has data (preserve existing keys unless `--force` overwrites).
   - **Idempotency**: running migration twice must not lose data or duplicate. Second run same result.
   - **No data loss**: after migration, every key from legacy must be reachable via `proxy get <k>`.
   - **Dry-run**: with `--dry-run`, print plan (e.g. "Would migrate X keys: shard 0: Y keys...") and do NOT modify shard files.
   - **Backup**: with `--backup <path>`, copy legacy file to backup path before migration, and also optionally backup each shard as `<shard>.bak`. At minimum backup of legacy required when flag given.
   - **Error handling**: invalid JSON, permission errors → exit non-zero, stderr, no corrupt shard files (atomic rename guarantees).
   - **Performance**: handle at least 10k keys efficiently – batch per shard, not one file write per key.

3. **Post-migration behavior**:

   - After migration, proxy still works for new writes (`set`).
   - Legacy file may be kept for rollback – tests only require that after migration, proxy can get all legacy keys and shard files contain them correctly per hash. Deleting legacy after migration is optional, but fallback logic must not break.
   - Reads must work both before and after migration.

### Constraints

- Go stdlib only, `go.mod` no external requires.
- Binary must build via `go build -o /tmp/proxy .` from `/app/` (must have a `main` package).
- Same MD5 hashing must be used for proxy and migration – mismatch is data loss.
- Atomic writes mandatory.
- CLI flags: `--config` default `/app/config.json`, `--legacy` default `/app/data/legacy.json` for migrate and fallback.

### What success looks like for this turn

- `get` returns legacy values via fallback before migration.
- `proxy migrate` migrates all legacy keys into correct shard files.
- After migration, all legacy keys `get`-able via proxy (even if legacy file later removed).
- Idempotent second run, dry-run no-op, backup creation works.
- No shard file corruptions, no data loss (sum of shard counts == unique legacy + pre-existing shard keys).
- Turn 1 functionality (set/get/delete/distribution) still passes.

### Reference CLI examples

```bash
# Build
go build -o /tmp/proxy .

# Turn1 commands
/tmp/proxy --config /app/config.json set user:1 '{"id":1}'
/tmp/proxy --config /app/config.json get user:1
/tmp/proxy --config /app/config.json get-shard-id user:1
/tmp/proxy --config /app/config.json list-keys
/tmp/proxy --config /app/config.json distribution

# Turn2 migration
/tmp/proxy --config /app/config.json --legacy /app/data/legacy.json migrate
/tmp/proxy --config /app/config.json --legacy /app/data/legacy.json migrate --dry-run
/tmp/proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/backup.json --force
```

Implement at `/app/` in Go – the existing Turn1 code will be present due to `inherit_prior_session=true`, so you just need to update it.

