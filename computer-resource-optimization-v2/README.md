# codimango/computer-resource-optimization-v2

Multi-turn Go cluster management with large-scale efficiency.

Turn1 30 tests: core nodes/jobs allocation, checksum integrity, atomic CreateTemp+Rename with file lock O_EXCL retry, corruption backup .corrupt.<nanosec>, special chars <>& no HTML escape, Unicode emoji, concurrent 20 same/different nodes preserves all, pagination contract limit0 all offset beyond [].

Turn2 45 tests extra-hard with real discriminators (fixes previous too-easy after contradiction fix):
- **Config rule clarified**: missing file → fallback single-file, invalid (missing shard_count, empty shards, bad JSON, duplicate id, empty path, weight≤0, negative) → exit2 no stdout. Previous contradiction at instruction.md:17/141 fixed.
- **Best-fit tie-break cascade** cpu→mem→gpu→id lexicographic deterministic vs first-fit. Tests: cpu waste, mem waste tie, gpu waste tie, id lex tie, fragmentation after allocations.
- **Token-bucket multi-cycle**: per-node float tokens, refill elapsed*rate, burst, persistence wrapper checksum, per-node independent, no-consume on insufficient, no side effects when limited, corruption reset, cycles 2 succ fail sleep1.2 succ fail sleep1.2 succ, refill 1.6s.
- **Optimize fragmentation**: total_nodes unchanged, used_nodes <= before OR fragmentation_after <= before, moves>=0 int, preserve all jobs, no overcommit.
- **Presence TTL**: heartbeat online, expiry 2s→3s offline, multi-node TTL, unknown offline 0, corruption handling.
- **Other**: snapshot dir+file restore exact post-mutation gone, ops-log skip invalid warning, pagination perf 200 nodes <2s, weighted distribution includes zeros, global broadcast -1 and comma-separated sorted paths, empty-string "" valid hashed via MD5.
- **Pattern**: Failing observations from pub-sub/dr-buffer history shipped in instruction – buggy skeleton shows wrong best-fit, shared bucket, optimize no-op.

Vestigial top-level tests/solution removed (no COPY/ADD so not leaking but dead).

Quality dimensions improved: domain-depth, realism, originality via best-fit tie-break and token-bucket cycles and optimize invariants.
