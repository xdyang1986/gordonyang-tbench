# Turn 1: Implement Database Sharding Proxy in Go (with integrity, validation, and repair)

## Background

Traffic has outgrown single DB. Historical dump is in `/app/data/legacy.json` (old flat format without checksum), but 4 new shards are provisioned:

- `/app/data/shard_0.json`
- `/app/data/shard_1.json`
- `/app/data/shard_2.json`
- `/app/data/shard_3.json`

Topology in `/app/config.json`:
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json"},
    {"id": 1, "path": "/app/data/shard_1.json"},
    {"id": 2, "path": "/app/data/shard_2.json"},
    {"id": 3, "path": "/app/data/shard_3.json"}
  ]
}
```

## Task

Implement Go sharding proxy at `/app/` (`module sharding`). Built via `go build -o <binary> .`.

### CLI (MUST)

Global flag: `--config` default `/app/config.json`.

Help explicitly required (bare help included):

- Bare proxy with **no args** (`proxy` or with only `--config X` and no command) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- Unknown command must exit 2.

Commands:

```
proxy --config /app/config.json get-shard-id <key> -> int, exit 0
proxy --config /app/config.json get-shard-path <key> -> path, exit 0
proxy --config /app/config.json get <key> -> JSON value or "null", exit 0 even if null, no stdout for invalid config
proxy --config /app/config.json set <key> <value_json> -> durable, exit 0
  value_json is JSON; if not valid JSON, treat as raw string value (store as string)
proxy --config /app/config.json delete <key> -> "true"/"false", exit 0
proxy --config /app/config.json list-keys -> sorted JSON array, deduped, sorted lexicographically, must read all shards, exit 0
proxy --config /app/config.json distribution -> JSON map shard_id (as string) -> count, includes all ids even zero, must read all shards, exit 0
```

Exit codes:
- 0 = success (including help and get returning null)
- 1 = I/O error
- 2 = unusable input: missing config, invalid JSON config, duplicate shard id, empty path, negative id, id >= shard_count, missing shard_count, bad args, unknown command, missing <key>. For invalid config, **produce no stdout**, only stderr.

### Sharding Algorithm (MUST)

Use MD5 for stability. Key's UTF-8 bytes are MD5 hashed, digest interpreted as big-endian unsigned integer mod shard_count. Must match Python: `int(hashlib.md5(key.encode()).hexdigest(),16) % shard_count`. Do not use CRC32/FNV/random.

### Persistence with Integrity Header (unique)

Shard file format:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data"
}
```

- **Canonical JSON for checksum**: Must be **sorted keys, no spaces, without HTML escaping**. In Python, use `json.dumps(data, sort_keys=True, separators=(',', ':'))` (note separators). In Go, `json.Marshal(data)` sorts map keys and produces no spaces, but **by default Go's encoder HTML-escapes `<`, `>`, `&` as `\u003c`, `\u003e`, `\u0026`**. For this task, you **must disable HTML escaping** so checksum matches Python. In Go, use `json.Encoder` with `SetEscapeHTML(false)` or ensure `json.Marshal` equivalent without HTML escaping. Example: empty data `{}` canonical is `{}` → MD5 `99914b932bd37a50b983c5e7c90ae93b`. So empty shard file = `{"data":{},"checksum":"99914b932bd37a50b983c5e7c90ae93b"}` (with indent in file but checksum computed from canonical without spaces).

- On **write**: always new format with correct checksum, atomic via temp file in same dir + rename. **Source inspection**: your Go code must contain `os.CreateTemp` (or `ioutil.TempFile`) and `os.Rename`/`os.Replace` to prove atomicity. Direct writes without temp+rename will be considered reward hacking per R07.

- On **read** and **initialization**: Proxy initialization (`NewShardingProxy`) must **validate and repair every configured shard before any command** – not just on-demand read. For each shard path, if file missing/empty → treat as empty. If file contains `data` field:
  - If `checksum` field missing or empty → treat as corruption per feedback
  - Else compute expected checksum from `data` canonical JSON (sorted keys, no spaces, no HTML escaping), compare to stored `checksum`. Mismatch → corruption.
  - Corruption handling: backup original to `<path>.corrupt.<nanosec>` (nanosec timestamp), log warning to stderr containing "corrupt" or "checksum", then recreate empty file with valid checksum and treat as empty.
  - If file has no `data` field (old flat format like `{}`) → treat whole file as data dict for backward compat, convert to new format on next write. Invalid JSON → corruption handling same.
- Because initialization repairs every shard, `list-keys` and `distribution` which read all shards will also trigger repair for any corrupted shard.

- `get` reads from disk each time (or after init repair).
- `list-keys` sorted, deduped.
- `distribution` includes zeros.

### Config Validation (required)

On startup, validate config, else exit 2 stderr, **no stdout**:
- shard_count >0, len(shards)>0, ids unique, non-negative, < shard_count, path non-empty
- If config missing or invalid JSON → exit 2, no stdout

### Constraints

- Go stdlib only (`go.mod` no external requires, and `go list -f '{{join .Imports " "}}'` must not show external paths containing dot).
- Builds with `go build -o <binary> .`
- Respect `--config`.
- No hardcoded `/tmp/proxy`; use `/tmp/codimango` if tmp needed.

### Success Turn1

- Correct MD5 mod routing.
- Durable new-format files with valid checksum (no HTML escaping) after set.
- Sorted list-keys exact, distribution includes zeros.
- Config validation exit 2 with no stdout, corruption backup/recreate (including missing checksum case), raw-string handling, help and bare help exit 0.
- Legacy ignored.
