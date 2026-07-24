# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check, `allow_internet=true`), a **Kafka-like partitioned message queue broker** at `/app`. Durable, crash-consistent with compaction, plus TRIM retention, PRODUCE_BATCH atomic, and PRODUCE_IDEMPOTENT dedup.

Commands (space-separated, timestamps ≥0, negative timestamp = invalid input → exit non-zero):
- **Topic lifecycle:** `CREATE_TOPIC` (idempotent), `DELETE_TOPIC` (removes messages + group state for topic, but groups persist even when empty - intended, leniently accepts GC)
- **Producer:** `PRODUCE <topic> <partition> <payload> <ts>` → offset, `PRODUCE_AUTO <topic> <payload> <ts>` → `<partition> <offset>` hashed `sum(bytes)%partitions`, `PRODUCE_BATCH <topic> <count> <part1> <payload1> ... <timestamp>` count 1..100 atomic (all partitions validated before any append, comma-separated offsets), `PRODUCE_IDEMPOTENT <topic> <partition> <dedup_id> <payload> <ts>` → offset (duplicate id returns existing offset, no new message, dedup cleared on TRIM/DELETE)
- **Retention:** `TRIM <topic> <partition> <offset> <ts>` sets low watermark to max(old low, offset), deleting messages with offset < low; `FETCH`/`FETCH_RANGE` respect low (effective start = max(start,low)), `PARTITION_INFO` low/high, `TOPIC_INFO` total retained sum(high-low), `COMMIT`/`SEEK` must respect low (>=low or -1 for COMMIT)
- **Consumer:** `FETCH`, `FETCH_RANGE`, `POLL` (auto-creates group **only on success**, subscribes, position init max(low, committed+1 else low), auto-advance over trimmed ranges, advances 1, logs only one SEEK for final next position), `COMMIT` (>=low or -1, only on success creates group), `SEEK` (>=low..high, only on success creates group), `JOIN_GROUP` (idempotent, only on success creates group), `GET_GROUP_OFFSET` (NONE if committed<low or -1 or trimmed), `LIST_GROUPS`
- **Maintenance:** `COMPACT` atomic rewrite to minimal deterministic record set: CREATE per topic sorted asc, PRODUCE all 0..high-1 per partition sorted asc offset asc (with dedup preserved as PRODUCE_IDEMPOTENT where applicable), TRIM where low>0 sorted, JOIN per group sorted asc topic sorted, COMMIT where committed !=-1 and >=low and topic exists sorted, SEEK where pos != expected_default (max(low, committed+1) or low) sorted, all timestamp 0, deterministic sorted order, temp file + atomic rename.

Payloads: single tokens no spaces no commas 1..1024, dedup_id same validation as payload, topic/group names `[A-Za-z0-9._-]+` 1..255 not `.`/`..`. Invalid input → exit non-zero; app errors → `ERROR`. Negative timestamp explicitly invalid input per review. Group lifecycle on error clarified: failed JOIN/POLL/COMMIT/SEEK does NOT create group (fixes previous ambiguity where Avocado created group on errored SEEK and failed fuzz on LIST_GROUPS).

## What makes this hard

- **Durable log with CRC framing:** `mq.log` records are `uint32 len + uint32 crc32 + payload`; recovery must stop at first corrupt record, truncate torn tail, and remain appendable.
- **Atomic minimal compaction (fixed over-pinning per review):** Previously test asserted `len(before)==12` for live log, failing valid alternate strategies where POLL logs 2 SEEKs (init 0 + advance 1) that still replay to same state. Now test allows extra non-noop live WAL records that replay to same final state, requiring only post-compact exact minimal sequence per spec and `after_size < before_size`. Spec now explicitly states POLL must log only one SEEK for final next position.
- **TRIM low/high semantics** cascading into FETCH/RANGE/POLL/COMMIT/SEEK/PARTITION_INFO/TOPIC_INFO with auto-advance/clear on trim, plus dedup map GC on trim.
- **SEEK/COMMIT group lifecycle on error (Issue 1 fixed):** Previously SEEK listed error cases then said "Auto-creates group" — ambiguous whether failed SEEK still registers group. Avocado registered group first then checked offset, so `SEEK g1 t0 2 4 2` (offset 4, high 1) created g1 then returned ERROR, causing LIST_GROUPS `g0,g1,g2` vs reference `g0,g2`. This was only failing test (fuzz 75/76) in trials kBqDgXE/Naf3cFM. Now spec explicitly: on application error (invalid topic/partition or offset out of range) group is **NOT** created and no subscription. Updated for JOIN, POLL, COMMIT, SEEK consistently. Makes test measure capability, not spec-reading.
- **PRODUCE_BATCH atomicity** (all partitions validated before any append) — models often append partial then error, failing atomicity.
- **PRODUCE_IDEMPOTENT dedup (hardening for TOO_EASY):** Requires per-partition dedup map `dedup_id → offset`, duplicate returns existing offset without new message, no log, TRIM clears dedup < low, DELETE clears all, persistence across restart and compaction (must emit PRODUCE_IDEMPOTENT for retained messages with dedup, not plain PRODUCE), interaction with TRIM allowing recreate after trim.
- **Fuzz with Python reference:** `test_fuzz_random` 50×200 random commands vs Python reference implementing identical low/high, committed, pos, auto-advance, clearing <low, no group creation on error, dedup, batch atomicity. Hard to hardcode.
- **No-op suppression** and **sorted-order** guarantees in compaction output, plus **stdlib-only** enforcement (test rejects imports containing `.`) and **best-effort fsync** informational test always passes (accepts `O_SYNC`/`O_DSYNC`, fixes previous contradiction where best-effort acted as hard gate).
- **Strict validation:** invalid names, negative timestamps (explicitly invalid), bad arity → non-zero exit.

## Test / Solution Details

- **84 tests** via `go build -o /tmp/agent_mq .`:
  * basic, auto-hash (foo 324%3=0, bar 309%3=0, baz 317%3=2), fetch NONE beyond high/low, fetch_range with low, list sorted, topic/partition info low/high, create idempotent, delete, produce errors
  * consumer groups: join/poll (auto-create only on success), commit/get, -1 clear, seek (only on success creates group), poll after produce, multi-partition, list_groups, offset NONE, delete lenient GC, produce_auto poll, poll isolation, **failed SEEK/COMMIT must not create group**
  * error handling: invalid topic/partition, commit/seek beyond high/low, trim beyond high, batch atomic error
  * invalid input: unknown cmd, arity, bad ints, 0/>1000 partitions, bad topic/group names, payload comma, **negative timestamps** (5 cases), **batch count 0/>100 and arity mismatch**, **group names invalid for 5 commands**
  * blank lines, deterministic, stdlib-only (enforced via import dot check), payload 1024 & topic 255 boundaries
  * durability: persist across restart, group committed/seek, auto-produce, torn-tail, bad CRC, truncate-then-append, compact preserves state/seek/trim, stray tmp, empty log clean, in-memory no persist, **produce_auto logged as normalized PRODUCE (not PRODUCE_AUTO)**
  * **TRIM (10+):** basic, range, commit/seek error below low, poll auto-advance, poll after trim+produce, commit cleared on trim, persist low, many-messages (20 trim 15), offsets continue, delete+recreate resets low, large payload & 201-char topic & 1000 partitions, range low edge, compact preserves trim
  * **BATCH (6):** basic, same partition, atomic error (invalid partition → ERROR none appended), invalid input, persist, batch+trim
  * **IDEMPOTENT (6):** basic duplicate returns same offset, different ids, trim allows recreate after dedup cleared, persist across restart (dedup persists), batch+idempotent interaction, error cases
  * **Stress (2):** 1k produces + trim 500, many dedup ids (10) + 5 duplicates + trim + recreate
  * **Fuzz (1):** 50×200 random commands (including BATCH, IDEMPOTENT, TRIM, etc.) vs Python reference with identical low/high, dedup, batch atomic, no group creation on error
  * **R06/R07 (5):** compact minimal deterministic and smaller (now allows extra live WAL records, only checks post-compact exact sequence + size shrink, spec says POLL logs only one SEEK), produce_auto logged as normalized produce, noop does not append (5 types), compaction preserves sorted order, invalid group names
  * **Best-effort durability:** `test_fsync_best_effort` — informational only, always passes, accepts `Sync()` / `O_SYNC` / `O_DSYNC` / `fsync`, logs warning if missing (spec says skipping fsync still passes functional tests, so does NOT gate reward, fixing contradiction)
  * Total 84.

- **Reference solution:** Go 1.26, `Partition{msgs []string, low int64, dedup map[string]int64, revDedup map[int64]string}`, `doTrim` clearing dedup and group state < low, `doProduceIdempotent` with dedup map and trim-aware duplicate check, `PRODUCE_BATCH` atomic validation then sequential produce with comma-separated offsets, `doProduce`, FETCH/FETCH_RANGE/TOPIC_INFO/PARTITION_INFO/COMMIT/SEEK/POLL respect low and auto-advance, `COMMIT`/`SEEK`/`JOIN`/`POLL` return ERROR before creating group on invalid, replay handles PRODUCE, PRODUCE_IDEMPOTENT, TRIM, JOIN, COMMIT, SEEK, compact emits CREATE → PRODUCE/PRODUCE_IDEMPOTENT all 0..high-1 → TRIM low → JOIN → COMMIT >=low → SEEK != expected_default, `Sync()` in append/compact, atomic rename.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true` (stdlib pre-bundled, no pip/apt needed; third-party rejected via import check — satisfies bundling validator that complained `allow_internet=false` without bundled deps).

## Completion Rates (online validation — commit 0acdc9ea, 84 tests, 2026-07-24)

- Oracle: **3/3** — validated
- Opus 4.8 (agent): **3/5** — validated
- GPT-5.5 (codex): **3/5** — validated
- Avocado (metacode): **2/5** — validated
- avgReward **0.80**, validation passing — balanced: all three models land 2-3/5 on genuine failures, Oracle solves it.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. This was a **clean run** (every failing trial `status=completed`, no infra), and the PRODUCE_IDEMPOTENT hardening is now the primary discriminator.

- **`test_fuzz_random` — the main discriminator (PRODUCE_IDEMPOTENT dedup).** All three frontier fuzz failures (2 Opus + 1 GPT-5.5) diverge from the Python reference at the **identical point — line 41: `go='5 b643'` vs `py='2 imsg641'`**. The reference expects an idempotent message at offset 2; the models return a batch message at offset 5. Root cause: their `PRODUCE_IDEMPOTENT` dedup/offset semantics diverge from the reference (a reused `dedup_id` must return the existing offset and append no new message), which shifts offsets so a later FETCH/POLL returns the wrong record. This is exactly the newly-added hard feature working as intended.

- **Compaction cluster (GPT-5.5, 1 trial, 81/84).** Failed `test_compact_preserves_trim`, `test_compact_minimal_deterministic_and_smaller`, and `test_compaction_preserves_sorted_order` — minimal deterministic compaction with sorted order and trim/dedup preservation.

- **Per-model:**
  - **Opus 4.8 (agent) — 3/5.** 2 genuine failures, both `test_fuzz_random` only (83/84, the dedup mismatch).
  - **GPT-5.5 (codex) — 3/5.** 2 genuine failures: one `test_fuzz_random` (same dedup mismatch), one compaction cluster.
  - **Avocado (metacode) — 2/5.** 2 clean passes; its 3 losses were **`go build failed: no Go files in /app`** — it did not write source to `/app` (non-delivery), not a granular reasoning failure. Avocado is inconsistent at delivering compilable code here.
  - **Oracle — 3/3.**

**Assessment:** valid, well-calibrated task. The PRODUCE_IDEMPOTENT hardening moved it off TOO_EASY — all three frontier models now land 2-3/5 with real failures on the dedup fuzz (and compaction), while Oracle solves it 3/3. The only non-reasoning noise is Avocado failing to emit code in 3 of 5 trials.

## Anti-Cheating Analysis

- No hardcoded outputs: fuzz 50×200 random commands covering TRIM low/high, BATCH atomic, IDEMPOTENT dedup with trim GC, invalid SEEK/COMMIT group-creation edge.
- Tests run binary as subprocess with fresh tmp dirs, check durability via size, CRC, exact minimal post-compact sequence (not over-pinning live log len), sorted order, no-op suppression, invalid names, boundaries (1024B payload, 255-char topic, 1000 partitions), batch atomicity, idempotent dedup persistence, low handling, stdlib import dot check, fsync best-effort informational (always passes, accepts O_SYNC).
- Bypassing fails on per-partition offsets, sum-bytes hash, low handling with auto-advance and clearing <low, batch atomic validation, idempotent dedup map and trim GC, compaction minimal deterministic and strictly smaller, sorted order, CRC framing, group lifecycle on error.
