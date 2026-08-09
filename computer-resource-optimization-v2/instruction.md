# Computer Resource Optimization – Multi-Turn Go Task

This is a **two-turn** Terminal-Bench task implementing a computer cluster management system in Go with resource allocation, integrity, and large-scale efficiency.

## Overview

- **Turn 1 (1_step_one, 30 tests, moderate):** Build core cluster manager at `/app/` (module `cluster-manager`) with single-file persistence `/app/data/cluster.json` wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping via `SetEscapeHTML(false)`. Commands: `add-node <id> <cpu> <mem> <gpu>` idempotent cpu>0 mem>0 gpu>=0 else exit2, `remove-node` prints true/false fails if has jobs exit2, `list-nodes <limit> <offset>` pagination contract sorted by id asc; limit=0 returns all; offset beyond returns [] same for list-jobs, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate` insufficient → exit2 stderr insufficient, `deallocate` true/false, `schedule` first-fit sorted ids, no fit → exit1 `no fit`, `status`. Atomic via `CreateTemp+Rename` plus lock `<data>.lock` O_CREATE|O_EXCL retry 5ms 2000 tries cleanup, corruption backup `.corrupt.<nanosec>` warning corrupt/checksum, special chars `<>&` no HTML escape raw contains `<`, Unicode emoji preserved, concurrent 20 same node preserves all 20, lock cleanup, stdlib only.

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 45 tests, extra-hard, inherits Turn1 via inherit_prior_session):** Extend to large-scale efficient with weighted sharding, best-fit with full tie-break cascade, pagination, presence TTL with expiry, per-node token-bucket with multi-cycle refill. Flags `--data` default `/app/data/cluster.json`, `--config` default `/app/config.json`. Config rule: if file missing → fallback single-file; if exists and valid → sharded; if exists but invalid (missing shard_count, empty shards, bad JSON, duplicate id, empty path, weight≤0, negative id) → exit2 no stdout. Config format: `shard_count>0` required, `shards [{id,path,weight}]` id unique non-negative, path non-empty, weight>0 else exit2 no stdout, unknown fields ignored. Sharded semantics: nodes sharded via weighted hash MD5 big-endian `int(md5(key.encode()).hexdigest(),16) % totalWeight` iterate sorted by id subtracting weight, `global:` → -1 broadcast to all shards, `get-shard-id`/`get-shard-path` empty-string key "" is valid hashed via MD5 returns exit0, whitespace invalid exit2. `get-shard-path` global returns comma-separated sorted list. `distribution` map shard_id→count includes zeros. `list-nodes <limit> <offset>` and `list-jobs <limit> <offset>` pagination contract sorted asc limit0 all offset beyond [] invalid→exit2 perf 200 <2s. `schedule` now best-fit minimal waste scoring minimal (free_cpu - req_cpu) → minimal (free_mem - req_mem) → minimal (free_gpu - req_gpu) → smallest node ID lexicographic deterministic; this cascade is the core efficiency discriminator. Rate limiting per-node token bucket `rate_limit {allocations_per_second, burst}` default 5/10, tokens float, refill elapsed*rate, consume 1 else exit1 `rate limit` no side effects per-node independent, persistence wrapper checksum atomic, no-consume on insufficient, corruption reset, multi-cycle refill after 1.6s/1.2s, per-node independence. Presence: `heartbeat <nodeID>` requires node exists else exit2 updates last_seen nano in presence.json wrapper, `get-node-health`/`get-presence` returns online bool vs TTL, `list-healthy`/`list-online` sorted healthy within TTL, TTL expiry 2s→3s offline, unknown offline 0. Snapshot/restore dir mkdir -p copy shards+jobs+presence+rate_limit+counter+ops_log+config, file mode combined JSON shards map+jobs+presence+rate_limit+counter+ops_log. `ops-log` prints ops.log array skipping invalid warning corrupt/skip/warning preserved order. `optimize` must consolidate: fragmentation_after <= fragmentation_before OR used_nodes_after <= used_nodes_before, moves>=0, total_nodes unchanged, preserve all jobs, no overcommit.

  Failing observations (buggy skeleton): best-fit picks first-fit not minimal waste on mem/gpu/id tie; token bucket shared global not per-node, no refill after sleep, consumes token on insufficient; optimize reports same fragmentation without moving jobs. Fix these.

  See full spec: `steps/2_step_two/instruction.md`

## Build

`go build -o ./cluster-manager .` in `/app/`, module `cluster-manager`, stdlib only.

## Pagination Contract (MUST)

`list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`.

## Empty-String Handling (MUST – highest leverage)

`get-shard-id ""` and `get-shard-path ""` with empty-string key "" is valid, hashed via MD5, returns exit 0. Whitespace invalid → exit2. `add-node ""` still invalid → exit2.

## Config Rule (MUST – fixed)

- Config file missing → fallback to single-file mode (backward compat)
- Config exists and valid → sharded mode
- Config exists but invalid (bad JSON, missing shard_count, empty shards, duplicate id, empty path, weight≤0, negative id) → exit2 no stdout
- Empty shards array `[]` is invalid → exit2 (discriminator aligns with spec)

## Best-Fit Cascade (MUST – discriminator)

Scoring: minimal (free_cpu - req_cpu), tie mem, tie gpu, tie smallest node ID lexicographically. Tests verify CPU-only, MEM tie-break, GPU tie-break, ID lex tie-break, fragmentation vs first-fit.

## Rate Limiting (MUST – discriminator)

Per-node token bucket float, refill elapsed*rate, burst initial, persist across invocations, per-node independent, no consume on insufficient, no side effects when limited, corruption reset, multi-cycle: 2 succeed fail sleep1.2 succeed fail sleep1.2 succeed, refill 1.6s.

## Optimize Invariants (MUST)

After optimize: total_nodes unchanged, used_nodes <= before OR fragmentation_after <= fragmentation_before, moves>=0 int, all jobs preserved, no node overcommitted.
