# Computer Resource Optimization – Multi-Turn Go Task

This is a two-turn Terminal-Bench task implementing a computer cluster management system in Go with resource allocation, integrity, and large-scale efficiency.

## Turn 1: Core Cluster Management (347 tests, extra-hard)

Build core cluster manager at `/app/` (module `cluster-manager`) with single-file persistence `/app/data/cluster.json`.

**Persistence format (MUST):** Wrapper `{"data":{"nodes":{...},"jobs":{...}}, "checksum": md5 canonical}` where canonical = `json.dumps(data, sort_keys=True, separators=(',',':'), ensure_ascii=False)`. Raw UTF-8 must be preserved for emoji. Go must use `SetEscapeHTML(false)`. Special handling required for U+2028/U+2029: Go escapes these even with SetEscapeHTML(false), Python with ensure_ascii=False does not, so implementation must handle this to match checksum.

**Atomicity and locking (MUST):** On write, atomic via `CreateTemp` same dir + `Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry 5ms 2000 tries with cleanup after each command. No `<data>.tmp.*` or `<data>.lock` leftover. Pre-existing stale tmp and lock files must be ignored and cleaned. Truncated file must take corruption path with `.corrupt.<nanosec>` backup, not crash.

**Execution-based integrity checks (not source-scan):** No grep for CreateTemp/Rename/SetEscapeHTML. Verified via behavior: no tmp leftover after writes, lock cleaned after success and after failure (insufficient, invalid), raw file contains unescaped "<" for special chars `<>&` and raw UTF-8 emoji, checksum matches canonical with ensure_ascii=False and U+2028/U+2029 handling.

**Core functionality:** add-node idempotent no-op preserves old resources (not upsert), remove-node true/false fails exit2 if has allocated jobs, list-nodes pagination sorted asc limit0 all offset beyond [] invalid→exit2, get-node, add-job idempotent preserves allocation, remove-job deallocates first then node jobs [] not null, list-jobs same pagination, get-job, allocate insufficient→exit2 stderr insufficient already allocated different node→exit2 same node idempotent, deallocate true/false, schedule first-fit sorted IDs asc first that fits even if wasteful (nodeA 10 CPU id smaller vs nodeB 4 CPU both fit 2 CPU → nodeA wins for Step1), no fit→exit1 no fit no stdout, status total/used resources sum.

**Hard discriminators (real failures observed, not enumerated rules):**
- Canonicalization: ID containing `<>&` and emoji same key – raw `<` and raw UTF-8 simultaneously, keys with U+2028/U+2029 escaped by Go but not Python, mixed scripts byte vs codepoint ordering (UTF-8 preserves codepoint order but Go escapes line/para separators).
- Stale artifacts: pre-create `<data>.tmp.<pid>` must be ignored and cleaned, pre-create `<data>.lock` must be retried 5ms×2000 never leaving corrupt DB, truncated file must backup .corrupt.<nanosec>.
- Exact-state concurrency: 20 concurrent CLI processes interleaved add-node+allocate overlapping IDs, then assert exact used/free arithmetic plus valid checksum and no overcommit.
- Empty file "" and whitespace "   \n\t" → empty store not corrupt, files "null"/"[]" → corrupt backup warning, missing/bad checksum → backup, jobs field [] not null (nil-slice pitfall), idempotent preserves, concurrent add-node 20 sorted, same node 20 preserve all 20, diff nodes 20, large 800 nodes <1.5s O(n log n), etc.

See `steps/1_step_one/instruction.md` for full spec.

## Turn 2: Large-Scale Efficient (72 tests, extra-hard)

Extends Turn1 via `inherit_prior_session`. Adds weighted sharding, best-fit, presence TTL, token-bucket, snapshot/restore, ops-log, optimize. Flags: --data default /app/data/cluster.json, --config default /app/config.json. Config rule: missing file→fallback single-file, exists valid→sharded, exists invalid (missing shard_count, empty shards, bad JSON, dupe id, empty path, weight≤0, negative)→exit2 no stdout. Config format: shard_count>0 required, shards [{id,path,weight}] unique non-negative, path non-empty, weight>0 else exit2, unknown fields ignored.

**Sharding:** Weighted hash MD5 big-endian `int(md5(key.encode()).hexdigest(),16)%totalWeight` iterate sorted by id subtracting weight, global:→-1 broadcast to all shards, get-shard-id/path empty-string "" valid hashed via MD5, get-shard-path global returns comma-separated sorted list, distribution map shard_id→count includes zeros.

**Best-fit (now):** Minimal waste scoring (free_cpu-req_cpu)→(free_mem-req_mem)→(free_gpu-req_gpu)→smallest ID lex deterministic. This flips from Turn1 first-fit and is core discriminator.

**Rate limiting per-node token bucket:** rate_limit {allocations_per_second,burst} default 5/10 tokens float refill elapsed*rate consume1 else exit1 rate limit no side effects per-node independent persistence wrapper checksum atomic no-consume on insufficient corruption reset multi-cycle refill after 1.6s/1.2s and burst exact.

**Presence TTL:** heartbeat updates last_seen nano presence.json wrapper requires node exists else exit2, get-node-health/get-presence online bool vs TTL, list-healthy/list-online sorted healthy within TTL, expiry 2s→3s offline, multiple nodes, unknown offline 0, corruption and refresh extends online.

**Other:** snapshot/restore dir and file modes restore exactly, presence and rate_limit restored, ops-log prints array skipping invalid warning order preserved, must use bufio.Scanner big buffer 10*1024*1024 to handle 100KB+ lines, with second discriminator ops-log single 200KB line and rate-limit persistence surviving corrupt-then-recreate cycle (de-monocultured so neither test is load-bearing). Optimize consolidates fragmentation_after <= before OR used_nodes<=before moves>=0 total_nodes unchanged preserve jobs no overcommit.

See `steps/2_step_two/instruction.md` for full spec.

## Build

`go build -o ./cluster-manager .` in `/app/`, module `cluster-manager`, stdlib only.

## Pagination Contract (MUST)

`list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`.

## Empty Array Invariant (MUST)

Node jobs field empty MUST serialize as [] not null.

## First-Fit vs Best-Fit (MUST)

- Step1: first-fit sorted IDs asc first that fits wins even if wasteful
- Step2: best-fit cpu→mem→gpu→id lex

## Latest Validation

- Step1: 347/347 PASS extra-hard with canonicalization divergences (<>&+emoji same key, U+2028/U+2029, mixed scripts), stale tmp/lock artifacts, truncated file, exact-state concurrency 20 interleaved add-node+allocate overlapping IDs, plus 300+ other discriminators
- Step2: 72/72 PASS extra-hard with ops-log 200KB line and rate-limit corrupt-then-recreate de-monocultured, plus best-fit tie-break, token-bucket cycles, presence TTL, snapshot/restore exact, etc.
