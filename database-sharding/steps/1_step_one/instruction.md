# Turn 1: Sharding Proxy in Go – Integrity, Validation, Corruption Repair (Eased, Explicit Empty-String Handling)

## Background

Traffic has outgrown a single database. We have 4 new shards provisioned:

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

For this turn, keep routing **simple** (MD5 mod, not weighted) and no broadcast/global or ops.log – those will be added in Turn2 to make it harder. Focus is on robust sharding with integrity.

## Task – Implement Go proxy at `/app/` (module `sharding`), built via `go build -o <binary> .`

### CLI (MUST)

Global `--config` default `/app/config.json`.

Help explicitly required (bare help):

- Bare proxy with **no args** (`proxy` or `proxy --config X` with no command) must print help to stdout containing `get-shard-id`, `set`, `get`, `delete`, `list-keys`, `distribution`, `config` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- Unknown command → exit 2.

Commands (simple, no global/broadcast, no weight, no ops.log in this eased Turn1):

```
proxy --config /app/config.json get-shard-id <key> -> int, exit 0
  Simple MD5 mod: shard_id = MD5(key) big-endian int % shard_count

proxy --config /app/config.json get-shard-path <key> -> path, exit 0
proxy --config /app/config.json get <key> -> JSON value or "null", exit 0 even if null, no stdout on invalid config
proxy --config /app/config.json set <key> <value_json> -> durable, exit 0
  value_json is JSON; if not valid JSON, treat as raw string value
proxy --config /app/config.json delete <key> -> "true"/"false", exit 0
proxy --config /app/config.json list-keys -> sorted JSON array deduped, reads all shards, sorted lexicographically, exit 0
proxy --config /app/config.json distribution -> JSON map shard_id (string) -> count, includes all ids even zero, exit 0
```

Exit codes:
- 0 success (including help, get null)
- 1 I/O error
- 2 unusable input: missing config, invalid JSON config, duplicate shard id, empty path, negative id, id>=count, missing shard_count, bad args, unknown command, missing <key> arg, **empty string key "" (see below)**. For invalid config, **no stdout only stderr**.

### Empty String Handling – Explicit to Avoid Oracle Null Ambiguity (per reviewer Request changes)

- **For this task, empty string "" IS NOT considered a legitimate key** and should be treated as **invalid input** (same as missing key argument). This is to avoid ambiguity: Oracle DB treats empty string as null, and there is no previous data with empty key in our dataset, so a human reasonably treating "" as null/invalid should not be faulted.
- Therefore:
  - `proxy get-shard-id ""` (empty string passed as `proxy get-shard-id ""`) must **exit 2** with no stdout (only stderr), not exit 0. This distinguishes from missing arg `proxy get-shard-id` (zero args) which also exits 2, but empty string is also invalid and not enforced as valid key.
  - `proxy set "" <value>`, `proxy get ""`, `proxy delete ""` must also exit 2, no stdout.
  - Tests **do NOT include empty string "" as valid key** – they only test missing arg case via `test_missing_key_arg_exit_2`. The previous discriminator `test_get_shard_id_uses_md5` that included "" has been removed to avoid ambiguous expectations per Human Checks.
- If you implement empty string as valid (hash it), you will still pass visible tests (since "" not in them), but you will be violating spec – we will not penalize you for extra handling of "" as valid as long as missing arg exit 2 works, but spec says empty should be invalid to avoid ambiguity. **Explicit note: Do NOT treat "" as valid; treat as invalid input exit 2.**

- Shell distinction reminder: `proxy get-shard-id ""` has 1 arg (empty) vs `proxy get-shard-id` has 0 args (missing) – both should exit 2 for this task (empty is not legitimate), but we test only missing case to avoid ambiguity. The empty-string case is intentionally NOT enforced to avoid penalizing either interpretation, but spec says it SHOULD be considered invalid.

### Sharding Algorithm (MUST, simple mod for eased Turn1)

- Use MD5 for stability: `hash = MD5(key)` 16-byte digest as big-endian unsigned integer, `shard_id = hash % shard_count`. Must match Python: `int(hashlib.md5(key.encode()).hexdigest(),16) % shard_count`. Do not use CRC32/FNV/random. For Turn1 eased, use **simple mod**, not weighted (weighted will be added in Turn2 to make it harder).

### Persistence with Integrity Header (unique but eased)

Shard file format:

```json
{
  "data": { "key": value, ... },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```

- **Canonical data JSON for checksum**: **sorted keys, no spaces, without HTML escaping**. Python `json.dumps(data, sort_keys=True, separators=(',', ':'))` must match Go `json.Marshal` with `SetEscapeHTML(false)` disabled (Go default escapes `<>&` as `\u003c` etc – must disable, tested via `<>&` value). Example empty shard: canonical data `{}` → MD5 `99914b932bd37a50b983c5e7c90ae93b`, file = `{"data":{},"checksum":"99914b932bd37a50b983c5e7c90ae93b"}`.
- On write: always new format with correct checksum, atomic via `os.CreateTemp` in same dir + `os.Rename`. **Source inspection**: must contain `CreateTemp` and `Rename` to prove atomicity per R07.
- On read and **initialization**: `NewShardingProxy` must **validate and repair every configured shard before any command** (not just on-demand). For each shard:
  - Missing/empty → empty `{}`
  - Has `data` field: require `checksum` present and non-empty, else corruption (missing checksum → corruption per feedback). Compute expected checksum from `data` canonical (sorted keys, no spaces, no HTML escaping). Mismatch → corruption.
  - Corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt"/"checksum", recreate empty with valid checksum.
  - No `data` field → old flat format `{key: value}` backward compat, treat whole file as data dict, convert to new format on next write. Invalid JSON → corruption handling.
- Because init repairs every shard, `list-keys`/`distribution` reading all shards triggers repair for any corrupted shard (fixes earlier bug where missing-checksum test corrupted wrong shard never read – now we corrupt actual hashed shard and use list-keys reading all).

- `get` reads from disk each time.
- `list-keys` sorted exact, `distribution` includes zeros.

### Config Validation

On startup, validate config, else exit 2 stderr, **no stdout**:
- shard_count>0, len(shards)>0, ids unique, non-negative, <count, path non-empty
- Config missing/invalid JSON → exit 2, no stdout

### Constraints

- Go stdlib only (`go.mod` no external requires, `go list` imports no dot)
- Builds via `go build -o <binary> .`
- Respect `--config`
- No hardcoded `/tmp/proxy`, use `/tmp/codimango` if tmp needed

### Success Turn1 (eased)

- Correct simple MD5 mod routing (not weighted)
- Durable new-format files with valid checksum (no HTML escaping)
- Sorted list-keys exact, distribution includes zeros
- Config validation exit 2 no stdout, corruption backup/recreate including missing checksum case (targeting correct hashed shard), raw-string handling, help flag and bare help exit 0, empty-string explicitly invalid (not enforced as valid key to avoid ambiguity)
- Legacy ignored
