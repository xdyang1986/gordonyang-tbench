# Turn 2: Genomics Fleet at Scale – Flowcell-Partitioned, Rate-Limited, Health-Aware

Turn1 built durable single-file core. Now fleet is 10k sequencers, 100k pipeline jobs. Single file and first-fit are too slow/fragmented. We implement flowcell-partitioned storage, best-fit defrag, per-sequencer token buckets, health TTLs, snapshot/restore, ops-log, optimize.

Turn1 binary present via `inherit_prior_session`; must keep Turn1 working in fallback mode.

## General CLI rules (applies to all commands below)
- Exact argument count as documented; missing or extra → exit 2, no stdout.
- Empty or whitespace-only IDs, non-integer or out-of-range numeric args → exit 2.
  Exception: `get-flowcell-id` and `get-flowcell-path` accept an empty-string node ID and hash it normally (MD5 of the empty string, d41d8cd98f00b204e9800998ecf8427e).
- On success exit 0 unless otherwise noted. No `.lock` or `.tmp.*` left after success or failure.
- Specific failures: `allocate` insufficient → exit 2 stderr `insufficient`; `schedule` no fit → exit 1 stderr `no fit`; rate-limit throttling → exit 1 stderr contains `rate limit`, no stdout.

## Output contracts (exact) – extends Turn1
- `status` same as Turn1 (integer keys total_nodes, total_jobs, allocated_jobs, pending_jobs, total_resources/used_resources as {"cpu":N,"memory":N,"gpu":N})
- `schedule` and `allocate` JSON same as Turn1: `{"job_id":...,"node_id":...,"scheduled":true}` / `{"job_id":...,"node_id":...,"allocated":true}` – SAME node no-op exit 0, DIFFERENT node exit 2
- Job JSON: `node_id` is `""` empty string never null when unallocated
- `get-node` / `get-job` nonexistent → exit 2 no stdout
- `list-nodes [limit] [offset]`: limit 0 = no limit (all), negative → exit 2. Numeric parsing same as Turn1: Go `strconv.Atoi` semantics – leading zeros (`00`→0) and plus sign (`+4`→4, `-0`→0) are valid; hex (`0x10`), float (`2.0`), whitespace (`" 2 "`), empty, non-numeric → exit 2; `-0` valid as 0 wherever 0 valid.
- Corrupt data file handling same as Turn1: copy to `<path>.corrupt.<unix-nanos>`, warn stderr containing `corrupt` or `checksum`, continue EMPTY store exit 0 so `list-nodes` prints `[]`

## Help contract (exact)
`--help` / bare argument or `help` subcommand must print help containing ALL of these keywords (checked lowercased): `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `get-flowcell-id`, `get-flowcell-path`, `distribution`, `heartbeat`, `get-node-health`, `list-healthy`, `snapshot`, `restore`, `ops-log`, `optimize`, `data`, `checksum`, `flowcell`, `weight`. Exit 0. Note: help output must contain the full literal command names – substring presence is checked, so do not use abbreviated forms. Extra aliases `get-presence` (alias for `get-node-health`) and `list-online` (alias for `list-healthy`) are allowed but not required for help check. Legacy partition aliases are allowed for compatibility.

## Rate-limit contract – full token bucket spec
Token bucket throttles `allocate` and `schedule` per sequencer node, not globally:
- Config: `rate_limit.allocations_per_second` (float/int, tokens refilled per second) and `rate_limit.burst` (max tokens). Defaults if missing in config: rate 5, burst 10. Must be >0 else invalid config → exit 2.
- Per-node independent buckets stored in `rate_limit_path` wrapper file (`{"data": {"nodeA": {"tokens": float, "last_refill": int_nanosec}, ...}, "checksum": md5}`) with same canonicalization as Turn1 (sort_keys, separators (',',':'), ensure_ascii=False, SetEscapeHTML(false), U+2028/U+2029 escaped handling).
- Bucket fields per node: `tokens` float (remaining), `last_refill` int (unix nanoseconds from `time.Now().UnixNano()`).
- Refill timing: on each allocate/schedule attempt for node N, compute elapsed = (now_nano - last_refill)/1e9, new_tokens = min(burst, tokens + elapsed*rate), update last_refill=now_nano, set tokens=new_tokens before consume check.
- Consume: if tokens >= 1 → tokens -= 1, persist bucket, proceed to allocate/schedule logic (may still fail with insufficient → see no-consume rule). Else (tokens < 1) → persist refilled bucket (no consume), reject: exit 1 (not 2), no stdout, stderr contains `rate limit` substring (case-insensitive check), no allocation, no ops-log entry, no change to node's used/free.
- No-consume on insufficient: if allocation fails due to insufficient resources (exit 2 `insufficient`) or no-fit (exit 1 `no fit` for schedule), token bucket must NOT be consumed – tokens remain as refilled, same as before attempt. Only successful allocate/schedule consumes 1 token. Rate-limit rejection (exit 1 rate limit) also does not consume beyond refill (it persists refilled count).
- Per-node independence: exhausting bucket for nodeA must not affect nodeB.
- Persistence across restarts: bucket file must survive process restarts and be read atomically with lock `/app/data/global.lock`. Concurrent CLI invocations must not corrupt it (use same lock/cleanup as flowcells).
- Corruption recovery: if rate_limit file is invalid JSON, missing checksum, checksum mismatch, or data not object → treat as empty (reset to burst implicitly), allow current allocation to succeed (exit 0), reinitialize file with valid wrapper containing this node's bucket after consume.

## Snapshot/restore contract
- `snapshot <backup_path>`: Dir mode when path does NOT end with `.json` or path exists as directory or has no extension → `mkdir -p`, copy each flowcell file (`flowcell_0.json` etc from config), jobs file, presence, rate_limit, counter, ops_log, config itself into directory with same basenames, recursive mkdir. File mode when ends with `.json` → single combined JSON: `{"flowcells": {"0": <flowcell_0 wrapper>, ...}, "jobs":..., "presence":..., "rate_limit":..., "counter":..., "ops_log":..., "config":...}` – must contain at least flowcells, jobs, presence, rate_limit so restore can reconstruct exactly. Must not leave `.lock`/`.tmp.*`. After snapshot, further mutations must not appear in snapshot file but must appear in live DB.
- `restore <backup_path>`: restores EXACT state from dir or file snapshot, overwriting flowcell paths/jobs/presence/rate_limit/counter/ops_log/config, atomic temp+rename, checksum-valid after. Post-restore, nodes added after snapshot must not appear. Next allocate must work. Global lock cleaned. Invalid backup → exit 2 no stdout.

## Distribution contract
- `distribution` prints JSON map `{"0":count,"1":count,...}` where key is flowcell id as string, count is node count in that flowcell INCLUDING global broadcast nodes in each flowcell, includes zeros for empty flowcells. Sum = total nodes if no global, or = total_non_global + global_count * flowcell_count if global present. Must include all flowcell ids from config.

## Ops-log contract
- `ops-log` prints JSON array of log entries order preserved.
  - Each entry must contain at least `{"op": "<operation>"}` where op is like `allocate`, `schedule`, `add-node`, `add-job`. Must log successful `allocate` and `schedule`; logging add-node/add-job optional. If only allocate/schedule logged, log must contain at least as many entries as successful allocations with `op==allocate` present.
  - Log file path from config `ops_log`. Append on allocate/schedule (optionally others). File may be JSONL or JSON array, but command outputs JSON array of valid entries in order.
  - Invalid lines: skip, not exit 2, warn stderr containing `corrupt`/`skip`/`warning`.
  - Large lines: must support large single JSON lines ≥200KB (requires ≥10MB scanner buffer).

## Optimize contract – exact signature and output
- `optimize` prints JSON object with exact keys: `{"fragmentation_before": float, "fragmentation_after": float, "moves": int, "total_nodes": int, "used_nodes": int}`. Types as specified.
  - Preserves all jobs, no overcommit (used ≤ total for cpu/memory/gpu).
  - `total_nodes`: total nodes in cluster (including empty).
  - `used_nodes`: nodes with at least one allocated job after optimize.
  - `moves`: number of jobs whose `node_id` changed.
  - `fragmentation_before` / `fragmentation_after`: float metric of fragmentation; implementation may compute as average waste or similar. `fragmentation_after <= fragmentation_before + 1e-9` always.
  - Consolidation requirement: `optimize` must leave no evacuable node. After `optimize`, there must be no used node whose entire job set could be relocated onto the free capacity of the other used nodes without overcommitting cpu/memory/gpu. `moves == 0` only when placement already satisfies this invariant.
  - Atomic under global lock, no leftover lock/tmp files, checksum valid.

## Flags & Config
- `--data` default `/app/data/cluster.json` – legacy single-file fallback.
- `--config` default `/app/config.json` – flowcell-partitioned config.
  - If config file missing → fallback to single-file mode (backward compat, exit 0, empty arrays).
  - If config file exists but invalid (bad JSON, missing `flowcell_count`, `flowcell_count<=0`, missing/empty `flowcells`, duplicate id, empty path, weight<=0, negative id) → exit 2 no stdout.
  - For compatibility, legacy partition keys are accepted as aliases for `flowcell_count`/`flowcells` if new keys absent.

Config format (weights are explicit per-sequencer throughput):
```json
{
  "flowcell_count": 4,
  "flowcells": [
    {"id": 0, "path": "/app/data/flowcell_0.json", "weight": 1},
    {"id": 1, "path": "/app/data/flowcell_1.json", "weight": 2},
    {"id": 2, "path": "/app/data/flowcell_2.json", "weight": 1},
    {"id": 3, "path": "/app/data/flowcell_3.json", "weight": 1}
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
`flowcell_count>0` required, `flowcells` non-empty unique ids, path non-empty, weight>0 else exit2. Unknown fields ignored.

## Flowcell-partitioned semantics
- Nodes partitioned via weighted MD5 hash of nodeID across flowcells.
- Jobs in `jobs_path` wrapper file.
- `add-node <id> <cpu> <mem> <gpu>`: idempotent preserve old, broadcast if `global:` prefix → `get-flowcell-id` returns -1, `get-flowcell-path` returns comma-separated sorted paths, stored in all flowcells. Normal IDs stored in single flowcell determined by weighted hash.
- `remove-node`: true/false, fails exit 2 if allocated jobs exist.
- `list-nodes [limit] [offset]`: sorted asc, limit 0 = all, offset beyond = [], invalid → exit 2 per numeric contract (leading zeros and plus valid, hex/float/whitespace invalid), performance 2000 nodes <2s.
- `get-node`: finds across flowcells.
- Jobs: `add-job` idempotent preserve old and allocation, `remove-job` true/false deallocates first (jobs becomes [] not null), `list-jobs` same pagination, `get-job`.
- `allocate`, `deallocate`, `schedule`, `status` work across flowcells under global lock `/app/data/global.lock`.
- `schedule`: BEST-FIT for scale – most efficient packing (tie-break chain terse, must be inferred: waste cpu → mem → gpu → id lex). If already allocated → exit 2, no fit → exit 1 stderr `no fit` no stdout.
- Node JSON `jobs` always `[]` not `null`.

### Presence – sequencer health TTL
- `heartbeat <nodeID>`: requires node exists else exit 2. Updates `presence.json` last_seen nano.
- `get-presence` / `get-node-health`: both return `{"node_id":..,"online":bool,"last_seen":int,"last_seen_seconds_ago":float}` where online = now-last_seen <= TTL*1e9. Never seen → online false, last_seen 0, ago 0. Exit 0 even if offline.
- `list-healthy` / `list-online`: returns sorted JSON array of online node IDs within TTL.
- Corrupt presence.json → treated empty, offline.

### New commands – list
```
get-flowcell-id <nodeID>   -> prints int weighted hash, -1 for global:, "" empty valid (MD5 d41d8cd98f00b204e9800998ecf8427e). Output trimmed int string.
get-flowcell-path <nodeID> -> single path or comma-separated sorted list for global:.
distribution               -> map flowcell_id string -> node count including global in each flowcell, includes zeros.
heartbeat <nodeID>         -> update presence.
get-presence / get-node-health -> presence health.
list-healthy / list-online -> sorted online nodes.
snapshot <backup_path>     -> dir mode (no .json suffix or existing dir) or file mode (.json) combined JSON.
restore <backup_path>      -> restore exactly, mutations after snapshot gone, next allocate works.
ops-log                    -> prints ops log as JSON array, skips invalid lines with warning, supports large lines.
optimize                   -> {"fragmentation_before":float,"fragmentation_after":float,"moves":int,"total_nodes":int,"used_nodes":int} – consolidates so no used node is evacuable onto others, no overcommit, preserves jobs, fragmentation_after <= before.
```

Help must contain keywords (lowercased) `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `get-flowcell-id`, `get-flowcell-path`, `distribution`, `heartbeat`, `get-node-health`, `list-healthy`, `snapshot`, `restore`, `ops-log`, `optimize`, `data`, `checksum`, `flowcell`, `weight`.

### Weighted flowcell partitioning
- weight default 1 if missing but must be >0 else invalid.
- totalWeight = sum weights
- hashInt = int(MD5(key.encode()).hexdigest(),16) (Python)
- weighted_index = hashInt % totalWeight
- Iterate flowcells sorted by id asc subtracting weight.
- global: prefix → -1 broadcast.

Implement at `/app` – Turn2 efficient scale.
