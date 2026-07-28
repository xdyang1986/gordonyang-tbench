# Turn 2: Hard Migration with Weighted, Broadcast, Log Replay, Duplicate Cleanup

## Background

Turn1 proxy works for new weighted sharding + broadcast + ops.log, but breaks historical reads. Legacy file `/app/data/legacy.json` is old flat format (without checksum) containing both normal keys and `global:` broadcast keys (5 global configs). Additionally:

- Some shard files corrupted (invalid JSON, checksum mismatch, missing checksum)
- Duplicate keys across shards from prior buggy migration (same key in multiple shards, should only be in correct weighted shard)
- Transaction log `/app/data/ops.log` contains recent operations that override legacy values – must be replayed after legacy migration for latest state

We need zero-downtime migration with cleanup and log replay.

## Task

### 1. Proxy fallback + robustness (keeps Turn1 integrity + weighted + broadcast + ops.log)

Shard format remains: `{"data": {...}, "checksum": md5_hex(canonical_data)}` with canonical = sorted keys, no spaces, **no HTML escaping** (`SetEscapeHTML(false)` in Go, Python `separators=(',',':')`).

- Init must **validate and repair every shard before any command**.
- Corruption: invalid JSON, checksum mismatch, **missing checksum** → backup `.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum.
- Config validation: shard_count>0, ids unique, non-negative, <count, path non-empty, weight>0 if present. Missing/invalid → exit 2 no stdout.
- `get`: for normal keys, check designated weighted shard; if not found, check legacy file fallback (`--legacy` default `/app/data/legacy.json`, old flat). For `global:` keys, check all shards in id order, return first found, else check legacy fallback. Zero-downtime.
- `list-keys`: union shards + legacy, deduped sorted.
- `distribution`: counts only shards, includes zeros, counts broadcast keys in each shard (so sum may be >= unique).
- `set`: for normal, write to weighted designated shard; for `global:`, write to all shards. Append to ops.log.
- `delete`: for normal, delete from designated; for global, delete from all (true if any). Append to ops.log on success.
- Ops log: `/app/data/ops.log` append-only, each line JSON `{"op":"set"/"delete","key":...,"value":...,"ts":nanosec,"shard_id":...}`. On read (if you implement ops-log command), skip invalid JSON lines with stderr warning.

Help explicitly required (fixes R02/R03/R08):

- Bare proxy with no command (`proxy`, `proxy --config X`, `proxy --config X --legacy Y`) → help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `weight`, `global`, `ops.log`, `dry-run`, `backup`, `force` and exit 0.
- `--help`, `-h`, `help` → same help exit 0
- `migrate --help`, `proxy migrate --help` → help containing `dry-run`, `backup`, `force` exit 0
- Unknown command or unknown migrate flag/arg → exit 2, no stdout on invalid config

### 2. Migration subcommand (same binary) with weighted, broadcast, log replay, duplicate cleanup

```
proxy --config /app/config.json --legacy /app/data/legacy.json migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries both `<binary> --config X --legacy Y migrate ...` and `<binary> migrate --config X --legacy Y ...`

Requirements beyond generic template (hard, non-standard composition):

- **Read legacy**: JSON dict old flat, missing → exit 1 stderr, invalid JSON → exit 1, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, but **still perform duplicate/wrong-shard cleanup and ops.log replay** (even empty legacy must trigger cleanup), exit 0.
- **Read shards**: for each shard, support both old flat and new checksum formats, with corruption handling (backup to `.corrupt.<timestamp>` if invalid JSON, checksum mismatch, missing checksum), treat as empty and continue.
- **Read ops.log**: if exists, read line by line, skip invalid JSON lines with stderr warning containing "corrupt" or "invalid" or "warning", collect valid ops in order. Ops log may contain both normal and global keys.
- **Detect inconsistent state**: scan all shards, build key→[shard ids]. If same non-global key in multiple shards, log stderr `Warning: key "dup" found in multiple shards [0 1]`. For `global:` keys, duplicate across all shards is expected (broadcast), not considered inconsistency.
- **Cleanup wrong-shard and duplicate**: Migration must **remove wrong-shard duplicate copies and leave only correct weighted shard copy** (unless --force overwrites values). For any non-global key found in shard where `hash(key) % totalWeight` (weighted) != shard id, remove from wrong shard. If key exists in multiple shards including correct, keep only in correct. For `global:` keys, they should be in **all** shards after migration; if missing in some, replicate to missing. If non-global key exists in multiple wrong shards, move one copy to correct shard.
  - Regression: seed same non-global key in all shards, assert only correct weighted shard retains it after migration.

- **Weighted routing**: same weighted algorithm as Turn1 must be used for both proxy and migration (compute totalWeight = sum weights default 1). For `global:` keys, `get-shard-id` returns -1, but migration must replicate them to all shards.

- **Group legacy keys**: group by destination using weighted algorithm, but for `global:` keys, destination is all shards.

- **Batched atomic writes**: per shard needing changes (new legacy + cleanup + misplaced + global replication), write once atomically via `CreateTemp`+`Rename`. Source inspection will check for `CreateTemp`+`Rename` and grouping map.

- **Ops log replay after legacy**: after migrating legacy and cleaning duplicates/misplaced, replay ops.log in order, applying set/delete to correct shards (respecting weighted and broadcast). This ensures latest values win. For example, legacy has `user:1=old`, ops.log has `set user:1=new` → after migration, `user:1` should be `new`. For delete, ops.log delete should remove.

- **Preserve vs force**: preserve existing shard keys unless `--force`. With `--force`, overwrite with legacy values (and misplaced values) and log stderr `Overwriting key 'X' in shard Y`.

- **Dry-run**: prints plan with total and per-shard counts (including misplaced cleanup count), no modifications, mentions cleanup in stderr.

- **Backup**: with `--backup <path>`, copy legacy to backup path (mkdir -p), and **each relevant shard** that will be modified to `<shard>.bak` before overwriting, and also ops.log to `<ops.log>.bak` if exists. Must create legacy backup and each modified shard `.bak` containing old data.

- **Corrupted shard handling**: as proxy, backup to `.corrupt.<timestamp>` and treat as empty, then migrate into it.

- **Bad args**: unknown migrate flag, bare unknown arg after migrate, missing key arg → exit 2. **Migrate with bad config** (duplicate id, empty path, weight<=0, etc.) → exit 2, no stdout.

- **Invalid config no stdout**: for both proxy and migrate with bad config, no stdout, only stderr.

- Exit codes: 0 success (help 0, get null 0, empty legacy 0), 1 I/O/missing legacy/invalid legacy, 2 invalid config/args.

### 3. Post-migration

- New writes still work (including global broadcast and ops.log append)
- After migration, proxy gets all legacy keys (including global) even if legacy removed.
- Shard files in new checksum format (no HTML escaping) with valid checksum.
- No wrong-shard non-global keys left after migration; global keys present in all shards.

### Constraints

- Stdlib only: `go.mod` no external requires and `go list -f '{{join .Imports " "}}'` no dot imports
- Build via `go build -o <binary> .`
- Same weighted MD5 big-endian mod, no HTML escaping for checksum, broadcast handling
- Atomic writes via `CreateTemp`+`Rename`, validated via source inspection
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed

### Example

```bash
go build -o ./proxy .

./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json set global:cfg:1 '{"val":1}'  # broadcast to all
./proxy --config /app/config.json get global:cfg:1
./proxy --help

./proxy --config /app/config.json --legacy /app/data/legacy.json get user:2
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
./proxy migrate --help
```

Implement at `/app/` – Turn1 code present via inherit_prior_session.
