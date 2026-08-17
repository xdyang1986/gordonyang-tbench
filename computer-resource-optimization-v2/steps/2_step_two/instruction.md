# Turn 2: Genomics Fleet at Scale – Sharded, Rate-Limited, Health-Aware (Go, 72 tests)

Turn1 built durable single-file core. Now fleet is 10k sequencers, 100k pipeline jobs. Single file and first-fit are too slow/fragmented. We implement sharded storage, best-fit defrag, per-sequencer token buckets, health TTLs, snapshot/restore, ops-log, optimize.

Turn1 binary present via `inherit_prior_session`; must keep Turn1 working in fallback mode.

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
- `list-nodes [limit] [offset]`: sorted asc, limit 0 = all, offset beyond = [], invalid → exit 2, performance 2000 nodes <2s.
- `get-node`: finds across shards.
- Jobs: `add-job` idempotent preserve old and allocation, `remove-job` true/false deallocates first (jobs becomes [] not null), `list-jobs` same pagination, `get-job`.
- `allocate`, `deallocate`, `schedule`, `status` work across shards under global lock `/app/data/global.lock` – no leftover `.lock` or `.tmp.*`.
- `schedule`: NOW BEST-FIT for scale (vs Turn1 first-fit). Choose node with smallest waste: minimal `(free_cpu - req_cpu)`, tie → minimal `(free_mem - req_mem)`, tie → minimal `(free_gpu - req_gpu)`, tie → smallest ID lex. If already allocated → exit 2, no fit → exit 1 stderr "no fit" no stdout. This tie-break cascade is critical vs first-fit naive.
- Node JSON `jobs` always `[]` not `null`.

### Rate limiting – per-sequencer token bucket (HPC throttling)

- Per-node bucket `{tokens:float, last_refill:nano}` in `rate_limit_path` wrapper, atomic.
- Rate = `allocations_per_second`, burst = `burst`, default 5,10.
- Refill: elapsed=(now-last)/1e9, tokens=min(burst, tokens+elapsed*rate), last=now.
- Consume: if tokens>=1 → tokens-=1 allow, persist, allocate/schedule; else persist refilled, exit 1 stderr "rate limit" no stdout, no allocation, no ops-log.
- Per-node independent, no-consume on insufficient resources (exit 2 insufficient → bucket unchanged).
- Persistence, corruption handling (invalid JSON → reset to burst, allow).

### Presence – sequencer health TTL

- `heartbeat <nodeID>`: updates `presence.json` last_seen nano, requires node exists else exit 2.
- `get-presence` / `get-node-health`: both return `{"node_id":..,"online":bool,"last_seen":int,"last_seen_seconds_ago":float}` where online = now-last_seen <= TTL*1e9. Never seen → online false, last_seen 0, ago 0.
- `list-healthy` / `list-online`: sorted online nodes within TTL.
- Corrupt presence.json → treated empty, offline.

### New commands

```
get-shard-id <nodeID>      int weighted hash, -1 for global:, "" empty string valid (MD5 d41d8cd98f00b204e9800998ecf8427e)
get-shard-path <nodeID>    single path or comma-separated sorted list for global:
distribution               map shard_id string -> node count including global in each shard, includes zeros
heartbeat <nodeID>
get-presence <nodeID> / get-node-health
list-healthy / list-online
snapshot <backup_path>     dir mode (no .json suffix or existing dir): mkdir -p copy shards+jobs+presence+rate_limit+counter+ops_log+config; file mode (.json): combined JSON {shards:{id:fileData},jobs,presence,rate_limit,counter,ops_log}
restore <backup_path>      dir/file modes restore exactly, post-snapshot mutations gone, next allocate works
ops-log                    prints ops log as JSON array, skips invalid JSON lines with warning stderr containing corrupt/skip/warning, order preserved. Large test: after 50 add-node/add-job/allocate triplets, expects >=50 entries (allocate-only logging is sufficient per spec) containing allocate.
optimize                   {"fragmentation_before":float,"fragmentation_after":float,"moves":int,"total_nodes":int,"used_nodes":int} – consolidates jobs onto fewer nodes, no overcommit, preserves all jobs, fragmentation_after <= before OR used_nodes_after <= before.
```

Help must contain keywords `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `get-node-health`, `list-healthy`, `list-online`, `snapshot`, `restore`, `ops-log`, `optimize`, plus `data`, `checksum`, `shard`, `weight`, `global`.

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
