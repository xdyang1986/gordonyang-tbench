# Turn 1: Implement Database Sharding Proxy in Go – Integrity, Validation, Corruption Repair (Eased)

## Background

Traffic has outgrown a single database. We have provisioned 4 new shards on disk:

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

We focus on new traffic first. Legacy handling (existing data) will be addressed next iteration. For this turn, keep it **simple and robust**, not weighted or broadcast – those will be added in Turn2.

## Task – Implement Go proxy at `/app/` (module `sharding`), built via `go build -o <binary> .`

### CLI (MUST)

Global flag: `--config` default `/app/config.json`.

Help explicitly required and must be easy:

- Bare proxy with **no args** (`proxy` or `proxy --config X` with no command) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- Unknown command must exit 2.

Commands (simple, no global/broadcast, no weight, no ops.log in this turn):

```
proxy --config /app/config.json get-shard-id <key> -> int, exit 0
  Simple MD5 mod: shard_id = MD5(key) big-endian int % shard_count

proxy --config /app/config.json get-shard-path <key> -> path, exit 0
  Single path of designated shard

proxy --config /app/config.json get <key> -> JSON value or "null", exit 0 even if null, no stdout on invalid config

proxy --config /app/config.json set <key> <value_json> -> durable, exit 0
  value_json is JSON; if not valid JSON, treat as raw string value (store as string)

proxy --config /app/config.json delete <key> -> "true"/"false", exit 0

proxy --config /app/config.json list-keys -> sorted JSON array deduped, reads all shards, sorted lexicographically, exit 0

proxy --config /app/config.json distribution -> JSON map shard_id (string) -> count, includes all ids even zero, e.g., {"0":5,"1":3,"2":2,"3":0}, exit 0
```

Exit codes:
- 0 success (including help, get null)
- 1 I/O error
- 2 unusable input: missing config, invalid JSON config, duplicate shard id, empty path, negative id, id>=count, missing shard_count, bad args, unknown command, missing <key>. For invalid config, **no stdout only stderr**.

### Empty String Key Handling – Explicit (to avoid Oracle null ambiguity)

- An empty string `""` **IS a valid, provided key** for this task and must be hashed via MD5, MD5("") = `d41d8cd98f00b204e9800998ecf8427e`, routed via simple mod, supports `set ""` / `get ""`.
- This is distinct from missing key argument (e.g., `proxy get-shard-id` with zero args → exit 2, tested in `test_missing_key_arg_exit_2`). Empty string `""` is provided (len 1, value empty) and must be valid.
- Shell distinction: `proxy get-shard-id ""` has 1 arg (empty), `proxy get-shard-id` has 0 args (missing) → only latter exit 2.
- Although Oracle DB treats empty as null, for this task empty is NOT null and NOT missing – spec explicitly says this with example.

### Sharding Algorithm (MUST, simple mod for Turn1 eased version)

- Use MD5 for stability: `hash = MD5(key)` 16-byte digest as big-endian unsigned integer, `shard_id = hash % shard_count`. Must match Python: `int(hashlib.md5(key.encode()).hexdigest(),16) % shard_count`. Do not use CRC32/FNV/random. For Turn1 eased, use **simple mod**, not weighted (weighted will be added in Turn2).

### Persistence with Integrity Header (unique but eased)

Shard file format:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```

- Canonical data JSON for checksum: **sorted keys, no spaces, without HTML escaping**. Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)` disabled (Go default escapes `<>&` as `\u003c` etc – must disable, tested via `<>&` value). Example empty shard: canonical data `{}` → MD5 `99914b932bd37a50b983c5e7c90ae93b`, file = `{"data":{},"checksum":"99914b932bd37a50b983c5e7c90ae93b"}`.

- On write: always new format with correct checksum, atomic via `os.CreateTemp` in same dir + `os.Rename`. **Source inspection**: must contain `CreateTemp` and `Rename` to prove atomicity.

- On read and **initialization**: `NewShardingProxy` must **validate and repair every configured shard before any command** (not just on-demand). For each shard:
  - Missing/empty → empty `{}`
  - Has `data` field: require `checksum` present and non-empty, else corruption (missing checksum → corruption). Compute expected checksum from `data` canonical (sorted keys, no spaces, no HTML escaping). Mismatch → corruption.
  - Corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum.
  - No `data` field → old flat format `{key: value}` backward compat, treat whole file as data dict, convert to new format on next write. Invalid JSON → corruption handling.
- Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair for any corrupted shard (fixes earlier bug where missing-checksum test corrupted wrong shard never read).

- `get` reads from disk each time.
- `list-keys` sorted exact, `distribution` includes zeros.

### Config Validation (required)

On startup, validate config, else exit 2 stderr, **no stdout**:
- shard_count>0, len(shards)>0, ids unique, non-negative, <count, path non-empty
- Config missing/invalid JSON → exit 2, no stdout

Tests will include bad configs (duplicate id, empty path, negative id, missing count, invalid JSON, missing file) and verify exit 2 and no stdout.

### Constraints

- Go stdlib only (`go.mod` no external requires, `go list` imports no dot)
- Builds via `go build -o <binary> .`
- Respect `--config`
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed

### Success Turn1 (eased)

- Correct simple MD5 mod routing (not weighted for this eased Turn1)
- Durable new-format files with valid checksum (no HTML escaping) after set
- Sorted list-keys exact, distribution includes zeros
- Config validation exit 2 no stdout, corruption backup/recreate including missing checksum case, raw-string invalid JSON handling, help flag and bare help exit 0, empty-string key valid
- Legacy ignored
