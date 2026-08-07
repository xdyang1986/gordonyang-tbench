# Computer Resource Optimization – Multi-Turn Go Task

This is a **two-turn** Terminal-Bench task implementing a computer cluster management system in Go with resource allocation, integrity, and large-scale efficiency.

## Overview

- **Turn 1 (1_step_one, 30 tests, moderate):** Build core cluster manager at `/app/` (module `cluster-manager`) with single-file persistence `/app/data/cluster.json` wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping via `SetEscapeHTML(false)`. Commands: `add-node <id> <cpu> <mem> <gpu>` idempotent cpu>0 mem>0 gpu>=0 else exit2, `remove-node` prints true/false fails if has jobs exit2, `list-nodes <limit> <offset>` pagination contract sorted by id asc; limit=0 returns all; offset beyond returns [] same for list-jobs, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate` insufficient → exit2 stderr insufficient, `deallocate` true/false, `schedule` first-fit sorted ids, no fit → exit1 `no fit`, `status`. Atomic via `CreateTemp+Rename` plus lock `<data>.lock` O_CREATE|O_EXCL retry 5ms 2000 tries cleanup, corruption backup `.corrupt.<nanosec>` warning corrupt/checksum, special chars `<>&` no HTML escape raw contains `<`, Unicode emoji preserved, concurrent 20 same node preserves all 20, lock cleanup, stdlib only.

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 20-25 tests, moderate, inherits Turn1 via inherit_prior_session):** Extend to large-scale efficient with weighted sharding, best-fit, pagination, presence TTL, per-node rate limiting. Flags `--data` default `/app/data/cluster.json` single-file fallback, `--config` default `/app/config.json` sharded active if exists+valid else fallback. Config format: `shard_count>0`, `shards [{id,path,weight}]` id unique non-negative, path non-empty, weight>0 else exit2 no stdout, unknown fields ignored. Sharded semantics: nodes sharded via weighted hash MD5 big-endian `int(md5(key.encode()).hexdigest(),16) % totalWeight` iterate sorted by id subtracting weight, `global:` → -1 broadcast to all shards, `get-shard-id`/`get-shard-path` empty-string key "" is valid hashed via MD5 returns exit0, whitespace invalid exit2 (carved out highest leverage). `get-shard-path` global returns comma-separated sorted list. `distribution` map shard_id→count includes zeros. `list-nodes <limit> <offset>` and `list-jobs <limit> <offset>` pagination contract sorted asc limit0 all offset beyond [] invalid→exit2 perf 100 <2s. `schedule` now best-fit minimal waste cpu→mem→gpu→id lexicographic deterministic for efficiency vs first-fit. Rate limiting per-node token bucket `rate_limit {allocations_per_second, burst}` default 5/10, tokens float, refill elapsed*rate, consume 1 else exit1 `rate limit` no side effects, per-node independent, persistence wrapper checksum atomic. Presence: `heartbeat <nodeID>` requires node exists else exit2 updates last_seen nano in presence.json wrapper, `get-node-health`/`get-presence` returns online bool vs TTL, `list-healthy`/`list-online` sorted healthy within TTL, TTL expiry 2s→3s offline, unknown offline 0. Snapshot/restore dir mkdir -p copy shards+jobs+presence+rate_limit+counter+ops_log+config, file mode combined JSON shards map+jobs+presence+rate_limit+counter+ops_log. `ops-log` prints ops.log array skipping invalid warning corrupt/skip/warning preserves order. `optimize` defrag prints fragmentation_before/after/moves/total_nodes/used_nodes no overcommit preserves jobs. Integrity: wrapper checksum MD5 canonical `json.dumps(data,sort_keys=True,separators=(',',':'))` `SetEscapeHTML(false)` atomic via CreateTemp+Rename, corruption backup `.corrupt.<nanosec>` warning, all sharded files strict, atomic same node 20 concurrent preserves all 20 global.lock cleaned, stdlib only.

  See full spec: `steps/2_step_two/instruction.md`

## Build

`go build -o ./cluster-manager .` in `/app/`, module `cluster-manager`, stdlib only.

## Pagination Contract (MUST)

`list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`.

## Empty-String Handling (MUST – highest leverage)

`get-shard-id ""` and `get-shard-path ""` with empty-string key "" is valid, hashed via MD5, returns exit 0. Whitespace invalid → exit2. `add-node ""` still invalid → exit2.
