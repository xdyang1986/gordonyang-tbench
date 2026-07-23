# Large File Upload Processor for YouTube-like Video Platform (Go) — HARD MODE

You are building a Go CLI tool that handles uploading massive video files (hundreds of GBs) for a YouTube-like platform. Think YouTube's resumable upload protocol - users upload 100GB+ 4K videos from unreliable networks with parallel workers, encryption, and multiple checksum algos.

Working directory: `/app`. Go module already initialized (`go.mod` with module `largefileuploader`, Go 1.23). Starter skeleton exists with TODOs - you must implement the missing logic. This is HARD MODE - requires concurrency, crypto, and robust error handling.

## Problem Context

The current uploader crashes or OOMs on large files because it tries to load entire files into memory. It also lacks:
- Proper video format validation (magic bytes, not just extension) for 10 formats
- Resumable chunked upload with manifest tracking
- Streaming SHA256/MD5 checksums with multiple algo support
- Support for huge sparse files without disk blowup
- Parallel upload with worker pool
- Retry with exponential backoff
- Streaming XOR encryption

## Requirements

### 1. Supported Video Formats & Validation

Support at least: `mp4`, `mov`, `mkv`, `webm`, `avi`, `flv`, `mpeg`, `mpg`, `3gp`, `wmv`

**Validation must check magic bytes FIRST (more reliable than extension).**

Magic byte rules (check first 16 bytes, provide best-effort detection):

- `mp4`: byte 4-7 is `ftyp`, and bytes 8-11 contains `isom`, `iso2`, `mp41`, `mp42`, `avc1`, `dash`, `msnv` etc.
- `mov`: byte 4-7 is `ftyp` with `qt` brand, OR starts with `moov`
- `mkv`: starts with `0x1A 0x45 0xDF 0xA3` (EBML)
- `webm`: same EBML header as MKV, but header after should contain `webm` string within first 64 bytes
- `avi`: starts with `RIFF` and bytes 8-11 is `AVI `
- `flv`: starts with `FLV` (0x46 0x4C 0x56)
- `mpeg`/`mpg`: starts with `0x00 0x00 0x01` followed by `0xB3` or `0xBA`, OR starts with `0x47` sync byte for TS
- `3gp`: byte 4-7 `ftyp` and bytes 8-11 contains `3gp`
- `wmv`: starts with `0x30 0x26 0xB2 0x75 0x8E 0x66 0xCF 0x11`

**Format resolution policy (clear, no ambiguity)**:
1. Try magic-byte detection first. If magic matches a supported format, return that format as VALID, regardless of extension. This handles files with no extension or wrong extension.
2. If magic does not match any supported format, then treat file as INVALID. If extension is supported but magic mismatches, output must be `INVALID: magic mismatch ...` (contain "magic mismatch").
3. If extension is unsupported and magic unknown, output `INVALID: unsupported format <ext>`.
4. Uppercase extensions must be handled: `.MP4`, `.MKV` etc. should be treated case-insensitively.
5. No-extension files: if magic matches supported format, VALID with detected format. If no magic match and no extension, INVALID.
6. Multiple dots: `my.video.backup.mp4` → ext `mp4` (last suffix).

If file < 16 bytes, it's INVALID. Empty file INVALID.

`validate` command output format:
- Valid: `VALID: <format>` e.g., `VALID: mp4` (lowercase)
- Invalid: `INVALID: <reason>` e.g., `INVALID: unsupported format xyz` or `INVALID: magic mismatch expected mp4 got avi` or `INVALID: file too small`

Exit code 0 for valid, 1 for invalid.

### 2. Chunk Size Parsing

Must parse human-readable sizes:
- `512K` or `512KB` or `512k` => 512*1024
- `8M` or `8MB` or `8m` => 8*1024*1024
- `1G` or `1GB` => 1*1024*1024*1024
- Plain number => bytes
- Case-insensitive, optional `B` suffix
- Support spaces: `8 MB` should also parse (trim spaces)
- Reject: zero, negative, > 1GB chunk (max 1GB), non-numeric. Error message must contain "invalid chunk size"

Default chunk size: `8M` (8388608 bytes).

### 3. Streaming & Memory Efficiency (CRITICAL for 100s of GB)

- **MUST NOT load entire file into memory.** No `os.ReadFile`, `ioutil.ReadFile`, `io.ReadAll` on full file. Only chunk-sized buffers (e.g., 8MB + small overhead) allowed, ideally 1MB internal buffer reused.
- Must handle files larger than RAM (tests include sparse 10GB+ files created via `truncate` - Stat size reports big, but disk usage tiny, and reading should use Seek).
- Use `int64` for offsets/sizes — must handle >4GB (int32 overflow).
- Checksum: streaming SHA256/MD5 — `io.Copy` through hash, chunk by chunk, not loading whole file.
- Chunking: `io.Seek` + `io.ReadFull` with fixed buffer.
- Many small chunks: tests will use 64KB chunk size with 20MB file = 320 chunks. Must handle many chunks without leaking file descriptors or growing memory.

Tests enforce streaming behaviorally via memory limit (4096 MB) and large sparse file uploads (5GB+). Implementations loading entire file will OOM or timeout. Only chunk-sized buffers allowed.

### 4. Manifest Format (Resumable Upload)

JSON file tracking upload progress. Example:

```json
{
  "session_id": "random-uuid",
  "source_file": "/path/to/video.mp4",
  "source_size": 107374182400,
  "chunk_size": 8388608,
  "total_chunks": 12800,
  "file_checksum": "sha256 hex",
  "file_checksum_md5": "md5 hex (if both)",
  "checksum_algo": "sha256|md5|both",
  "chunks": [
    {"index": 0, "offset": 0, "size": 8388608, "checksum": "sha256 ab12...", "checksum_md5": "md5 cd34...", "uploaded": true, "path": "chunks/chunk_000000"},
    {"index": 1, "offset": 8388608, "size": 8388608, "checksum": "sha256...", "uploaded": false, "path": "chunks/chunk_000001"}
  ],
  "created_at": "2024-01-02T15:04:05Z",
  "updated_at": "2024-01-02T15:04:05Z",
  "dest_dir": "/dest/path",
  "parallel": 4,
  "encrypt_key": "mykey (if used)"
}
```

Requirements:
- `session_id`: unique per upload (uuid, crypto/rand)
- `source_size`: int64 from file stat
- `total_chunks = ceil(source_size / chunk_size)` — handle last chunk smaller
- Each chunk: index, offset (int64), size (int64), checksum(s), uploaded bool, path relative
- `checksum_algo`: records which algo(s) used
- `parallel`: number of workers used
- `encrypt_key`: if encryption used, store key (or hash) for verification? Store original key or empty if none. For simplicity store key string if provided, else "".
- Must be able to resume: if manifest exists, re-validate already uploaded chunks by checksumming dest chunk files, mark corrupted as not uploaded, continue.
- Atomic manifest updates: write to temp + rename, with mutex for parallel workers.
- Timestamps: RFC3339
- Thread-safe: parallel workers must not corrupt manifest — use mutex or sync after each chunk with lock.

### 5. Upload Logic — HARD MODE

`upload` command: `go run . upload --source <src> --dest <dest-dir> --chunk-size 8M --parallel 4 --retries 3 --checksum both --encrypt-key mysecret --manifest <manifest.json>`

Flags:
- `--source`: required, path to video file (symlink allowed, must follow)
- `--dest`: required, directory simulating remote storage (create if not exists)
- `--chunk-size`: optional, default 8M
- `--manifest`: optional, default `<dest>/<filename>.manifest.json`
- `--parallel`: optional, int 1-32, default 4, number of concurrent upload workers. Must actually use goroutines (`go func`, `sync.WaitGroup`, channels, `sync.Mutex` for manifest). Sequential (1) must still work. Manifest must store `parallel` field.
- `--retries`: optional, int 0-10, default 3, number of retries for failed chunk upload with exponential backoff: `backoff = 100ms * 2^attempt` (e.g., 100ms, 200ms, 400ms). Must retry on transient errors (e.g., temp file write failure, checksum mismatch). On each retry, print `RETRY: chunk X attempt Y backoff <duration>` to stderr (must contain `RETRY:`).
- `--checksum`: optional, string `sha256` (default), `md5`, or `both`. If `md5`, file_checksum and chunk checksums are MD5 hex (32 chars). If `both`, manifest must store both SHA256 (64-char) in `file_checksum`/`checksum` and MD5 (32-char) in `file_checksum_md5`/`checksum_md5`. Final file verification checks both when algo=both.
- `--encrypt-key`: optional, string key for XOR encryption. If provided, each chunk's bytes are XORed with key bytes cycling with chunk offset: `enc_byte[i] = orig[i] XOR key[(chunk_offset+i) % len(key)]` before writing to dest. Must be streaming with 1MB buffer. On assembly, if manifest has `encrypt_key`, decrypt similarly via XOR. Final assembled file checksum must match source (decrypted). If encrypt-key differs on resume, error `ERROR: encrypt key mismatch`.

**Retry testing hook (documented for verification)**: For automated retry verification, honor env var `INJECT_FAIL_CHUNK`. When set to a chunk index (e.g., `INJECT_FAIL_CHUNK=0`), the upload of that chunk MUST fail on first attempt with synthetic transient error (e.g., `injected failure`), triggering retry+backoff and `RETRY:` log, then succeed on next attempt. This hook is part of retry feature and must be implemented to allow grading of retry logic without real I/O flakiness.

Steps:
1. Validate source file format (fail if invalid) - follow symlinks via os.Stat (which follows links by default)
2. Stat size (int64) via os.Stat
3. Parse flags: chunk size (human-readable with optional spaces), parallel (1-32, error "invalid parallel" if out of range), retries (0-10, error "invalid retries"), checksum algo (must be sha256|md5|both, error "invalid checksum algo"), encrypt-key (any string, may be empty = no encryption)
4. Compute source file checksum(s) streaming based on requested algo (sha256 uses 64-char hex, md5 32-char, both computes both in single pass via MultiWriter)
5. If manifest exists: load and resume with defined policy:
   - If corrupted JSON, print `WARN: corrupted manifest, starting fresh` to stderr and create new manifest
   - If source_size != current size → error `ERROR: source file changed since manifest creation (size mismatch)` and exit 1
   - If encrypt_key in manifest != provided encrypt-key → error `ERROR: encrypt key mismatch` and exit 1
   - If parallel in manifest differs from requested parallel → print `WARN: parallel changed from <old> to <new>, using new` to stderr and use the newly requested parallel value for workers
   - If checksum_algo in manifest differs from requested → print `WARN: checksum algo changed from <old> to <new>, using new algo but re-verifying` to stderr, use new algo, and re-verify all existing chunks under new algo (reset uploaded flags if checksum fields missing)
   - For verification of existing chunks: chunk files on disk may be encrypted if encrypt_key is set. Checksum fields in manifest always refer to ORIGINAL unencrypted data. Therefore verification must read chunk file, decrypt via XOR with encrypt_key if present, then compute checksum(s) of decrypted data and compare to manifest's stored checksum(s). If size matches and checksums match, mark uploaded true; else mark false and re-upload.
   - After verification, continue uploading remaining chunks
6. Else create new manifest with new session_id, file checksums, parallel, encrypt_key, checksum_algo
7. Ensure `dest/chunks/` exists
8. Save initial manifest
9. **Parallel upload**: 
   - Create channel of chunk indices that are not yet uploaded
   - Start `parallel` workers (goroutines) with `sync.WaitGroup`
   - Each worker:
     - For each chunk index from channel, attempt upload with retries:
       - Seek source file (need per-worker file handle or mutex around Seek, because os.File Seek is not thread-safe - use separate file handle per worker or mutex)
       - Read chunk via LimitReader
       - Compute checksum(s) of original data
       - If encrypt-key: XOR encrypt the chunk data with key
       - Write chunk atomically to `dest/chunks/chunk_%06d` (temp file + rename) with retry+backoff
       - Verify written chunk: read back, if encrypted decrypt, compute checksum, compare
       - On failure, retry up to `retries` times with exponential backoff, print `RETRY: chunk X attempt Y` to stderr
       - On success, update manifest: mark uploaded, store checksums, update timestamp, save atomically with mutex protection
       - Print progress thread-safely: `Uploading chunk X/Y (Z%)` 
   - Wait for all workers via WaitGroup
   - Must handle many chunks (320+ with 64KB size) without leaking file descriptors
10. After all chunks: assemble final file to `dest/<basename>` streaming
   - If encrypted, decrypt each chunk while assembling (XOR)
   - Use `io.Copy` with 1MB buffer, no loading all chunks
11. Compute final checksum(s) of assembled file streaming, compare to source checksum(s) based on algo. If mismatch, fail `ERROR: final checksum mismatch` (or `ERROR: final md5 mismatch` for md5).
12. On success print:
    - For algo=sha256: `UPLOAD COMPLETE: <dest>/<filename> Size: <bytes> Checksum: <sha256> Chunks: <total> Parallel: <parallel> ChecksumAlgo: sha256`
    - For algo=md5: `UPLOAD COMPLETE: <dest>/<filename> Size: <bytes> Checksum: <md5> Chunks: <total> Parallel: <parallel> ChecksumAlgo: md5`
    - For algo=both: `UPLOAD COMPLETE: <dest>/<filename> Size: <bytes> Checksum: <sha256> ChecksumMD5: <md5> Chunks: <total> Parallel: <parallel> ChecksumAlgo: both`
    All fields must be present as specified; manifest must contain both checksums for both case.

Resume capability: re-running upload with same args (including parallel, encrypt-key, checksum) should continue from where left, re-using manifest. Must handle parallel changes with WARN and checksum algo changes with WARN as defined in step 5.

Resume capability: re-running upload with same args (including parallel, encrypt-key, checksum) should continue from where left.

Corrupted chunk detection: If chunk file exists but after decrypt checksum doesn't match manifest expected, re-upload.

Encryption: If `--encrypt-key` provided, manifest stores `encrypt_key`. On resume, must use same key; if different key provided on resume, error `ERROR: encrypt key mismatch`.

### 6. Other CLI Commands

- `validate --file <path>`: as described
- `info --file <path>`: prints JSON:
  ```json
  {"file": "/path", "size": 12345, "format": "mp4", "valid": true, "checksum": "sha256 hex", "checksum_md5": "md5 hex (if both or md5)", "chunk_info": {"chunk_size": 8388608, "total_chunks": 2}}
  ```
  If invalid format, valid=false and format="unknown". Still prints size and checksums.
  Must handle `--checksum` flag: if `both`, include both `checksum` (sha256) and `checksum_md5`. If `md5` only, `checksum` should be md5? For simplicity: `checksum` always sha256 unless algo=md5, then checksum is md5. If both, `checksum` is sha256 and `checksum_md5` is md5.

- `upload` as above with new flags

- `assemble --manifest <path> --output <path>`: manual assembly from manifest chunks. Reads manifest, concatenates chunks to output streaming, decrypts if encrypt_key present, verifies checksum if file_checksum present. On success print: `ASSEMBLE COMPLETE: <output_path>`

All commands must handle `--help` / `-h`.

### 7. Go Specific Requirements

- Module: `largefileuploader` already in `go.mod`
- Go 1.23, stdlib only (no external deps)
- Entry: `main.go` at `/app/main.go`, `go run .` must work
- Error handling: exit codes 0=success, 1=validation/failure, 2=usage error
- **Concurrency**: Must use goroutines for parallel>1, `sync.WaitGroup`, `sync.Mutex` for manifest, channels for work distribution. Code must be race-free (should pass `go test -race` if there were tests, and not have data races on manifest).
- Must handle `parallel` flag parsing and validation
- Must handle `encrypt-key` XOR streaming correctly
- Must handle multiple checksum algos

### 8. Edge Cases (Must Handle)

- Empty file: INVALID
- File exactly chunk size, smaller than chunk, 1 byte larger
- Last chunk smaller
- Resume with partial chunks — must work with parallel>1 and sequential
- Manifest corrupted JSON — `WARN: corrupted manifest, starting fresh`
- Source changed size mismatch — `ERROR: source file changed`
- Format resolution: magic first, then unsupported. Uppercase extensions `.MP4` must work. No-extension files with valid magic must VALID. Multiple dots `my.video.backup.mp4` → ext mp4. Magic mismatch: if ext supported but magic mismatches, `INVALID: magic mismatch...` contain "magic mismatch". Symlink: follow link, validate target.
- Chunk size parsing: `0`, `-1`, `abc`, `8MBB`, `9999G` (>1GB) → "invalid chunk size". Also `8 MB` with space should parse.
- Parallel flag: `0`, `-1`, `33`, `abc` → "invalid parallel" (must be 1-32). Default 4.
- Retries flag: `-1`, `11`, `abc` → "invalid retries" (0-10). Default 3.
- Checksum flag: `xxx` → "invalid checksum algo". Must be sha256|md5|both.
- Encrypt-key: any string allowed, including empty (no encryption). If provided on resume with different key → `ERROR: encrypt key mismatch`.
- Very large file: 5GB sparse file via `truncate -s 5G file.mp4` + magic
- 100s GB: 10GB-20GB sparse files, info reports correct size and chunk count via int64. Must not OOM.
- Many small chunks: 64KB chunk size with 20MB file = 320 chunks, must handle without leaking FDs.
- Parallel upload with many chunks must preserve correctness even if chunks uploaded out-of-order.

### 9. Expected File Layout After Success

After `upload`:
```
<dest>/
  <filename>
  <filename>.manifest.json
  chunks/
    chunk_000000 (may be encrypted if key provided)
    ...
```

### 10. Build & Run

```bash
cd /app
go build -o ./uploader .        # must build
./uploader validate --file /tmp/test.mp4
go run . validate --file /tmp/test.mp4
go run . upload --source /tmp/big.mp4 --dest /tmp/dest --chunk-size 4M --parallel 4 --checksum both
go run . upload --source /tmp/big.mp4 --dest /tmp/dest --chunk-size 4M --parallel 8 --encrypt-key mysecret --retries 5
```

### What to Implement

Look at `/app/*.go` — TODOs. Key files:
- `main.go`: CLI, flag parsing for parallel/retries/checksum/encrypt
- `formats.go`: magic detection with uppercase/no-ext/multiple-dots support
- `chunk.go`: chunk size parsing, total chunks, parallel validation
- `hasher.go`: SHA256 + MD5 streaming, both
- `manifest.go`: manifest with parallel, encrypt_key, checksum_algo, thread-safe save, checksum fields for both algos
- `uploader.go`: parallel worker pool, retry+backoff, XOR encryption streaming, thread-safe manifest

### Success Criteria (Hard Mode)

- All CLI commands with new flags work
- Parallel upload actually uses goroutines (`go` keyword, `WaitGroup`, `Mutex`, channel)
- Retry logic with exponential backoff prints `RETRY: ...`
- Multiple checksum algos stored in manifest
- Encryption XOR streaming works and final file matches source after decrypt
- No external deps, stdlib only
- Memory efficient, handles sparse huge files
- Resumable, atomic writes, thread-safe
- Exit codes correct
- Handles uppercase, no-ext, symlink, many small chunks

Example happy path hard:

```bash
truncate -s 20M /tmp/sample.mp4
printf '\x00\x00\x00\x18ftypisom' | dd of=/tmp/sample.mp4 bs=1 seek=0 conv=notrunc 2>/dev/null
go run . upload --source /tmp/sample.mp4 --dest /tmp/dest --chunk-size 4M --parallel 4 --checksum both --encrypt-key secret123
sha256sum /tmp/sample.mp4 /tmp/dest/sample.mp4  # must match
go run . assemble --manifest /tmp/dest/sample.mp4.manifest.json --output /tmp/final.mp4
```

Good luck — this is hard!
