# codimango/large-file-upload-process — HARD MODE

## Description

**HARD MODE** Go CLI for YouTube-like 100s-GB video upload with parallel workers, encryption, and multiple checksum algos. Simulates YouTube's resumable protocol at scale.

**Core challenges**:
- **Massive files**: streaming I/O with `int64`, `Seek`, sparse files via `truncate -s 5G` (size reports big, disk tiny)
- **10 formats**: mp4/mov/mkv/webm/avi/flv/mpeg/mpg/3gp/wmv via magic bytes — magic first, supports uppercase `.MP4`, no-extension, multiple dots `my.video.backup.mp4`
- **Chunked resumable**: default 8M, human-readable `512K`/`8M`/`1G`/`8 MB` with spaces, ceil(size/chunk), `chunk_%06d`, atomic temp+rename
- **Streaming integrity**: SHA256 & MD5 & BOTH via `CopyBuffer` 1MB + `TeeReader`/`MultiWriter`, never ReadFile whole file. Manifest stores both checksums per chunk
- **Parallel upload**: `--parallel 1-32` (default 4) using goroutines, WaitGroup, Mutex for manifest, channel work queue, per-worker file handles (Seek not thread-safe), out-of-order correctness
- **Retry + backoff**: `--retries 0-10` (default 3) exponential `100ms * 2^attempt`, prints `RETRY: chunk X attempt Y` to stderr
- **Encryption**: `--encrypt-key` XOR streaming: `byte[i] XOR key[i%len(key)]` per chunk, encrypted on disk, decrypted on assembly, checksum of original unencrypted
- **Edge cases**: empty INVALID, exact/smaller/+1-byte boundaries, 64KB many-chunks (20MB/64K=320 chunks) without FD leak, corrupted chunk, corrupted manifest WARN, source size mismatch + encrypt-key mismatch, symlink following, no-ext/uppercase/multi-dot
- **CLI**: validate (VALID/INVALID with magic mismatch), info (JSON size/format/valid/checksum/md5/chunk_info), upload (all new flags, `UPLOAD COMPLETE: <path> Size: <bytes> Checksum: <sha> Chunks: <total> Parallel: <n> ChecksumAlgo: <algo>`), assemble (`ASSEMBLE COMPLETE: <path>`)

Starter skeleton returns `not implemented` for core logic. Agent must implement ~800 lines.

**Why naive fails**: ReadFile OOMs on 5GB sparse (memory limit 4096MB), int32 overflows, ext-only validation fails magic mismatch, no mutex corrupts manifest under parallel, no Seek race → wrong data, encryption forgotten → checksum mismatch, MD5/both not stored, parallel flag not validated.

## Completion Rates (online validation — commit 2fcf1c64, 29 tests, 2026-07-23)

- **Oracle**: **3/3** — validated
- **Opus 4.8 (agent)**: **5/5** — failed (solved every trial → too easy for this model)
- **GPT-5.5 (codex)**: **1/5** — validated (4 losses were infra errors, not test failures)
- **Avocado (metacode)**: **3/7** — validated
- avgReward **0.55**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. The earlier brittle tests that caused artificial 0/5 (the `worker N` stdout grep in `test_parallel_flag_validation`, and the `512`-byte chunk-size hang in `test_chunk_size_parsing`) are **gone** — this commit's failures are all on legitimate content.

- **Avocado (metacode) — 3/7, one genuine, consistent reasoning failure.** 3 completed trials passed; 2 real completed failures each failed *only* the memory cluster `test_large_sparse_file_handling` + `test_hundreds_gb_simulation` (27/29); 2 trials were `status=error` infra. **Root cause (both failures): OOM.** Uploading a 5GB (and 10GB) file with `--chunk-size 1G` returned `-9` (SIGKILL / OOM-killed) in the 4GB-limited container — the implementation allocates a full **1GB chunk-sized buffer** instead of streaming each chunk with a small fixed buffer (e.g. 1MB `CopyBuffer`). This is the core "stream massive files without loading into memory" requirement, so it is a **legitimate discriminator** (and it reproduced identically on the prior commit).

- **GPT-5.5 (codex) — 1/5, no reasoning signal.** 1 clean trial passed; the other 4 were all `status=error` (Daytona `ThrottlerException` / harness). Codex is systematically infra-blocked on this task, so 1/5 understates capability — it needs a clean re-run.

- **Opus 4.8 (agent) — 5/5.** All clean passes; too easy for this model.

- **Oracle — 3/3.** Reference solution streams with a small fixed buffer and passes.

**Assessment:** the task now discriminates on a real reasoning gap — memory-efficient streaming must use a small fixed buffer independent of `--chunk-size`; using the chunk size as the buffer OOMs on 1G chunks. Avocado fails it (2/7); Opus and Oracle handle it. Two caveats: Opus is 5/5 (too easy for the strongest model), and codex's 1/5 is infra noise, not difficulty. To tighten calibration, hardening should target the strongest model (Opus) while codex needs infra relief for a valid reading.

## Anti-Cheating Analysis

- **Hardcoded outputs**: `tempfile.TemporaryDirectory()` + dynamic SHA256 via `hashlib` + random session_id via `crypto/rand`. MD5 also dynamic.

- **Overfitting**: 25 tests covering 10 formats, magic mismatch, info-invalid, symlink, no-ext/uppercase/multi-dot, exact/smaller/+1-byte, 5GB/10GB sparse int64, resume, corruption, manifest corrupted, source changed, encrypt-key mismatch, assemble, help, parallel validation, retries validation, checksum algo, encryption XOR, many small chunks 320/640, parallel out-of-order, combined hard (parallel 8 + both + encrypt + 512K). No static oracle.

- **Modifying tests**: `/tests` hidden in TBR. Tests use `go run .` from `/app`.

- **Bypassing**: Must have chunks dir, manifest fields parallel/checksum_algo/encrypt_key, `UPLOAD COMPLETE` with `Parallel:`/`ChecksumAlgo:`, `ASSEMBLE COMPLETE`, many-chunks count, encryption chunks encrypted.

- **Memory**: 5GB sparse in 4GB limit — ReadFile OOMs. Tests assert Seek + int64 + behavioral upload.

- **Concurrency**: parallel 1-32 validation, must contain WaitGroup, Mutex, go keyword.

- **No internet**: stdlib only via go.mod check.

## Additional Notes

- **Go stdlib only**: No external deps
- **Sparse trick**: `truncate -s 5G file.mp4` reports 5GB but uses 4KB disk. Reading holes returns zeros.
- **Build**: `go build -o ./uploader .` must succeed
