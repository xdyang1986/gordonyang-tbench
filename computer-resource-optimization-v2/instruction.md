# Computer Resource Optimization – Multi-Turn Go Task

This is a **two-turn** Terminal-Bench task implementing a computer cluster management system in Go with resource allocation, integrity, and large-scale efficiency.

## Overview

- **Turn 1 (1_step_one, 49 tests, extra-hard, was 30 too easy):** Build core cluster manager at `/app/` (module `cluster-manager`) with single-file persistence `/app/data/cluster.json` wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping via `SetEscapeHTML(false)` raw "<" preserved. Commands: `add-node <id> <cpu> <mem> <gpu>` idempotent no-op preserves old resources (not upsert) cpu>0 mem>0 gpu>=0 else exit2, `remove-node` true/false fails if has jobs exit2, `list-nodes <limit> <offset>` pagination contract sorted asc limit0 all offset beyond [] invalid->exit2 perf 500 <2s, `get-node`, `add-job` idempotent no-op preserves old allocation, `remove-job` true/false deallocates first then removes node jobs [] not null, `list-jobs`, `get-job`, `allocate` insufficient -> exit2 stderr insufficient already allocated different node exit2 same node idempotent, `deallocate` true/false, `schedule` **first-fit** sorted IDs asc first that fits (even if wasteful) not best-fit (Step2 will change), no fit -> exit1 no fit no stdout, `status` total/used resources sum. Hardened: empty file "" and whitespace "   \n\t" -> empty store not corrupt, missing checksum/bad checksum/invalid JSON -> backup .corrupt.<nanosec> warning corrupt/checksum recreate empty, jobs field empty MUST be [] not null (Go nil-slice pitfall) after add-node/deallocate/remove-job, concurrent add-node 20 preserve all sorted, concurrent allocate same node 20 preserve all 20 used correct no overcommit file valid JSON during, concurrent diff nodes 20 preserve all, pagination offset then limit order (offset1 limit2 -> 1,2 not 0,1), large ID 10KB supported, special chars <>& raw no escape job and node, Unicode emoji preserved, atomic CreateTemp+Rename + file lock O_CREATE|O_EXCL retry 5ms 2000 tries cleanup no tmp leftover, stdlib only contains CreateTemp Rename SetEscapeHTML, ops-log big buffer etc. Failing observations: jobs null not [], first-fit vs best-fit confusion, concurrent loss, checksum missing.

  See full spec: `steps/1_step_one/instruction.md`

- **Turn 2 (2_step_two, 46 tests, extra-hard, good to keep, inherits Turn1 via inherit_prior_session):** Extend to large-scale efficient with weighted sharding, best-fit with full tie-break cascade cpu→mem→gpu→id lex, pagination, presence TTL expiry, per-node token-bucket multi-cycle refill. Flags --data default /app/data/cluster.json --config default /app/config.json. Config rule: missing file -> fallback single-file, exists valid -> sharded, exists invalid (missing shard_count, empty shards, bad JSON, dupe id, empty path, weight<=0, negative) -> exit2 no stdout. Config format shard_count>0 required shards [{id,path,weight}] id unique non-negative path non-empty weight>0 else exit2, unknown fields ignored. Sharded semantics weighted hash MD5 big-endian int(md5(key.encode()).hexdigest(),16)%totalWeight iterate sorted by id subtracting weight, global: -> -1 broadcast to all shards get-shard-path comma-separated sorted, get-shard-id/path empty-string "" valid hashed MD5 returns exit0, distribution map shard_id→count includes zeros, list-nodes/jobs pagination contract sorted asc limit0 all offset beyond [] invalid->exit2 perf 200 <2s, schedule now best-fit minimal waste scoring (free_cpu-req_cpu) -> (free_mem-req_mem) -> (free_gpu-req_gpu) -> smallest node ID lex deterministic core discriminator, rate limiting per-node token bucket rate_limit {allocations_per_second,burst} default5/10 tokens float refill elapsed*rate consume1 else exit1 rate limit no side effects per-node independent persistence wrapper checksum atomic no-consume on insufficient corruption reset multi-cycle refill after1.6s/1.2s, presence heartbeat updates last_seen nano presence.json wrapper requires node exists else exit2 get-node-health/get-presence online bool vs TTL list-healthy/list-online sorted healthy within TTL TTL expiry2s->3s offline multi-node unknown offline0. Snapshot/restore dir mkdir -p copy shards+jobs+presence+rate_limit+counter+ops_log+config file mode combined JSON, ops-log prints array skipping invalid warning corrupt/skip/warning preserved order, optimize consolidates fragmentation_after <= fragmentation_before OR used_nodes<=before moves>=0 total_nodes unchanged preserve all jobs no overcommit.

  Failing observations (buggy skeleton): best-fit picks first-fit not minimal waste on mem/gpu/id tie; token bucket shared global not per-node, no refill after sleep, consumes token on insufficient; optimize reports same fragmentation without moves.

  See full spec: `steps/2_step_two/instruction.md`

## Build

`go build -o ./cluster-manager .` in `/app/`, module `cluster-manager`, stdlib only.

## Pagination Contract (MUST)

`list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`.

## Empty Array Invariant (MUST)

Node `jobs` field empty MUST serialize as [] not null (Go nil-slice pitfall). After add-node, deallocate, remove-job must be [] not null.

## Config Rule (MUST)

- Config file missing → fallback to single-file mode
- Config exists and valid → sharded mode
- Config exists but invalid → exit2 no stdout
- Empty shards array [] invalid → exit2

## First-Fit vs Best-Fit (MUST)

- Step1: first-fit sorted IDs asc first that fits wins even if wasteful (nodeA 10 CPU id smaller vs nodeB 4 CPU both fit job 2 CPU -> nodeA wins)
- Step2: best-fit minimal waste cpu→mem→gpu→id lex (same example nodeB wins). This flip is intentional discriminator.

## Rate Limiting & Optimize (Step2)

Per-node token bucket float refill, burst, persistence, per-node independent, no-consume on insufficient, corruption reset, multi-cycle 2 succ fail sleep1.2. Optimize: total_nodes unchanged, used_nodes<=before OR fragmentation_after<=before, preserve jobs, no overcommit.

## Latest Validation

- Step1: 49/49 PASS harder (was 30), new discriminators: whitespace empty file, missing/bad checksum corruption backup, jobs [] not null, add-job idempotent preserves allocation, concurrent add-node 20, concurrent same node 20 preserve all, concurrent diff nodes 20, pagination offset then limit order, invalid limit/offset, large 500 perf <2s, special chars job, large ID 10KB, status sum, first-fit not best-fit, atomic no tmp leftover
- Step2: 46/46 PASS good to keep (best-fit tie-break mem/gpu/id lex, token-bucket multi-cycle refill 1.6s/1.2s per-node independent no-consume persistence corruption, optimize fragmentation invariants, presence TTL expiry multi-node unknown offline, config validation, snapshot restore exact, ops-log skip invalid)
