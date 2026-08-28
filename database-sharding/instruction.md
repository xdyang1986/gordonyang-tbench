# Database Sharding Proxy – Migration

Traffic outgrown single DB. 4 shards provisioned in `/app/config.json` with optional `weight` (default 1, weight<=0 if present → invalid config exit 2). Broadcast keys prefixed `global:` must be replicated to all shards.

Historical dump `/app/data/legacy.json` is old flat format containing users, orders, and global configs. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys (key in wrong weighted shard).

`/app/` already contains a working proxy (module `sharding`) that handles weighted routing, broadcast replication, checksum integrity without HTML escaping, corruption backup, self-healing, transaction log, raw-string handling, and large values. It was built into the image at build time – you start with a working proxy, not a skeleton. Your job is to upgrade it to handle versioned integrity and legacy migration.

## Shard file format (upgraded)

New versioned format must be:
```json
{
  "shard_id": <must equal expected id>,
  "version": <>=0 increments by 1 on each successful set/delete/migration/replay>,
  "data": {...},
  "checksum": "md5 hex of data canonical JSON without HTML escaping"
}
```
Canonical data JSON for checksum is sorted keys, no spaces, without HTML escaping. Checksum covers only `data`.

On startup validate and repair every configured shard before any command. Repair means backup corrupted file to `<path>.corrupt.<nanosec>` with warning, then recreate empty versioned file with correct shard_id, version 0, valid checksum via atomic write (temp file in same dir then rename). Support backward compatibility: old flat file (whole file is data), old format `{"data":...,"checksum":...}` without shard_id/version with valid checksum, and versioned format. Corruption includes invalid JSON, missing/empty checksum, checksum mismatch, shard_id mismatch, missing/invalid version.

On write: always write versioned format with incremented version, correct shard_id, valid checksum without HTML escaping, atomically via temp file then rename.

Global broadcast keys (`global:`): set writes all shards, get returns first found scanning shards in id order then legacy fallback, delete deletes all, get-shard-id returns -1, get-shard-path returns comma-separated sorted list of all paths by id.

Transaction log `/app/data/ops.log`: on successful set/delete append a JSON line with op/key/value (for set)/ts/shard_id (-1 for global, singular per command)/version. Use append, create if missing, no HTML escaping. On read skip invalid JSON lines with warning and avoid infinite loop.

`get` fallback: normal checks designated weighted shard first then legacy (`--legacy` default `/app/data/legacy.json`), global checks all id order then legacy. `list-keys` union shards+legacy deduped sorted exact. `distribution` includes all ids even zero counting broadcast. `ops-log` command prints array skipping invalid lines with warning.

`set` value parsing: if value_json is not fully valid JSON after trimming whitespace, treat whole input as raw string. Do not use lenient streaming decode that consumes prefix and ignores trailing. After writing correct shard, clean up misplaced/duplicates in other shards; `delete` must clean all shards where key exists (global deletes all). Version bumps per modified shard.

Empty string `""` is valid key, hashed via MD5, routed weighted, not missing-arg error. Missing key argument (zero args) exits 2 no stdout.

Help must document commands; `migrate --help` must document its flags.

## Migration subcommand

```
proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```
Flags may appear before or after `migrate`.

Legacy reading: old flat dict. Missing file or invalid JSON → exit 1 with stderr. Empty `{}` → print “Legacy file is empty, nothing to migrate” but still perform cleanup and ops.log replay and honor backup.

Shard reading: support all formats with same corruption handling as init.

Ops.log reading: via Scanner, skip invalid with warning, collect valid, then replay in ascending ts order stable for ties (missing ts→0). Handle corrupted lines and out-of-order timestamps. Detect duplicate non-global keys across shards and log warning.

Cleanup: for any non-global key whose weighted destination does not match current shard, move to correct shard; if present in multiple shards keep only in correct. Global keys replicated to all shards after migration.

Writes: group legacy keys per shard (normal weighted, global to all), batched one atomic write per shard with version old+1 if changed. Create staging dir `/app/data/staging` and ensure staged artifacts are durable and byte-equal to final shards, final writes durable atomic via temp file then rename. No ordering guarantee beyond durability and byte-equality – final writes are source of truth.

Tombstone replay: after legacy+cleanup, replay ops.log ascending ts stable ties applying set/delete to correct shards (weighted for normal, all for global), each replay bumps version.

Preserve existing keys unless `--force`, then overwrite and log `Overwriting key 'X' in shard Y` (accepted on either stream). Dry-run prints plan mentioning cleanup/version/shard_id but must not modify files. Backup: with `--backup`, copy legacy to backup path (mkdir -p), each modified shard to `.bak` containing old data, and ops.log to `.bak` if exists. Handle corrupted shards during migration same as init. Handle 1MB+ values atomically via legacy file.

Bad args or bad config (duplicate id, empty path, negative id, id>=count, weight<=0, empty shards) → exit 2 no stdout. Exit codes: 0 success, 1 missing/invalid legacy/I-O, 2 invalid config/args.

## Post-migration

- New writes work with version increment, ops.log.
- After migration, proxy serves all legacy keys even if legacy file removed.
- Shard files valid versioned format with correct shard_id, version, checksum.
- No wrong-shard non-global keys; global replicated.
- distribution, list-keys, ops-log behave as described.

## Build

`go build -o <binary> .` in `/app/`, module `sharding`, Go stdlib only, no dot imports. Use `/tmp/codimango` for temp files.

## Example

```bash
go build -o ./proxy .
./proxy --config /app/config.json get-shard-id mykey
./proxy set mykey '{"a":1}'
./proxy get mykey
./proxy set global:cfg '{"val":1}'
./proxy get-shard-path global:cfg
./proxy --help
./proxy migrate --help
./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy migrate --config /app/config.json --legacy /app/data/legacy.json --backup /tmp/backup.json --force
```

Implement at `/app/` – image already contains Turn1 proxy, reference solution is Turn2 migration.
