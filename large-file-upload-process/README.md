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

## Completion Rates

- **Oracle**: 3/3 passed (100%) in ~5m17s (hard mode, includes 5GB sparse + 10GB sparse + 320 chunks + encryption+parallel)
- **Sonnet 4.6 (5 attempts)**: Expected 0/5 to 1/5 — hardest parts:
  - Parallel worker pool with WaitGroup+Mutex+channel + per-worker file handle
  - Retry with exponential backoff + RETRY: log
  - Both checksums: manifest must have file_checksum (64) + file_checksum_md5 (32) + per-chunk both
  - XOR encryption streaming with correct key cycling across buffer boundaries and decrypt-then-verify
  - Many small chunks (64K) 320 files without FD leak
  - Symlink + no-ext + uppercase
- **Opus (5)**: Expected 1/5 to 2/5 — better at magic bytes but struggles with parallel order preservation and encryption+checksum interaction
- **Avocado (5)**: Expected 0/5 to 1/5

Previous easy version was 1/5 Sonnet, 3/5 Opus. Hard mode pushes Sonnet to 0/5.

## Model Analysis

Dominant failures for hard mode:

1. **Parallel not parallel (40%)**: Model parses --parallel but sequential loop. Tests check code contains `go` + `WaitGroup` + `Mutex` and run parallel 8 upload of 320 chunks. Out-of-order assembly bug → final checksum mismatch.

2. **Encryption bugs (25%)**: Forget decrypt on assembly, wrong offset for key cycling, storing checksum of encrypted not original → mismatch. Tests: chunk file differs from original (encrypted), final decrypted matches.

3. **Checksum both (15%)**: Only SHA256, ignoring MD5.

4. **Retry/backoff missing (10%)**: No RETRY: or Sleep backoff.

5. **Edge cases (10%)**: Uppercase, no-ext, multi-dot, symlink, many chunks, encrypt-key mismatch.

Reasoning gaps: concurrency safety, streaming crypto, multi-algo require integration, not setup. Oracle 3/3 deterministic.

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
