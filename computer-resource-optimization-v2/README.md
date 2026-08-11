# codimango/computer-resource-optimization-v2

Multi-turn Go cluster management with large-scale efficiency.

Turn1 30 tests: core nodes/jobs allocation, checksum integrity, atomic CreateTemp+Rename with file lock O_EXCL retry, corruption backup .corrupt.<nanosec>, special chars <>& no HTML escape, Unicode emoji, concurrent 20 same/different nodes preserves all, pagination contract limit0 all offset beyond [].

Turn2 46 tests extra-hard with real discriminators (fixes previous too-easy after contradiction fix):
- **Config rule clarified**: missing file → fallback single-file, invalid (missing shard_count, empty shards, bad JSON, duplicate id, empty path, weight≤0, negative) → exit2 no stdout. Previous contradiction at instruction.md:17/141 fixed.
- **Best-fit tie-break cascade** cpu→mem→gpu→id lexicographic deterministic vs first-fit. Tests: cpu waste, mem waste tie, gpu waste tie, id lex tie, fragmentation after allocations.
- **Token-bucket multi-cycle**: per-node float tokens, refill elapsed*rate, burst, persistence wrapper checksum, per-node independent, no-consume on insufficient, no side effects when limited, corruption reset, cycles 2 succ fail sleep1.2 succ fail sleep1.2 succ, refill 1.6s.
- **Optimize fragmentation**: total_nodes unchanged, used_nodes <= before OR fragmentation_after <= before, moves>=0 int, preserve all jobs, no overcommit.
- **Presence TTL**: heartbeat online, expiry 2s→3s offline, multi-node TTL, unknown offline 0, corruption handling.
- **Other**: snapshot dir+file restore exact post-mutation gone, ops-log skip invalid warning, pagination perf 200 nodes <2s, weighted distribution includes zeros, global broadcast -1 and comma-separated sorted paths, empty-string "" valid hashed via MD5.
- **Pattern**: Failing observations from pub-sub/dr-buffer history shipped in instruction – buggy skeleton shows wrong best-fit, shared bucket, optimize no-op.

Vestigial top-level tests/solution removed (no COPY/ADD so not leaking but dead).

Quality dimensions improved: domain-depth, realism, originality via best-fit tie-break and token-bucket cycles and optimize invariants.

## Latest Validation

Commit `7f16a6cc` (v0.3), Nest jobs 4489096–99, completed 2026-08-11. **`validationStatus: passing`**, `tbdReviewStatus: pass`.

| Stage | Agent | Result |
| --- | --- | --- |
| oracle | oracle | 3/3 |
| metacode | avocado `avocado-5.14-code` | 5/10 |
| agent | claude-code `claude-opus-4-8` | 7/10 |
| codex | `gpt-5.5` | 10/10 |

Structural 10/10, contamination LOW, novelty risk MEDIUM, embedding dedup 0.7472 with no named matches (threshold 0.75).

### Discriminators

All failures are Turn2; **Turn1 scored 30/30 in every trial and contributes no signal**. Across the 8 failing trials:

| Test | Count | Subsystem |
| --- | --- | --- |
| `test_snapshot_restore_dir` | 6 | snapshot/restore exactness |
| `test_optimize_moves_valid` | 2 | optimize invariants |
| `test_rate_limit_refill_after_sleep` | 1 | token bucket |
| `test_rate_limit_persistence` | 1 | token bucket |

Four distinct discriminators across three subsystems, all spec-backed. `test_snapshot_restore_dir` is stated twice in the Turn2 instruction (L105 and L189, both naming `newnode`); the trap is that dir-mode snapshot copies "each shard file (if exists)", so a shard file created after the snapshot survives a naive restore. Rate-limit wrapper checksum is stated at L80 and L194. Most failures are single-test misses (45/46).

One outlier trial (23522092, avocado) scored 5/46 after passing Turn1 30/30 — its binary never implemented the Turn2 global flag (`stderr='unknown command: --config'`). Legitimate agent failure, not infra.

### Fairness fix verified

`d4339e3` relaxed `test_ops_log_and_skip_invalid` from `len(arr) >= 3` to `>= 1` plus an assertion that the `allocate` op is present. The spec only attaches "append ops log" to `allocate`/`deallocate`/`schedule`/`status` (Turn2 L67) and never says `add-node`/`add-job` write to it, so the old assertion punished spec-following implementations. After the fix the test no longer discriminates — it appears only inside the collapsed trial above.

### Caveats

- `test_rate_limit_refill_after_sleep` uses a 1.6s sleep refill and fired exactly once; a single occurrence may be a timing flake rather than a real miss.
- Dedup margin is three thousandths (0.7472 vs 0.75). Re-check it before any content-heavy edit.
- At time of writing the "Metacode or Opus pass/fail balance" row had not yet appeared in `validationDetails` — aggregation lag after the final job completed. The other five checks are green.
