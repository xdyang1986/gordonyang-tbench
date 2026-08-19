# Turn 2: Sharded Cluster (Easy – 10 tests)

Extends Turn1 single-file core with sharded mode.

## Flags & Config
- `--data` default single-file fallback
- `--config` default `/app/config.json`
  - Missing → fallback to single-file mode (not exit2)
  - Invalid (bad JSON, missing shard_count<=0, empty shards, duplicate id, empty path, weight<=0) → exit2 no stdout

Config:
```json
{
  "shard_count": 4,
  "shards": [{"id":0,"path":"/app/data/shard_0.json","weight":1},...],
  "rate_limit": {"allocations_per_second": 1000, "burst": 10000},
  "node_heartbeat_ttl_seconds": 60
}
```
Unknown fields ignored.

## Sharded semantics
- Nodes sharded via weighted MD5 hash: totalWeight=sum weights, hashInt=int(MD5(key).hexdigest,16), index=hashInt%totalWeight, iterate shards sorted by id subtracting weight.
- Jobs in jobs_path wrapper.
- `global:` prefix → broadcast to all shards, get-shard-id -1, get-shard-path comma-separated sorted.
- Commands same as Turn1 but under global lock `/app/data/global.lock`, no .lock/.tmp leftover.
- `schedule` now BEST-FIT: minimal (free_cpu-req), tie mem, tie gpu, tie smallest ID lex. No fit → exit1 stderr "no fit".

New commands for 10 tests: get-shard-id, get-shard-path, distribution, heartbeat, get-presence, etc. Help must contain add-node, remove-node, list-nodes, get-node, add-job, remove-job, list-jobs, get-job, allocate, deallocate, schedule, status, get-shard-id, get-shard-path, distribution, heartbeat, get-presence, etc plus data, checksum, shard, weight, global.

Keep Turn1 first-fit working when config missing.

Build via `go build -o cluster-manager .`
