# Turn 2: Genomics Fleet at Scale – Sharded, Rate-Limited, Health-Aware (Go, 78 tests)

Turn1 built durable single-file core. Now fleet is 10k sequencers, 100k pipeline jobs. Single file and first-fit are too slow/fragmented. We implement sharded storage, best-fit defrag, per-sequencer token buckets, health TTLs, snapshot/restore, ops-log, optimize.

Turn1 binary present via `inherit_prior_session`; must keep Turn1 working in fallback mode.

## Output contracts (exact) – extends Turn1
- `status` same as Turn1 (integer keys total_nodes, total_jobs, allocated_jobs, pending_jobs, total_resources/used_resources as {"cpu":N,"memory":N,"gpu":N})
- `schedule` and `allocate` JSON same as Turn1: `{"job_id":...,"node_id":...,"scheduled":true}` / `{"job_id":...,"node_id":...,"allocated":true}` – SAME node no-op exit 0, DIFFERENT node exit 2
- Job JSON: `node_id` is `""` empty string never null when unallocated
- `get-node` / `get-job` nonexistent → exit 2 no stdout
- `list-nodes [limit] [offset]`: limit 0 = no limit (all), negative → exit 2. Numeric parsing same as Turn1: Go `strconv.Atoi` semantics – leading zeros (`00`→0) and plus sign (`+4`→4, `-0`→0) are valid; hex (`0x10`), float (`2.0`), whitespace (`" 2 "`), empty, non-numeric → exit 2; `-0` valid as 0 wherever 0 valid.
- Corrupt data file handling same as Turn1: copy to `<path>.corrupt.<unix-nanos>`, warn stderr containing `corrupt` or `checksum`, continue EMPTY store exit 0 so `list-nodes` prints `[]`

## Help contract (exact)
`--help` / bare argument or `help` subcommand must print help containing ALL of these keywords (checked lowercased): `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-node-health`, `list-healthy`, `snapshot`, `restore`, `ops-log`, `optimize`, `data`, `checksum`, `shard`, `weight`. Exit 0. Note: help output must contain the full literal command names – tests assert substring presence, so do not use abbreviated forms. Extra aliases `get-presence` (alias for `get-node-health`) and `list-online` (alias for `list-healthy`) are allowed but not required for help test.

## Rate-limit contract – full token bucket spec
Token bucket throttles `allocate` and `schedule` per sequencer node, not globally:
- Config: `rate_limit.allocations_per_second` (float/int, tokens refilled per second) and `rate_limit.burst` (max tokens). Defaults if missing in config: rate 5, burst 10. Must be >0 else invalid config → exit 2.
- Per-node independent buckets stored in `rate_limit_path` wrapper file (`{"data": {"nodeA": {"tokens": float, "last_refill": int_nanosec}, ...}, "checksum": md5}`) with same canonicalization as Turn1 (sort_keys, separators (',',':'), ensure_ascii=False, SetEscapeHTML(false), U+2028/U+2029 escaped handling).
- Bucket fields per node: `tokens` float (remaining), `last_refill` int (unix nanoseconds from `time.Now().UnixNano()`).
- Refill timing: on each allocate/schedule attempt for node N, compute elapsed = (now_nano - last_refill)/1e9, new_tokens = min(burst, tokens + elapsed*rate), update last_refill=now_nano, set tokens=new_tokens before consume check.
- Consume: if tokens >= 1 → tokens -= 1, persist bucket, proceed to allocate/schedule logic (may still fail with insufficient → see no-consume rule). Else (tokens < 1) → persist refilled bucket (no consume), reject: exit 1 (not 2), no stdout, stderr contains `rate limit` substring (case-insensitive check), no allocation, no ops-log entry, no change to node's used/free.
- No-consume on insufficient: if allocation fails due to insufficient resources (exit 2 `insufficient`) or no-fit (exit 1 `no fit` for schedule), token bucket must NOT be consumed – tokens remain as refilled, same as before attempt. Only successful allocate/schedule consumes 1 token. Rate-limit rejection (exit 1 rate limit) also does not consume beyond refill (it persists refilled count).
- Per-node independence: exhausting bucket for nodeA must not affect nodeB.
- Persistence across restarts: bucket file must survive process restarts and be read atomically with lock `/app/data/global.lock`. Concurrent CLI invocations must not corrupt it (use same lock/cleanup as shards).
- Corruption recovery: if rate_limit file is invalid JSON, missing checksum, checksum mismatch, or data not object → treat as empty (reset to burst implicitly), allow current allocation to succeed (exit 0), reinitialize file with valid wrapper containing this node's bucket after consume.

## Snapshot/restore contract – exact signatures and exit codes

- `snapshot <backup_path>` – signature: exactly 1 arg (backup path), extra/missing → exit 2. Exit 0 on success, exit 2 if path is unwritable or otherwise fails. Must not leave `.lock`/`.tmp.*` after.
  - Dir mode: if `<backup_path>` does NOT end with `.json` OR path exists and is a directory OR path has no extension and does not exist -> treat as directory: `mkdir -p <backup_path>`, then copy each sharded file: shards (`shard_0.json` etc from config), jobs file (`jobs_path`), presence (`presence_path`), rate_limit (`rate_limit_path`), counter (`counter_path`), ops_log (`ops_log`), config file (`/app/config.json` itself) into directory. File names preserved as basenames? Actually dir snapshot must create files inside dir with same basenames as sources, allowing restore to locate. Implementation may copy full container: each shard's raw bytes, jobs, presence, rate_limit, counter, ops_log, config. Must be recursive mkdir.
  - File mode: if `<backup_path>` ends with `.json` and does not exist as directory -> single file combined JSON: `{"shards": {"0": <shard_0 wrapper obj or raw data>, "1": ...}, "jobs": <jobs wrapper>, "presence": <presence wrapper>, "rate_limit": <rate_limit wrapper>, "counter": <counter>, "ops_log": <ops log array or raw>, "config": <config json>}` – structure must contain at least shards, jobs, presence, rate_limit keys so restore can reconstruct exactly. Tests only check that restore brings back nodes/jobs exact and post-snapshot mutations are gone, and that snapshot file exists.
  - After snapshot, further mutations (add-node/add-job) must not appear in snapshot file, but must appear in live DB.

- `restore <backup_path>` – signature: exactly 1 arg, extra/missing → exit 2. Exit 0 on success, exit 2 if backup missing/invalid/ unparseable, no stdout on invalid (per config invalid rule). Must restore EXACT state:
  - Dir mode: if path is directory (exists and is dir, or path without .json suffix that was previously snapshot dir) → read each file inside that dir and write back to original shard paths/jobs/presence/rate_limit/counter/ops_log/config locations, overwriting. Must be atomic (write temp then rename) and checksum-valid after. Post-restore, list-nodes must not contain nodes added after snapshot. All original files (shards, jobs, presence, rate_limit, ops_log) must be bit-exact restored (checksum valid).
  - File mode: if path ends with .json and file exists -> parse combined JSON, extract shards/jobs/presence/rate_limit/etc and write each to original locations. After restore, allocation must work (next allocate succeeds if resources).
  - After restore, global lock must be cleaned.

## Distribution contract
- `distribution` → signature: no args, extra → exit 2, exit 0 success. Prints JSON map `{"0":count,"1":count,...}` where shard_id is string of int id (from config), count is int node count in that shard INCLUDING global broadcast nodes in each shard, includes zeros for empty shards. Sum of values = total nodes if no global nodes, or = total_non_global + global_count * shard_count if global nodes present (since each global node counted in each shard). Must include all shard ids from config even if zero.

## Ops-log contract – exact signature and output
- `ops-log` → signature: no args, extra → exit 2, exit 0 success. Prints JSON array of log entries order preserved as appended.
  - Each entry JSON object must contain at least `{"op": "<operation>"}` where op is string like `allocate`, `schedule`, `add-node`, `add-job`. For allocate entries, additional fields may include `job_id`, `node_id`. Spec REQUIRES allocate (and schedule) to be logged, but add-node/add-job logging is optional – `test_ops_log_large_100_ops` expects >=50 entries after 50 add-node/add-job/allocate triplets and checks that allocate appears, not that add-node appears. Therefore allocate-only logging (50 entries) must pass.
  - Log file path from config `ops_log`. Append on each allocate/schedule (and optionally other ops). File may contain one JSON object per line (JSONL) or JSON array – but `ops-log` command must output JSON array (not JSONL) of all valid entries parsed from file in order.
  - Invalid lines handling: file may contain invalid JSON lines (injected). Command must skip invalid lines, not exit 2, and print warning to stderr containing `corrupt` or `skip` or `warning` substring. After skipping, output array must contain only valid entries in preserved order.
  - Large line handling: test `test_ops_log_single_200kb_line_big_buffer` writes a 200KB single JSON line; implementation must be able to read it. Go's `bufio.Scanner` default 64k must be increased to at least 10MB buffer (`scanner.Buffer(make([]byte, 0, 10*1024*1024), 10*1024*1024)`).
  - After 50 triplets, expects >=50 entries containing allocate.

## Optimize contract – exact signature and output
- `optimize` → signature: no args, extra → exit 2, exit 0 success. Prints JSON object with exact keys: `{"fragmentation_before": float, "fragmentation_after": float, "moves": int, "total_nodes": int, "used_nodes": int}`. All keys must be present, types as specified (floats may be JSON number, but must decode as float, not string).
  - Semantics: consolidates jobs onto fewer nodes to reduce fragmentation. Must preserve all jobs (total_jobs same before/after), no overcommit (for each node, used <= total for cpu,memory,gpu), jobs not lost.
  - `total_nodes`: total number of nodes in cluster (including empty).
  - `used_nodes`: number of nodes that have at least one allocated job after optimize.
  - `moves`: number of job relocations performed (>=0).
  - `fragmentation_before` / `fragmentation_after`: float metric of fragmentation; implementation may compute as average waste or similar. Requirement: `fragmentation_after <= fragmentation_before + 1e-9` OR `used_nodes` after <= used before (so either fragmentation improves or uses fewer nodes).
  - Must be atomic under global lock, no leftover lock/tmp files, checksum valid.

## Difficulty re-balance
After publishing exact contracts, difficulty in prior-violating semantic: best-fit tie-break chain terse (must infer waste cpu→mem→gpu→id lex), not unstated contracts or scale.

## Flags & Config

- `--data` default `/app/data/cluster.json` – legacy single-file fallback.
- `--config` default `/app/config.json` – sharded config.
  - If config file missing → fallback to single-file mode (backward compat, exit 0, empty arrays).
  - If config exists and valid → sharded mode.
  - If config exists but invalid (bad JSON, missing `shard_count`, `shard_count<=0`, missing/empty `shards`, duplicate shard id, empty path, weight<=0, negative id) → exit 2 no stdout.

Config format (unique vs `ci-scheduler-target-sharded` – weights are explicit per-sequencer throughput):
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}
  ],
  "rate_limit": {"allocations_per_second": 1000, "burst": 10000},
  "node_heartbeat_ttl_seconds": 60,
  "ops_log": "/app/data/cluster_ops.log",
  "jobs_path": "/app/data/jobs.json",
  "presence_path": "/app/data/presence.json",
  "rate_limit_path": "/app/data/rate_limit.json",
  "counter_path": "/app/data/counter.json"
}
```
`shard_count>0` required, `shards` non-empty unique ids, path non-empty, weight>0 else exit2. Unknown fields ignored (future-proofing).

## Sharded semantics – distinct from `container-resource-allocator`

- Nodes sharded via weighted MD5 hash of nodeID (like database-sharding but for genomics sequencers).
- Jobs in `jobs_path` wrapper file.
- `add-node <id> <cpu> <mem> <gpu>`: idempotent preserve old, broadcast if `global:` prefix → `get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted paths, stored in all shards. Normal IDs stored in single shard determined by weighted hash.
- `remove-node`: true/false, fails exit 2 if allocated jobs exist.
- `list-nodes [limit] [offset]`: sorted asc, limit 0 = all, offset beyond = [], invalid → exit 2 per numeric contract (leading zeros and plus valid, hex/float/whitespace invalid), performance 2000 nodes <2s.
- `get-node`: finds across shards.
- Jobs: `add-job` idempotent preserve old and allocation, `remove-job` true/false deallocates first (jobs becomes [] not null), `list-jobs` same pagination, `get-job`.
- `allocate`, `deallocate`, `schedule`, `status` work across shards under global lock `/app/data/global.lock` – no leftover `.lock` or `.tmp.*`.
- `schedule`: NOW BEST-FIT for scale (vs Turn1 first-fit) – most efficient packing (tie-break chain intentionally terse, must be inferred: waste cpu → mem → gpu → id lex). If already allocated → exit 2, no fit → exit 1 stderr "no fit" no stdout.
- Node JSON `jobs` always `[]` not `null`.

### Presence – sequencer health TTL

- `heartbeat <nodeID>`: signature 1 arg, missing/extra → exit 2, requires node exists else exit 2. Updates `presence.json` last_seen nano, requires node exists else exit 2.
- `get-presence` / `get-node-health`: both return `{"node_id":..,"online":bool,"last_seen":int,"last_seen_seconds_ago":float}` where online = now-last_seen <= TTL*1e9. Never seen → online false, last_seen 0, ago 0. Signature 1 arg, missing/extra → exit 2, exit 0 even if offline (only missing node arg cases are existence checks – but get-presence for never-seen returns offline exit 0).
- `list-healthy` / `list-online`: signature no args, extra → exit 2, returns sorted JSON array of online node IDs within TTL.
- Corrupt presence.json → treated empty, offline.

### New commands – full list with signatures

```
get-shard-id <nodeID>      -> prints int weighted hash, -1 for global:, "" empty string valid (MD5 d41d8cd98f00b204e9800998ecf8427e). Signature 1 arg, missing/extra → exit2, exit0 success. Output trimmed int string plus newline.
get-shard-path <nodeID>    -> single path or comma-separated sorted list for global:. Signature 1 arg, missing/extra → exit2.
distribution               -> map shard_id string -> node count including global in each shard, includes zeros. Signature no args.
heartbeat <nodeID>         -> exit 0 success, exit 2 if node not exist or missing/extra args.
get-presence <nodeID> / get-node-health -> see presence spec.
list-healthy / list-online -> sorted online nodes within TTL, no args.
snapshot <backup_path>     -> dir mode (no .json suffix or existing dir): mkdir -p copy shards+jobs+presence+rate_limit+counter+ops_log+config; file mode (.json): combined JSON {shards:{id:fileData},jobs,presence,rate_limit,counter,ops_log,config}. 1 arg, exit 0 success else 2.
restore <backup_path>      -> dir/file modes restore exactly, post-snapshot mutations gone, next allocate works. 1 arg, exit0/2.
ops-log                    -> prints ops log as JSON array, skips invalid JSON lines with warning stderr containing corrupt/skip/warning, order preserved, needs big bufio.Scanner 10MB buffer. No args, exit0 success. After 50 add-node/add-job/allocate triplets, expects >=50 entries containing allocate.
optimize                   -> {"fragmentation_before":float,"fragmentation_after":float,"moves":int,"total_nodes":int,"used_nodes":int} – consolidates jobs onto fewer nodes, no overcommit, preserves all jobs, fragmentation_after <= before OR used_nodes_after <= before. No args, exit0 success.
```

Help must contain keywords (lowercased) `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-node-health`, `list-healthy`, `snapshot`, `restore`, `ops-log`, `optimize`, `data`, `checksum`, `shard`, `weight`.

### Weighted sharding

- weight default 1 if missing but must be >0 else invalid.
- totalWeight = sum weights
- hashInt = int(MD5(key.encode()).hexdigest(),16) (Python semantics)
- weighted_index = hashInt % totalWeight
- Iterate shards sorted by id asc subtracting weight.
- global: prefix → -1 broadcast.

### Ops-log contract clarification (fix for previous BAD_GRADING_WRONG)

- `ops-log` prints JSON array of logged operations.
- Spec requires **allocate** (and schedule) to be logged. `add-node`/`add-job` logging is NOT required for correctness (see `test_ops_log_and_skip_invalid` comment).
- Therefore `test_ops_log_large_100_ops` after 50 triplets must expect `>=50` entries (allocate-only), not `>=100`. It checks allocate presence, not count of add-node.
- Invalid lines in ops log file must be skipped with warning.

Implement at `/app` – Turn2 efficient scale.
