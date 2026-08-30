# codimango/database-sharding (Go) - Weighted Sharding Proxy with Broadcast and Robust Migration

## Description

**Single-turn** Terminal-Bench task (`format = "terminal_bench_single_turn"`), Go, 84 tests, hard but solvable. Task root holds `instruction.md`, `task.toml`, `environment/`, `solution/`, `tests/`.

**Starting state.** The image pre-builds a working sharding proxy into `/app/main.go` (module `sharding`) at build time from `environment/main_go_b64.txt`, and verifies it compiles. The agent starts from a working proxy, not a skeleton. Also provisioned:

- `/app/config.json` – 4 shards with weights `1, 2, 1, 1` (totalWeight 5).
- `/app/data/shard_{0..3}.json` – empty, in the old `{"data":…,"checksum":…}` format (no `shard_id`/`version`).
- `/app/data/legacy.json` – old flat dump: 120 `user:*`, 30 `order:*`, 5 `global:config:*` keys, values randomized at build time with no seed.
- `/app/data/ops.log` – empty.

The starting proxy already handles weighted MD5 routing, `global:` broadcast, checksum integrity without HTML escaping, corruption backup, self-healing, the transaction log, raw-string values, and large values.

**What the agent must add.** Versioned shard integrity and legacy migration:

- **Versioned format** `{"shard_id", "version", "data", "checksum"}` — `shard_id` must equal the configured id, `version` increments by 1 per successful set/delete/migration/replay, checksum is MD5 of the canonical `data` JSON (sorted keys, no spaces, no HTML escaping) and covers `data` only.
- **Startup validate-and-repair** of every configured shard before any command, including under `--dry-run`. Corruption = invalid JSON, missing/empty checksum, checksum mismatch, `shard_id` mismatch, missing/invalid version. Repair = back up to `<path>.corrupt.<nanosec>` with a warning, then recreate an empty versioned file via same-directory temp-then-rename.
- **Backward compatibility** across three on-disk shapes: old flat file (whole file is data), old `{"data","checksum"}` wrapper with a valid checksum and no `shard_id`/`version`, and the versioned format.
- **`migrate` subcommand** — `proxy --config X --legacy Y --ops-log Z migrate [--dry-run] [--backup path] [--force]`, flags accepted before or after `migrate`. Groups legacy keys per weighted destination (global to all shards), one batched atomic write per shard with `version` old+1, staged first into `/app/data/staging` with staged bytes byte-equal to the final shard. Cleans misplaced and duplicated non-global keys, replays `ops.log` in ascending `ts` order stable for ties (missing `ts` → 0), preserves existing keys unless `--force`, and honors `--backup` for legacy, each modified shard `.bak`, and `ops.log.bak`.
- **CLI contract** — exit 0 success, 1 missing/invalid legacy or I/O, 2 invalid config or bad args with no stdout. `--help` documents the commands; `migrate --help` documents its flags.

Difficulty comes from breadth of interacting invariants rather than one algorithm: checksum canonicalization must be byte-identical across processes, routing must agree between the `set` path and migration, atomic writes must produce exactly one rename, and replay ordering must be stable while reconciling duplicates and misplaced keys.

## Completion Rates

Latest online validation – commit `d34a713`, Nest jobs 6583617–20 (+ 6585145 agentic review), completed 2026-08-30. **Validation status: passing** – all gates green, 84 tests.

| Stage | Agent | Result |
| --- | --- | --- |
| oracle | oracle | 3/3 (100%) |
| metacode | avocado `avocado-code-flex` | 1/5 (20%) |
| agent | claude-code `claude-opus-5` | 2/5 (40%) |
| codex | `gpt-5.5` | 3/5 (60%) |

Gate detail:
- Structural checks: 11/11 pass/warn.
- Oracle validation: 3/3 passed.
- Metacode or Opus pass/fail balance: **passed** – "avocado not trivial and ≥1 agent solved". Ordering is monotonic across the three models (avocado 1/5 < opus 2/5 < gpt 3/5).
- Contamination check: passed. Report body is `NOT_FOUND` / "no eval matches above the cosine threshold"; the MEDIUM risk label is a staleness artifact (verdict judged against earlier instruction text), not a match.
- Provenance check: passed – no third-party model authorship detected.
- Agentic Full-Task Review: **GOOD**, difficulty **GENUINELY_HARD**, secondary issues **NONE**. All rubrics R01–R13 PASS.

## Model Analysis

- Test suite: 84 tests – 43 in `tests/test_sharding_part1.py` (39 KB) and 41 in `tests/test_sharding_part2.py` (49 KB), both under the 64 KB reviewer inline cap. Shared helpers live in `tests/conftest.py`, which must export `__all__` – `from conftest import *` otherwise skips every underscore-prefixed helper.
- Signal quality: binary all-or-nothing scoring over 84 tests means a trial's outcome usually hinges on a single edge case. Pooled trials recommended; run-to-run noise is roughly ±20pp.
- Failures classification (all 15 trials completed; no infra errors). 8 of the 9 failing trials failed exactly 1 of 84 tests; one failed 2. Distribution of failed tests:

  | Test | Failures | Models |
  | --- | --- | --- |
  | `test_migrate_unknown_arg_exit_2` | 3 | opus ×2, codex ×1 |
  | `test_missing_checksum_corruption_step2` | 3 | opus ×1, avocado ×1, codex ×1 |
  | `test_unknown_command_exit_2_step2` | 2 | avocado ×2 |
  | `test_help_documents_commands_and_flags_spec36` | 1 | avocado |
  | `test_atomic_batched_write_behavior_migrate` | 1 | avocado |

- Discrimination spreads across 5 distinct tests and includes substantive behavior (`test_atomic_batched_write_behavior_migrate` covers inotify/atomic-rename; `test_missing_checksum_corruption_step2` covers the corruption contract in spec :22), not only argument parsing. Every model passes the full migration surface – versioned integrity, weighted routing, cleanup, ops-log replay, staging byte-equality, backups – on every run.
- Calibration headroom: avocado at 1/5 is the low edge. Under all-or-nothing scoring any added test can only lower pass rates, so the AFTR's optional coverage suggestions (legacy-file-removed assertion, dot-import alias check, staged-byte-equality with non-empty replay) are deliberately **not** applied – taking them risks a 0/5 avocado and an opposite-direction gate failure.
- `Reward Provenance: WARN` in structural checks is a false positive. `tests/test.sh` derives reward from `/logs/verifier/ctrf.json` under `python3 -I -S`; the checker's heuristic trips on the unrelated `parser_status=$?` used only to propagate the parser's exit status.

## Anti-Cheating Analysis

- **Not fixture-dependent.** 52 of the 84 tests build their own `tempfile.mkdtemp()` config, legacy dump, and shard files rather than relying on the seeded `/app/data` fixtures. Legacy values in the image are randomized at build time with no seed, so nothing about them can be hardcoded.
- **Independent verification, not self-report.** Tests recompute the canonical MD5 in Python (`hashlib.md5` over sorted-key, separator-tight JSON) and compare against the `checksum` field, plus a raw-text assertion that a value containing `<` is not HTML-escaped on disk. Shard `shard_id`/`version` are read back from the file, not from CLI output.
- **Routing can't be guessed.** Tests brute-force a key that lands on a chosen shard (10 call sites) instead of assuming a fixed key→shard mapping, so an implementation with the wrong weighted formula fails even if it is internally consistent.
- **Behavioral, not source inspection.** Atomic batched writes are checked with a real `inotify` watch (`conftest.py:239`) asserting exactly one `MOVED_TO` per shard with no in-place `MODIFY`/`DELETE` — this fails an implementation that rewrites a shard twice. Staging is verified by locating the staged file via its `shard_id` and asserting raw-byte equality with the final shard, so no filename convention is coupled.
- **Exit-code contract is checked with stdout.** 11 assertions pair a nonzero exit with `stdout.strip() == ""`, covering unknown command, unknown migrate arg, missing key arg, and the invalid-config cases — this is what caught the golden's help-to-stdout leak.
- **Stdlib-only is enforced at build.** `test_stdlib_go_list_imports` runs `go list -f '{{join .Imports " "}}'` and fails on any external import (`test_sharding_part2.py:262`).
- **Reward is not exit-code derived.** `tests/test.sh` writes `reward.txt` from `/logs/verifier/ctrf.json` (requires `tests > 0`, `passed > 0`, `failed == 0`, `other == 0`) and runs the parser under `python3 -I -S`, so a `sitecustomize.py` `atexit` shim cannot force a pass.
- **No test-mirroring examples in the spec.** `instruction.md` states the raw-string rule as an invariant (value must be fully-consumed valid JSON after trimming, otherwise the whole input is a raw string) rather than an input→output table, gives no worked weighted-routing mapping, and states replay ordering abstractly with no walkthrough matching `test_ops_log_replay_sorted_by_ts`.
- **README is not agent-visible.** `environment/Dockerfile` copies only `main_go_b64.txt`; this file stays at task root and never enters the container.

