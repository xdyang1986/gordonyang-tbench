# Turn 2: Hard Migration – Weighted, Broadcast, Versioned Integrity, Cleanup, Log Replay

## Background

Turn1 proxy now handles weighted sharding (weights 1,2,1,1 total 5), `global:` broadcast replication, checksum integrity without HTML escaping, config validation, corruption backup, sorted list-keys, distribution, raw-string handling, and transaction log.

Legacy file `/app/data/legacy.json` is old flat format containing users, orders, and global configs not yet in shards. Prior buggy migration left duplicate non-global keys across multiple shards and misplaced keys (key in wrong weighted shard). Shard files from Turn1 are in old format `{"data":...,"checksum":...}` without shard_id/version. Turn2 must upgrade shards to versioned format and complete migration correctly.

## Task – Update Go code at `/app/` (module `sharding`), built via `go build -o <binary> .` – Turn1 solution will be replayed via `solve.sh` before grading (inherit_prior_session=true)

### 1. Proxy upgrades to versioned format

**Shard file format:**
```json
{
  "shard_id": <must equal expected id>,
  "version": <>=0, increments by 1 on each successful set/delete/migration/replay>,
  "data": { ... },
  "checksum": "md5 hex of data canonical JSON without HTML escaping"
}
```
Canonical JSON for checksum is sorted keys, no spaces, without HTML escaping (Go must disable `SetEscapeHTML`). Checksum covers only `data`.

On startup (`NewShardingProxyWithLegacy`) validate and repair every configured shard before any command. Repair means backup corrupted file to `<path>.corrupt.<nanosec>` with warning containing corruption reason, then recreate empty versioned file with correct shard_id, version 0, valid checksum via atomic write (temp file in same dir then rename). Support backward compatibility: old flat file (whole file is data), Turn1 format (`data`+`checksum` without shard_id/version) with valid checksum, and Turn2 versioned format. All corruption forms (invalid JSON, missing or empty checksum, checksum mismatch, shard_id mismatch, missing/invalid version) must trigger repair.

On write (`set`/`delete`): always write new versioned format with incremented version, correct shard_id, valid checksum without HTML escaping, atomically via temp file then rename.

Global broadcast keys (`global:` prefix): `set` writes to all shards (each version bumps), `get` must return first found scanning shards in id order then legacy fallback, `delete` deletes from all, `get-shard-id` returns -1, `get-shard-path` returns all paths comma-separated sorted by id.

Transaction log `/app/data/ops.log`: on successful `set`/`delete` append a JSON line with `op`, `key`, `value` (for set), `ts` (unix nano), `shard_id` (or -1 for global), `version`. Use `O_APPEND`, create if missing, no HTML escaping. On read, skip invalid JSON lines with warning and avoid infinite loop on corrupt line (use Scanner).

`get` fallback: normal keys check designated weighted shard first then legacy (`--legacy` default `/app/data/legacy.json`); global keys check all shards in id order then legacy.

`list-keys`, `distribution`, `ops-log`: `list-keys` union shards+legacy deduped sorted exact, `distribution` includes all shard ids even zero and counts broadcast keys per shard, `ops-log` prints JSON array skipping invalid lines with warning.

`set` value parsing and self-healing: if value_json is not fully valid JSON after trimming whitespace, treat as raw string (no lenient streaming). After writing correct shard, clean up misplaced/duplicate copies in other shards; `delete` must clean all shards where key exists. Version bumps per modified shard.

Empty string `""` is valid key, hashed via MD5 (`d41d8cd98f00b204e9800998ecf8427e` → weighted routing), not missing-arg error. Missing key argument (zero args) exits 2 with no stdout.

Help must document all commands and flags (including staging, version, checksum, timestamp, etc.) and exit 0 on bare args, `--help`, `-h`, `help`. `migrate --help` must document its flags. Unknown command/flag exits 2; invalid config exits 2 with no stdout.

### 2. Migration subcommand

```
proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```
Flags may appear before or after `migrate` subcommand; harness tries both orders.

Legacy reading: old flat dict. Missing file or invalid JSON → exit 1 with stderr. Empty `{}` → print “Legacy file is empty, nothing to migrate” but still perform cleanup and ops.log replay and honor backup if requested, exit 0. Handle large legacy.

Shard reading: support all formats as in proxy init with same corruption handling (backup + warning, treat as empty).

Ops.log reading: line by line via Scanner, skip invalid with warning, collect valid entries. Detect duplicate non-global keys across shards and log warning. Entries may be out-of-order by ts.

Cleanup: for any non-global key whose weighted destination does not match current shard, move it to correct shard; if present in multiple shards, keep only in correct shard after migration. Global keys must be replicated to all shards – after migration every shard contains each global key from legacy and existing global keys.

Migration writes: group legacy keys per shard (normal weighted, global to all), batched one atomic write per shard with version old+1 if changed. Must create staging dir `/app/data/staging` and ensure staged artifacts are durable and byte-equal to final shards (final writes durable and atomic via temp file then rename; staged artifacts byte-equal). No ordering guarantee beyond durability and byte-equality – final writes are the source of truth.

Tombstone replay: after legacy + cleanup merge, replay ops.log entries in ascending `ts` order, stable for ties (missing `ts` treated as 0), applying set/delete to correct shards (weighted for normal, all for global), each replay bumps version. This ensures deletes prevent legacy resurrection and later ts wins.

Other semantics: preserve existing shard keys unless `--force`, in which case overwrite and log `Overwriting key 'X' in shard Y`. Dry-run prints plan with total legacy keys, per-shard counts, misplaced/dup/ops counts mentioning cleanup/version/shard_id, but must not modify any file. Backup: with `--backup`, copy legacy to backup path (mkdir -p), each modified shard to `<shard>.bak` containing old data, and ops.log to `.bak` if exists. Handle corrupted shards during migration same as init. Must handle 1MB+ JSON values atomically. Bad args or bad config (duplicate id, empty path, negative id, id>=count, weight<=0, etc.) → exit 2 no stdout. Exit codes: 0 success (including help, get null, empty legacy), 1 missing/invalid legacy/I-O, 2 invalid config/args.

### 3. Post-migration

- New writes work with weighted routing, broadcast, version increment, ops.log.
- After migration, proxy can serve all legacy keys (including global) even if legacy file removed.
- Shard files valid versioned format with correct shard_id, version incremented, valid checksum.
- No wrong-shard non-global keys; global keys replicated.
- distribution, list-keys, ops-log behave as described.

### Constraints

- Go stdlib only, `go.mod` no external requires.
- Build via `go build -o <binary> .`
- Weighted MD5 big-endian, no HTML escaping, broadcast, versioned integrity, atomic temp file then rename, batched per shard grouping.
- Use `/tmp/codimango` for temp files, not hardcoded `/tmp/proxy`.
- ops.log must avoid Decoder infinite loop.

### Example

```bash
go build -o ./proxy .
./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json set global:cfg '{"val":1}'
./proxy --config /app/config.json get-shard-id ""
./proxy --help
./proxy migrate --help
./proxy --config /app/config.json --legacy /app/data/legacy.json --ops-log /app/data/ops.log migrate --dry-run
./proxy migrate --config /app/config.json --legacy /app/data/legacy.json --backup /tmp/codimango/backup.json --force
```

Implement at `/app/` – Turn1 solution replayed before grading (inherit_prior_session=true).
