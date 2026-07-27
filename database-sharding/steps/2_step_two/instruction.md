# Turn 2: Handle Existing Data Migration Properly (Go) – Integrity, Duplicates, Fallback, Help

## Background

Turn1 proxy works for new writes but breaks historical reads (`/app/data/legacy.json` old flat format). Production also has:
- Corrupted shard files (invalid JSON or checksum mismatch or missing checksum).
- Duplicate keys across multiple shards from prior buggy migration (same key in shard 0 and shard 2, but should only be in MD5-designated shard).
- Users need zero-downtime migration.

## Task

### 1. Proxy fallback + robustness (keeps Turn1 integrity format)

Shard file format:
```json
{"data": { ... }, "checksum": "md5_hex_of_canonical_data_json"}
```
- Canonical data JSON: sorted keys, no spaces, must match Go `json.Marshal(data)` and Python `json.dumps(data, sort_keys=True, separators=(',', ':'))`. Checksum = md5 hex of canonical.
- On read: if file has `data` field, require `checksum` present and valid. Missing checksum or mismatch → corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt" or "checksum", recreate empty with valid checksum. If no `data` field, treat whole file as old flat dict (backward compat). Invalid JSON → corruption handling.
- On write: always new format with correct checksum, atomic temp+rename.
- Config validation exit 2, no stdout on invalid config.

- `get` must: check designated shard first; if not found, check legacy file (`--legacy` default `/app/data/legacy.json`, old flat format) if exists. Zero-downtime.
- `list-keys` union shards + legacy, deduped sorted.
- `distribution` counts only shards, includes zeros.

Help is explicitly required (fixes R02/R03/R08):

- Bare proxy with **no command** (`proxy` or `proxy --config X` or `proxy --config X --legacy Y` with no subcommand) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `migrate`, `config`, `legacy`, `dry-run`, `backup`, `force` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- `migrate --help` or `proxy migrate --help` or `proxy --help migrate` etc must print help containing `dry-run`, `backup`, `force` and exit 0.
- Unknown command or unknown migrate flag must exit 2.

### 2. Migration subcommand (same binary) with duplicate cleanup

```
proxy --config /app/config.json --legacy /app/data/legacy.json migrate [--dry-run] [--backup /path/to/backup.json] [--force]
```

Harness tries both `<binary> --config X --legacy Y migrate ...` and `<binary> migrate --config X --legacy Y ...`

Requirements beyond generic template:

- **Read legacy** JSON dict (old flat), missing → exit 1 stderr (not 2), invalid JSON → exit 1 stderr, empty `{}` → print "Legacy file is empty, nothing to migrate", still backup if requested, exit 0, but still perform duplicate/wrong-shard cleanup if any (i.e., even empty legacy must trigger cleanup of misplaced keys).
- **Detect inconsistent state**: scan all shards (both old flat and new checksum formats, with corruption backup handling). Build key → [shard ids]. If same key in multiple shards, log warning stderr `Warning: key "dup" found in multiple shards [0 1]`.
- **Cleanup wrong-shard duplicates**: migration must **remove wrong-shard duplicate copies and leave only correct shard copy** unless `--force` overwrites values. For any key whose hash != shard id where it currently lives, remove from wrong shard. If key exists in multiple shards including correct, keep only in correct. After successful migration, no shard contains key that doesn't hash to it.
  - Example: key `user:1` hashes to 2, but exists in 0 and 1. After migration, only shard 2 has it.
  - Regression test seeds same key in multiple shards and asserts only MD5-designated shard retains it.
- **Group legacy keys** by destination MD5 big-endian mod.
- **Batched atomic writes**: per shard needing changes (new legacy + cleanup), write once atomically.
- **Preserve vs force**: preserve existing shard keys unless `--force`. With `--force`, overwrite with legacy values and log stderr `Overwriting key 'X' in shard Y`.
- **Idempotency**: second run identical.
- **No data loss**: after migration, every legacy key reachable via `get`.
- **Dry-run**: with `--dry-run`, print plan:
  ```
  Migration plan: 30 keys from /app/data/legacy.json -> 4 shards
    shard 0 (/app/data/shard_0.json): 8 keys
  Dry-run enabled, not writing any shard files
  ```
  No modifications. If duplicates/misplaced exist, also mention cleanup in stderr.
- **Backup**: with `--backup <path>`, copy legacy to backup path (mkdir -p) and each shard to `<shard>.bak` before overwriting. Test checks both legacy backup and shard `.bak` exist and contain old data.
- **Corrupted shard handling**: if shard file invalid JSON or checksum mismatch or missing checksum, backup to `.corrupt.<timestamp>` and treat as empty, then migrate into it successfully.
- **Empty/invalid legacy**: empty → message + backup if requested, but still cleanup misplaced; invalid JSON → exit 1 stderr.
- **Bad args**: unknown migrate flag, bad args, missing required key for subcommands → exit 2. Bad config for migrate (duplicate id etc.) → exit 2, no stdout.
- Exit codes: 0 success (help 0, get null 0), 1 I/O/missing legacy/invalid legacy, 2 invalid args/config.

### 3. Post-migration

- New writes work.
- After migration, proxy gets all legacy keys even if legacy file removed.
- Shard files in new checksum format with valid checksum.
- No wrong-shard keys left after migration.

### Constraints

- Stdlib only: `go.mod` no external requires **and** `go list -f '{{join .Imports " "}}'` must not contain external paths (e.g., no `github.com/...` imports). Test will inspect `go list` imports.
- Build via `go build -o <binary> .`
- Same MD5 big-endian mod.
- Atomic writes, validation, corruption backup, duplicate cleanup.
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
