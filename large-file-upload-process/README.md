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

## Completion Rates

- **Oracle**: 3/3 (6/6 in cloud) validated in 2:50-5:17
- **Sonnet 4.6**: Expected 0/5 — requires parallel worker IDs + retry backoff + both checksums + XOR + many chunks 320 + symlink/no-ext/uppercase — local run 0/5 in previous hard-mode test
- **Opus**: Expected 1-2/5
- **Avocado**: 2/5 validated online (metacode), codex 5/5 in one run but 0/5 in another due to infra

Hard mode pushes difficulty from easy (1/5 Sonnet) to hard (0/5).

## Model Analysis

- **Parallel not parallel (40%)**: Parses --parallel but sequential loop. Behavioral check: with parallel 4, output must contain multiple worker IDs (`worker 0`, `worker 1` etc). Sequential only shows worker 0 → fails. Also manifest parallel field must match requested.

- **Encryption (25%)**: Forget decrypt on assembly, wrong key cycling, checksum of encrypted not original.

- **Checksum both (15%)**: Only SHA256.

- **Retry/backoff (10%)**: Must print `RETRY:` + `backoff` + `100ms` on injected failure via `INJECT_FAIL_CHUNK`. Previously grep-only, now behavioral via env var injection.

- **Edge (10%)**: Uppercase, no-ext, multi-dot, symlink, many chunks, encrypt-key mismatch, custom manifest path, WARN parallel/algo changed.

## Anti-Cheating Analysis

- **Hardcoded**: `tempfile.TemporaryDirectory()` + dynamic SHA256/MD5 via hashlib + random session_id.

- **Overfitting**: 30 tests covering 10 formats, magic mismatch, info-invalid, symlink, no-ext/uppercase/multi-dot, exact/smaller/+1-byte, 5GB/10GB sparse int64, resume, corruption, manifest corrupted/custom path, source changed, encrypt-key mismatch, assemble, help, parallel flag validation (1/2/4/8 + worker IDs + manifest parallel), retries flag validation (0/3/5 + injection with RETRY: + backoff 100ms), checksum algo (md5/both + both lengths), encryption XOR (encrypted differs, final decrypted matches, assemble decrypt, mismatch), many small chunks 320/640, parallel out-of-order, combined hard (parallel 8 + both + encrypt + 512K), WARN resume (parallel changed, algo changed).

- **Modifying tests**: /tests hidden in TBR.

- **Bypassing**: Must have chunks dir, manifest fields parallel/checksum_algo/encrypt_key, UPLOAD COMPLETE with Parallel:/ChecksumAlgo: (+ ChecksumMD5: when both), ASSEMBLE COMPLETE, many-chunks count.

- **Memory**: 5GB sparse in 4GB limit — ReadFile OOMs (-9). Now purely behavioral via upload, not brittle Seek grep.

- **Concurrency**: Behavioral via worker IDs in stdout, not literal source scan for go/WaitGroup/Mutex/Sleep (previous Medium was grep-only, now behavioral).

- **No internet**: stdlib only.
