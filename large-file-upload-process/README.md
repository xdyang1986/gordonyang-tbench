# codimango/large-file-upload-process — HARD MODE

## Description

**HARD MODE** Go CLI for YouTube-like 100s-GB video upload with parallel workers, encryption, multiple checksums, retry with backoff.

**Core**:
- **Massive**: streaming I/O int64 + Seek/ReadAt, sparse via `truncate -s 5G`, 5GB/10GB sparse in 4GB memory limit
- **10 formats** magic-first: mp4/mov/mkv/webm/avi/flv/mpeg/mpg/3gp/wmv, uppercase `.MP4`, no-ext, multi-dot
- **Chunked resumable**: 8M default, human-readable `512K`/`8M`/`1G`/`8 MB` with spaces, `chunk_%06d`, atomic temp+rename, manifest JSON with per-chunk SHA256+MD5, session_id, parallel, encrypt_key, checksum_algo
- **Parallel**: `--parallel 1-32` default 4, worker pool with goroutines+WaitGroup+Mutex+channel, per-worker file handle, out-of-order correctness, prints `worker %d` for behavioral concurrency proof
- **Retry**: `--retries 0-10` default 3, exponential `100ms*2^attempt`, prints `RETRY: chunk X attempt Y backoff <dur>` to stderr, documented hook `INJECT_FAIL_CHUNK` for grading
- **Checksum**: `--checksum sha256|md5|both`, `both` stores both 64-char SHA256 and 32-char MD5, `UPLOAD COMPLETE` prints `Checksum: <sha256> ChecksumMD5: <md5> Chunks: ... Parallel: ... ChecksumAlgo: ...`
- **Encryption**: `--encrypt-key` XOR streaming offset-aware `enc[i]=orig[i]^key[(offset+i)%len]`, encrypted on disk, decrypted on assembly, mismatch error

Skeleton returns `not implemented`, agent implements ~800 lines.

**Why naive fails**: ReadFile OOMs on 5GB sparse, int32 overflow, ext-only fails magic mismatch, no mutex corrupts manifest under parallel, no Seek race → wrong data, encryption forgotten → checksum mismatch, MD5/both not stored.

## Completion Rates (online validation — commit 0cf5c73, 2026-07-23)

- **Oracle**: **3/3** — validated
- **Opus 4.8 (agent)**: **3/5** — validated
- **GPT-5.5 (codex)**: **0/5** — failed
- **Avocado (metacode)**: **0/5** — failed
- avgReward **0.50**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. This was a **clean run** (every failing trial was `status=completed`, no infra errors), and the signal is unusually sharp: **all 12 failing trials failed the same single test — `test_parallel_flag_validation`** — and 11 of them failed *only* that test (29/30).

- **Root cause — an undocumented output-string requirement, not a reasoning gap.** `test_parallel_flag_validation` runs a behavioral check that greps stdout for `worker <N>` tokens and asserts at least 2 distinct ones ("Parallel N should show at least 2 distinct workers"). But **instruction.md never documents that the uploader must print `worker N`** — it only requires goroutines/`WaitGroup`/`Mutex`, a manifest `parallel` field, and the `UPLOAD COMPLETE ... Parallel: <n>` line. The failing models *did* implement real parallelism: their output shows chunks completing **out of order** (`0, 2, 1, 3, 5, 4`) with a correct `Parallel: 4` manifest — but because they print `Uploading chunk X/N` instead of the magic `worker N` string, the regex returns `set()` → fail. This is the same brittle-test class as the previously-removed `Seek`-substring grep.

- **This single hidden requirement produced the 0/5 scores.** GPT-5.5 (0/5) and Avocado (0/5) each failed *only* `test_parallel_flag_validation` in every trial (29/30). Opus was 3/5 — it happens to print worker IDs in some runs and not others, so it passed 3 and failed 2 on the same test. So pass/fail here is essentially "did the model happen to log worker IDs", not whether it parallelized correctly.

- **Secondary:** one GPT-5.5 trial (28/30) also failed `test_checksum_algo_flag`.

**Per-model:**
- **Oracle — 3/3** (reference prints `worker %d`, so it passes its own check).
- **Opus 4.8 (agent) — 3/5**; the 2 losses were `test_parallel_flag_validation` only.
- **GPT-5.5 (codex) — 0/5**; every loss `test_parallel_flag_validation` (one also `test_checksum_algo_flag`).
- **Avocado (metacode) — 0/5**; every loss `test_parallel_flag_validation` only.

**Assessment:** the current 0/5 scores are a **test-quality artifact**, not difficulty. `test_parallel_flag_validation` demands an undocumented `worker N` stdout convention that only the reference solution emits, so correct parallel implementations fail on formatting. Fix before trusting the difficulty signal: either (1) document the `worker N` output contract in instruction.md, or (2) prove parallelism without a magic string — e.g. accept the out-of-order chunk completion the outputs already exhibit, or check the manifest `parallel` field plus a timing/interleaving signal.

## Anti-Cheating Analysis

- **Hardcoded**: `tempfile.TemporaryDirectory()` + dynamic SHA256/MD5 via hashlib + random session_id.

- **Overfitting**: 30 tests covering 10 formats, magic mismatch, info-invalid, symlink, no-ext/uppercase/multi-dot, exact/smaller/+1-byte, 5GB/10GB sparse int64, resume, corruption, manifest corrupted/custom path, source changed, encrypt-key mismatch, assemble, help, parallel flag validation (1/2/4/8 + worker IDs + manifest parallel), retries flag validation (0/3/5 + injection with RETRY: + backoff 100ms), checksum algo (md5/both + both lengths), encryption XOR (encrypted differs, final decrypted matches, assemble decrypt, mismatch), many small chunks 320/640, parallel out-of-order, combined hard (parallel 8 + both + encrypt + 512K), WARN resume (parallel changed, algo changed).

- **Modifying tests**: /tests hidden in TBR.

- **Bypassing**: Must have chunks dir, manifest fields parallel/checksum_algo/encrypt_key, UPLOAD COMPLETE with Parallel:/ChecksumAlgo: (+ ChecksumMD5: when both), ASSEMBLE COMPLETE, many-chunks count.

- **Memory**: 5GB sparse in 4GB limit — ReadFile OOMs (-9). Now purely behavioral via upload, not brittle Seek grep.

- **Concurrency**: Behavioral via worker IDs in stdout, not literal source scan for go/WaitGroup/Mutex/Sleep (previous Medium was grep-only, now behavioral).

- **No internet**: stdlib only.
