# Computer Resource Optimization – Multi-Turn Go Task

This is a **two-turn** Terminal-Bench task implementing a computer cluster management system in Go with resource allocation, integrity, and large-scale efficiency.

## Overview

- **Turn 1 (1_step_one, 196 tests, extra-hard, was 30 too easy, 49/66/80/96 still easy):** Single-file `/app/data/cluster.json` wrapper checksum MD5 canonical sort_keys separators + SetEscapeHTML false raw "<" not \u003c. Atomic CreateTemp+Rename same dir + file lock O_CREATE|O_EXCL retry 5ms 2000 tries cleanup no tmp no global.lock. Checksum strict missing/bad/invalid JSON including null/[] -> backup .corrupt.<nanosec> integer suffix regex \.corrupt\.\d+$ warning corrupt/checksum recreate empty. Empty "" whitespace "   \n\t" -> empty store not corrupt. Jobs [] not null after add-node/deallocate/remove-job/remove-all raw '"jobs":[]' no null. Idempotent no-op preserves old resources/allocation running not upsert, same ID concurrent 20 race ->1 node/job, add-node 20 sorted, same node 20 preserve all 20 used 20 valid JSON during, diff nodes 20 preserve all 20, deallocate 20 -> used 0 jobs [], list while allocating 10x30 valid JSON no crash, list 100 times 10 threads no crash. Pagination offset then limit order offset1 limit2 ->1,2 not 0,1 invalid negative abc ->exit2 limit0 vs omit both all offset beyond []. Perf 800 nodes list <1.5s O(n log n) limit100 offset100 <1.5s 500 jobs sorted, special chars <>& raw job and node, Unicode emoji 🌍🚀😀, large ID 10KB dash underscore dot colon valid, empty ID spaces "   " ->exit2 float resource "4.0" invalid, timestamp integer required reject 1000.0 1e3 0x3e8, status total/used sum used/free after allocate/deallocate, node jobs sorted asc, remove false not exist, deallocate false vs exit2 nonexist, allocate diff node exit2 same node idempotent no duplicate, insufficient memory/gpu/cpu, lock retry 100ms manual lock file then thread removes after 100ms should retry succeed, file lock cleaned after success/failure insufficient, etc. 166 new discriminators over original 30 (30->120).

- **Turn 2 (2_step_two, 46 tests, extra-hard, good to keep, inherits Turn1):** Weighted sharding MD5 big-endian int(md5)%totalWeight, global: -1 broadcast comma-separated sorted, empty-string "" valid MD5, distribution includes zeros, config missing->fallback invalid->exit2 no stdout empty shards [] invalid, list-nodes/jobs pagination sorted asc limit0 all offset beyond [] invalid->exit2 perf 200 <2s, schedule best-fit cpu->mem->gpu->id lex vs first-fit (nodeA 10 CPU wins Step1 vs nodeB 4 CPU wins Step2), token-bucket per-node float refill elapsed*rate burst persistence wrapper checksum atomic per-node independent no-consume on insufficient no side effects corruption reset multi-cycle 2 succ fail sleep1.2 succ fail sleep1.2 succ refill 1.6s, presence TTL heartbeat last_seen nano wrapper requires node exists else exit2 online bool vs TTL list-healthy sorted TTL expiry 2s->3s offline multi-node unknown offline0, snapshot/restore dir/file exact post-mutation gone trap, ops-log skip invalid warning order preserved, optimize fragmentation_after <= before OR used_nodes<=before moves>=0 total_nodes unchanged preserve jobs no overcommit.

## Build

`go build -o ./cluster-manager .` in `/app/`, module `cluster-manager`, stdlib only.

## Pagination Contract

`list-nodes <limit> <offset>` sorted asc limit0 all offset beyond [] Same for list-jobs.

## Empty Array Invariant

Node jobs empty MUST be [] not null (nil-slice). After add-node, deallocate, remove-job must be [] not null.

## First-Fit vs Best-Fit

- Step1: first-fit sorted IDs asc first that fits wins even if wasteful (nodeA 10 CPU id smaller vs nodeB 4 CPU both fit 2 CPU -> nodeA wins)
- Step2: best-fit cpu->mem->gpu->id lex (nodeB wins)

## Latest Validation

- Step1: 196/196 PASS extra-hard (was 30/49/66/80/96/108 still easy per feedback), 90 new discriminators: integer timestamp float resource, whitespace/null/[] corrupt vs empty store, [] not null, idempotent same ID race 20, concurrent add-node 20 sorted, same node 20 preserve all, diff nodes 20, deallocate 20, list while allocating, pagination offset then limit, invalid limit/offset, large 800 <1.5s O(n log n), special chars job, large ID 10KB dash underscore dot colon, empty ID spaces, status sum, used/free correct, node jobs sorted, lock cleaned after failure, lock retry 100ms, gpu insufficient, concurrent remove while allocating, etc.
- Step2: 46/46 PASS good to keep (best-fit tie-break mem/gpu/id lex, token-bucket multi-cycle refill 1.6s/1.2s per-node independent no-consume persistence corruption, optimize invariants, presence TTL expiry multi-node unknown offline, config validation, snapshot restore exact trap)
