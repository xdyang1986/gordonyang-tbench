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

## Completion Rates (online validation — commit a898d4ea, 30 tests, 2026-07-24)

- **Oracle**: **3/3** — validated
- **Opus 4.8 (agent)**: **5/5** — failed (solved every trial → too easy for this model)
- **GPT-5.5 (codex)**: **5/5** — failed (solved every trial → too easy for this model)
- **Avocado (metacode)**: **3/5** — validated
- avgReward **0.57**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. This was a **clean run** (all failing trials `status=completed`, no infra). Both frontier models solved every trial; the only failures came from Avocado.

- **Opus 4.8 (agent) — 5/5** and **GPT-5.5 (codex) — 5/5.** All clean passes; too easy for both frontier models.

- **Avocado (metacode) — 3/5, two failures of different kinds:**
  - **One genuine reasoning failure** (29/30): failed *only* `test_hundreds_gb_simulation` — uploading a 10GB file with `--chunk-size 1G` exited with **returncode 1** (errored instead of succeeding). The large-file / 1G-chunk memory-streaming path remains the discriminating edge (consistent with prior runs).
  - **One incomplete implementation** (3/30): the agent left the skeleton stubbed — `validate` returned `INVALID: not implemented`, chunk parsing returned `ERROR: invalid chunk size: not implemented`. Only build/help/invalid-format passed. This is a non-delivery (agent didn't finish), not a task-quality issue.

- **Oracle — 3/3.** Reference solution passes.

**Assessment:** the task has drifted back toward **too easy** — both Opus and GPT-5.5 are 5/5. The only difficulty signal comes from Avocado, and half of that is an unfinished implementation rather than a reasoning gap. The single genuine discriminator remaining is the large-file 1G-chunk streaming path (`test_hundreds_gb_simulation`). To restore balance for the frontier models, hardening should deepen the memory-streaming / large-file requirements (or add an adversarial case both Opus and GPT currently pass).

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
