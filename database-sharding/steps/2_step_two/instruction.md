# Turn 2: Handle Existing Data Migration Properly (Go) – Integrity, Duplicates, Fallback, Help

## Background

Turn1 proxy works for new writes but breaks historical reads (`/app/data/legacy.json` old flat format). Production also has:
- Corrupted shard files (invalid JSON or checksum mismatch or missing checksum).
- Duplicate keys across multiple shards from prior buggy migration (same key in shard 0 and 2, but should only be in MD5-designated shard).

We need zero-downtime migration with integrity and cleanup.

## Task

### 1. Proxy fallback + robustness (keeps Turn1 integrity format)

Shard file format:
```json
{"data": { ... }, "checksum": "md5_hex_of_canonical_data_json"}
```
- Canonical data JSON: **sorted keys, no spaces, without HTML escaping**. Python: `json.dumps(data, sort_keys=True, separators=(',', ':'))`. Go: `json.Marshal(data)` sorts keys and no spaces, but **default Go escapes `<`, `>`, `&` as `\u003c` etc**. You **must disable HTML escaping** (`json.Encoder.SetEscapeHTML(false)`) so checksum matches Python's no-escaping. If you use default Marshal, checksum for values containing `<>&` will mismatch.
  - Example: data `{"x":"<>&"}` canonical with separators is `{"x":"<>&"}` (Python) – Go must produce same without `\u003c`, so disable escaping.
  - Checksum = md5 hex of canonical.

- On read and initialization: Proxy initialization must **validate and repair every configured shard before any command**. For each shard:
  - If file missing/empty → empty
  - If has `data` field: require `checksum` present and valid. Missing/empty checksum → corruption. Mismatch → corruption. Corruption → backup `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt" or "checksum", recreate empty with valid checksum.
  - If no `data` field → old flat dict backward compat. Invalid JSON → corruption handling.
  - Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair for any corrupted shard.

- On write: always new format with correct checksum (no HTML escaping), atomic temp file in same dir + `os.Rename`. Source inspection will check for `CreateTemp` + `Rename`.

- `get` must: check designated shard first; if not found, check legacy file (`--legacy` default `/app/data/legacy.json`, old flat format) for fallback, zero-downtime.
- `list-keys` union shards + legacy, deduped sorted.
- `distribution` counts only shards, includes zeros.

Help explicitly required (fixes R02/R03/R08):

- Bare proxy with **no command** (`proxy`, `proxy --config X`, `proxy --config X --legacy Y`) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `dry-run`, `backup`, `force` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- `migrate --help`, `proxy migrate --help`, `proxy --help migrate`, etc must print help containing `dry-run`, `backup`, `force` and exit 0.
- Unknown command or unknown migrate flag/arg must exit 2, no stdout for invalid config case (stderr only).

### 2. Migration subcommand (same binary) with duplicate cleanup

```
proxy --config /app/config.json --legacy /app/data/legacy.json migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries `<binary> --config X --legacy Y migrate ...` and fallback `<binary> migrate --config X --legacy Y ...`

Requirements beyond generic template:

- **Read legacy** JSON dict (old flat), missing → exit 1 stderr (not 2), invalid JSON → exit 1 stderr, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, and still perform duplicate/wrong-shard cleanup if any, exit 0.
- **Read shards**: for each shard, use same integrity logic (supports old flat and new checksum formats, with corruption backup). If shard corrupted (invalid JSON, checksum mismatch, missing checksum), backup to `.corrupt.<timestamp>` then treat as empty and continue migration.
- **Detect inconsistent state**: scan all shards, build key → [shard ids]. If same key in multiple shards, log stderr `Warning: key "dup" found in multiple shards [0 1]`.
- **Cleanup wrong-shard duplicates**: **Must remove wrong-shard copies and leave only correct shard copy** unless --force overwrites values. For any key whose hash != shard id where it lives, remove from wrong shard. If key exists in multiple shards including correct, keep only in correct. After migration, no shard contains key that doesn't hash to it.
  - Regression test: seed same key in multiple shards, assert only MD5-designated shard retains it after migration.
- **Group legacy keys** by destination MD5 big-endian mod.
- **Batched atomic writes**: per shard needing changes (new legacy + cleanup), write once atomically via temp+rename. **Source inspection**: your Go code must show grouping (e.g., map of shard→keys) and single write per shard, not one write per key in loop. Direct per-key file writes without grouping will be considered reward hacking per R07.
- **Preserve vs force**: preserve existing shard keys unless `--force`. With `--force`, overwrite with legacy values and log stderr `Overwriting key 'X' in shard Y`.
- **Idempotency**: second run identical.
- **No data loss**: after migration, every legacy key reachable via `get`.
- **Dry-run**: with `--dry-run`, print plan with total and per-shard counts, no modifications.
- **Backup**: with `--backup <path>`, copy legacy to backup path (mkdir -p) and **each relevant shard** that will be modified to `<shard>.bak` before overwriting. Must create legacy backup and each modified shard `.bak`. Test checks existence of specified legacy backup and each relevant shard `.bak` containing old data, and checks duplicate-warning stderr if spec says warning message.
- **Empty/invalid legacy**: empty → message + backup if requested, but still cleanup misplaced; invalid → exit 1 stderr.
- **Bad args**: unknown migrate flag, bare unknown arg after migrate, missing required key arg for subcommands → exit 2. **Migrate with bad config** (duplicate id, empty path, etc.) must exit 2, no stdout (only stderr).
- **Invalid config no stdout**: for both proxy commands and migrate with bad config, produce no stdout, only stderr message, exit 2.
- Exit codes: 0 success (help 0, get null 0), 1 I/O/missing legacy/invalid legacy, 2 invalid args/config.

### 3. Post-migration

- New writes work.
- After migration, proxy gets all legacy keys even if legacy file removed.
- Shard files in new checksum format with valid checksum (no HTML escaping) after migration.
- No wrong-shard keys left.

### Constraints

- Stdlib only: `go.mod` no external requires and `go list -f '{{join .Imports " "}}'` must not contain external dot paths.
- Build via `go build -o <binary> .`
- Same MD5 big-endian mod, no HTML escaping for checksum.
- Atomic writes via `CreateTemp`+`Rename`, validated via source inspection.
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed.

### Example

```bash
go build -o ./proxy .

./proxy --config /app/config.json set user:1 '{"id":1}'
./proxy --config /app/config.json get user:1
./proxy --help

./proxy --config /app/config.json --legacy /app/data/legacy.json get user:2
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --dry-run
./proxy --config /app/config.json --legacy /app/data/legacy.json migrate --backup /tmp/codimango/backup.json --force
./proxy migrate --help
```

Implement at `/app/` – Turn1 code present via inherit_prior_session.
