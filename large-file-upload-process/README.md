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

## Completion Rates (online validation — commit 414f259, 2026-07-22)

- **Oracle**: **3/3** — validated
- **Opus 4.8 (agent)**: **4/5** — validated
- **GPT-5.5 (codex)**: **0/5** — failed (all trials were infra errors, not test failures)
- **Avocado (metacode)**: **2/6** — validated
- avgReward **0.53**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. This run's low scores come almost entirely from infrastructure flakiness plus one brittle structural test — not from reasoning gaps.

- **GPT-5.5 (codex) — 0/5, 100% infrastructure.** All 10 trials across two validation jobs were `status=error` (Daytona `ThrottlerException: Too Many Requests` / harness failures scoring 0). Codex never got a single clean trial, so the 0/5 carries no reasoning signal — it is entirely provisioning failure.

- **Opus 4.8 (agent) — 4/5, one brittle-test miss.** The single genuine failure was `test_memory_efficiency_and_streaming` (27/28). Opus uploaded the 5GB sparse file correctly and used `int64`, but the test additionally greps `uploader.go` for the literal substring `Seek` — and Opus's `uploader.go` did not contain it (it used a thread-safe per-worker read approach such as `ReadAt`/`io.SectionReader`). The task description itself says *"per-worker file handles (Seek not thread-safe)"*, so a correct parallel-safe implementation can legitimately avoid shared `Seek` and still fail this grep. This is a test-fragility / spec-contradiction issue, not a reasoning failure.

- **Avocado (metacode) — 2/6, no real failures.** Every completed trial passed; all losses were `status=error` infra flakes.

- **Oracle — 3/3.** Reference solution passes every trial.

**Assessment:** the hard-mode version is not yet cleanly discriminating on reasoning. Codex is fully blocked by infra (0 clean trials), and Opus's only "failure" is a fragile `Seek`-substring grep in `uploader.go` that penalizes a valid thread-safe streaming design the task itself recommends. Recommended before trusting the difficulty signal: (1) re-run to clear codex's infra block, and (2) replace the `assert "Seek" in uploader.go` structural check with a behavioral memory/streaming assertion (or accept `ReadAt`/`SectionReader`/`Seek`), so correct parallel-safe implementations aren't failed on code-organization grounds.

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
