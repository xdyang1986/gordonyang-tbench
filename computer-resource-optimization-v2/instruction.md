# Computer Resource Optimization – Multi-Turn Go Task

This is a **two-turn** Terminal-Bench task implementing a computer cluster management system in Go with resource allocation, integrity, and large-scale efficiency.

## Overview

- **Turn 1 (1_step_one, 66 tests, extra-hard, was 30 too easy, then 49 still easy):** Build core cluster manager at `/app/` (module `cluster-manager`) with single-file persistence `/app/data/cluster.json` wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5 canonical}` canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` raw "<" not \u003c via SetEscapeHTML(false). Commands: add-node idempotent no-op preserves old (not upsert) cpu>0 mem>0 gpu>=0 else exit2, remove-node true/false fails if has jobs exit2, list-nodes pagination contract sorted asc limit0 all offset beyond [] invalid->exit2 perf 500 <2s, get-node, add-job idempotent preserves allocation, remove-job true/false deallocates first node jobs [] not null, list-jobs, get-job, allocate insufficient->exit2 stderr insufficient already allocated diff node exit2 same node idempotent no duplicate, deallocate true/false false when not allocated exit2 if job not exist, schedule first-fit sorted IDs asc first that fits even if wasteful not best-fit (Step2 flips), no fit exit1 no fit no stdout, status total/used sum. Hardened 66 with 36 new discriminators over original 30: empty "" whitespace "   \n\t" empty store not corrupt vs null/[]/invalid JSON -> corrupt backup .corrupt.<nanosec> integer suffix warning, missing/bad checksum corruption, jobs [] not null after add-node/deallocate/remove-job, idempotent add-job preserves resources/allocation running, concurrent add-node 20 sorted lock cleaned, concurrent same node 20 preserve all 20 used correct no overcommit valid JSON during, concurrent diff nodes 20, concurrent list while allocating valid JSON, pagination offset then limit order offset1 limit2 -> 1,2 not 0,1 invalid limit/offset negative non-int abc exit2, large 1000 nodes perf <2s O(n log n), special chars <>& job and node raw SetEscapeHTML false, large ID 10KB dash underscore dot colon valid, empty ID with spaces "   " exit2 float resource "4.0" invalid, status sum, used/free correct after allocate/deallocate, node jobs sorted, file lock cleaned after failure insufficient, atomic no tmp leftover, stdlib contains CreateTemp Rename SetEscapeHTML, checksum strict. Failing observations: nil slice null, first-fit vs best-fit, concurrent loss, integer timestamp, etc.

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
