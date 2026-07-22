# Large File Upload Processor for YouTube-like Video Platform (Go)

You are building a Go CLI tool that handles uploading massive video files (hundreds of GBs) for a YouTube-like platform. Think YouTube's resumable upload protocol - users upload 100GB+ 4K videos from unreliable networks.

Working directory: `/app`. Go module already initialized (`go.mod` with module `largefileuploader`, Go 1.23). Starter skeleton exists with TODOs - you must implement the missing logic.

## Problem Context

The current uploader crashes or OOMs on large files because it tries to load entire files into memory. It also lacks:
- Proper video format validation (magic bytes, not just extension)
- Resumable chunked upload with manifest tracking
- Streaming SHA256 checksums
- Support for huge sparse files without disk blowup

## Requirements

### 1. Supported Video Formats & Validation

Support at least: `mp4`, `mov`, `mkv`, `webm`, `avi`, `flv`, `mpeg`, `mpg`, `3gp`, `wmv`

**Validation must check BOTH extension AND magic bytes (file signatures).** Don't trust extension alone.

Magic byte rules (check first 16 bytes, provide best-effort detection):

- `mp4`: byte 4-7 is `ftyp`, and bytes 8-11 contains `isom`, `iso2`, `mp41`, `mp42`, `avc1`, `dash`, `msnv` etc. Essentially contains `ftyp`.
- `mov`: byte 4-7 is `ftyp` with `qt` brand, OR starts with `moov` or `mdat` + `ftypqt`
- `mkv`: starts with `0x1A 0x45 0xDF 0xA3` (EBML)
- `webm`: same EBML header as MKV, but header after should contain `webm` string within first 64 bytes
- `avi`: starts with `RIFF` and bytes 8-11 is `AVI `
- `flv`: starts with `FLV` (0x46 0x4C 0x56)
- `mpeg`/`mpg`: starts with `0x00 0x00 0x01` followed by `0xB3` or `0xBA`, OR starts with `0x47` sync byte for TS (optional)
- `3gp`: byte 4-7 `ftyp` and bytes 8-11 contains `3gp`
- `wmv`: starts with `0x30 0x26 0xB2 0x75 0x8E 0x66 0xCF 0x11`

If file < 16 bytes, it's INVALID. Empty file INVALID. Unsupported extension INVALID unless magic matches known type (then report detected type).

`validate` command output format:
- Valid: `VALID: <format>` e.g., `VALID: mp4` (lowercase)
- Invalid: `INVALID: <reason>` e.g., `INVALID: unsupported format xyz` or `INVALID: magic mismatch expected mp4 got ...` or `INVALID: file too small`

Exit code 0 for valid, 1 for invalid.

### 2. Chunk Size Parsing

Must parse human-readable sizes:
- `512K` or `512KB` or `512k` => 512*1024
- `8M` or `8MB` or `8m` => 8*1024*1024
- `1G` or `1GB` => 1*1024*1024*1024
- Plain number => bytes
- Case-insensitive, optional `B` suffix
- Reject: zero, negative, > 1GB chunk (max 1GB), non-numeric. Error message must contain "invalid chunk size"

Default chunk size: `8M` (8388608 bytes) — YouTube's recommended.

### 3. Streaming & Memory Efficiency (CRITICAL for 100s of GB)

- **MUST NOT load entire file into memory.** No `os.ReadFile`, `ioutil.ReadFile`, `io.ReadAll` on full file. Only chunk-sized buffers (e.g., 8MB + small overhead) allowed.
- Must handle files larger than RAM (tests include sparse 10GB+ files created via `truncate` - Stat size reports big, but disk usage tiny, and reading should use Seek).
- Use `int64` for offsets/sizes — must handle >4GB (int32 overflow).
- Checksum: streaming SHA256 — `io.Copy` through hash, chunk by chunk, not loading whole file.
- Chunking: `io.Seek` + `io.ReadFull` with fixed buffer.

Tests will grep your code for forbidden patterns AND test with 2GB+ sparse files to catch OOM.

### 4. Manifest Format (Resumable Upload)

JSON file tracking upload progress. Example `/tmp/upload.json`:

```json
{
  "session_id": "random-uuid-or-nano-id",
  "source_file": "/path/to/video.mp4",
  "source_size": 107374182400,
  "chunk_size": 8388608,
  "total_chunks": 12800,
  "file_checksum": "sha256 hex",
  "chunks": [
    {"index": 0, "offset": 0, "size": 8388608, "checksum": "ab12...", "uploaded": true, "path": "chunks/chunk_000000"},
    {"index": 1, "offset": 8388608, "size": 8388608, "checksum": "cd34...", "uploaded": false, "path": "chunks/chunk_000001"}
  ],
  "created_at": "2024-01-02T15:04:05Z",
  "updated_at": "2024-01-02T15:04:05Z",
  "dest_dir": "/dest/path"
}
```

Requirements:
- `session_id`: unique per upload (uuid, timestamp+rand, etc.)
- `source_size`: int64 from file stat
- `total_chunks = ceil(source_size / chunk_size)` — handle last chunk smaller
- Each chunk: index, offset (int64), size (int64), checksum (SHA256 hex), uploaded bool, path relative to manifest's dest chunk dir
- Must be able to resume: if manifest exists, re-validate already uploaded chunks by checksumming dest chunk files, mark corrupted as not uploaded, continue.
- Atomic manifest updates: write to temp + rename, or sync after each chunk.
- Timestamps: RFC3339

### 5. Upload Logic

`upload` command: `go run . upload --source <src> --dest <dest-dir> --chunk-size 8M --manifest <manifest.json>`

- `--source`: required, path to video file
- `--dest`: required, directory simulating remote storage (create if not exists)
- `--chunk-size`: optional, default 8M
- `--manifest`: optional, default `<dest>/<filename>.manifest.json`

Steps:
1. Validate source file format (fail if invalid)
2. Stat size (int64)
3. If manifest exists from prior run for same source+dest: load and resume (verify existing chunk files checksums)
4. Else create new manifest + session_id
5. Ensure `dest/chunks/` exists
6. Upload chunks sequentially (in order) — for each not-yet-uploaded chunk:
   - Seek source to offset, read `size` bytes (streaming, buffer = chunk_size or 1MB internal)
   - Compute SHA256 of chunk
   - Write chunk to `dest/chunks/chunk_%06d` (e.g., `chunk_000000`). Use atomic write (temp file + rename) to avoid partial writes.
   - Verify written chunk checksum
   - Update manifest: mark uploaded, store checksum, update timestamp, atomic write manifest
   - Print progress: `Uploading chunk X/Y (Z%)` or JSON line — at least print something per chunk to stdout for progress tracking
7. After all chunks done: assemble final file to `dest/<source_basename>` by concatenating chunks in order streaming (no loading all chunks into memory). Use `io.Copy`.
8. Compute SHA256 of assembled file, compare to source file SHA256 (streaming). If mismatch, fail: `ERROR: final checksum mismatch`
9. On success print: `UPLOAD COMPLETE: <dest>/<filename> Size: <bytes> Checksum: <sha256> Chunks: <total>`

Resume capability: If process interrupted (manifest partially done), re-running upload with same args should continue from where left off, not re-upload already valid chunks.

Corrupted chunk detection: If a chunk file exists but checksum doesn't match manifest expected (or manifest says uploaded but file missing/corrupt), re-upload it.

Sparse file handling: Use `os.Stat` size, not reading to determine EOF. Seek must work beyond real data — reading sparse holes returns zeros (valid). Must not try to allocate file-size buffer.

### 6. Other CLI Commands

- `validate --file <path>`: as described
- `info --file <path>`: prints JSON to stdout:
  ```json
  {"file": "/path", "size": 12345, "format": "mp4", "valid": true, "checksum": "sha256 hex (first 10MB or full? Compute full streaming)", "chunk_info": {"chunk_size": 8388608, "total_chunks": 2}}
  ```
  If invalid format, valid=false and format is "unknown" or detected. Still prints size. Checksum can be computed for full file streaming (required). If <10MB compute full; requirement says full SHA256 anyway for integrity.

- `upload` as above
- `assemble --manifest <path> --output <path>`: manual assembly from manifest chunks (useful for testing). Reads manifest, concatenates chunks to output, verifies checksum if file_checksum present.

All commands must handle `--help` / `-h` with usage.

### 7. Go Specific Requirements

- Module: `largefileuploader` already in `go.mod`
- Go 1.23
- Entry: `main.go` in package main at `/app/main.go` with subpackages allowed at `/app/internal/...` or `/app/...` but `go run .` must work from `/app`.
- Must not have external dependencies beyond stdlib (allowed to use only standard library). No third-party packages — keep `go.mod` clean (no `require` external).
- Use `flag` or hand-rolled cli parsing — no cobra dependency.
- Error handling: meaningful messages, exit codes 0=success, 1=validation/failure, 2=usage error.
- Concurrency: sequential upload is fine (no need goroutines), but code should be concurrency-safe for future parallelization (manifest atomic updates).
- Must work on Linux (container is Ubuntu 24.04 with Go 1.23).

### 8. Edge Cases (Must Handle)

- Empty file: INVALID
- File exactly chunk size, smaller than chunk, 1 byte larger than chunk
- Last chunk smaller than chunk size
- Destination exists with partial chunks — resume correctly
- Manifest corrupted JSON — start fresh with warning to stderr: `WARN: corrupted manifest, starting fresh`
- Source file modified between resume attempts (size changed) — detect and error: `ERROR: source file changed since manifest creation (size mismatch)`
- Unsupported format but magic matches known — should still validate as that known format if magic is clear? Actually require both extension AND magic to align OR magic alone if extension missing? Simplest: check extension first, if in supported list, then verify magic matches extension type; if extension not supported but magic matches supported type, accept as valid (report detected format). If neither matches, invalid.
- Chunk size parsing edge: `0`, `-1`, `abc`, `8MBB`, `9999G` (>1GB) should fail with "invalid chunk size"
- Symlink source: follow link, validate target
- Very large file simulation: tests will create 5GB sparse file via `truncate -s 5G file.mp4` + write magic bytes at start. Your code must handle this without trying to allocate 5GB.
- 100s of GB scenario: similar, 100GB sparse file, chunk count = 100GB/8MB = 12800 chunks. Should handle calculation with int64 without overflow, and not OOM.

### 9. Expected File Layout After Success

After `upload`:
```
<dest>/
  <filename>                # assembled final file
  <filename>.manifest.json  # final manifest
  chunks/
    chunk_000000
    chunk_000001
    ...
```

### 10. Build & Run

Tests will run:

```bash
cd /app
go build -o /tmp/uploader .        # must build
/tmp/uploader validate --file /tmp/test.mp4
go run . validate --file /tmp/test.mp4
go run . upload --source /tmp/big.mp4 --dest /tmp/dest --chunk-size 4M
```

If `go run .` doesn't work, task fails.

### What to Implement

Look at `/app/*.go` — multiple TODOs commented with `// TODO:`. You must fill them. Key files:

- `main.go`: CLI dispatch, flag parsing
- `formats.go`: Supported formats, magic detection, validation
- `chunk.go`: Chunk size parsing, chunk calculation, chunk reading
- `hasher.go`: Streaming SHA256 file & chunk
- `manifest.go`: Manifest create/load/save/resume logic
- `uploader.go`: Core upload + assemble logic

You may refactor, add files, but keep `go run .` working and don't break expected CLI interface described.

### Success Criteria

- All CLI commands work as specified
- Memory efficient (no whole-file reads)
- Handles sparse huge files (int64, seek)
- Resumable upload works (manifest tracking + atomic writes)
- Format validation via extension + magic bytes
- Chunk size parser human-readable
- Final assembled file SHA256 matches source
- No external dependencies (stdlib only)
- Exit codes correct

Example happy path:

```bash
truncate -s 20M /tmp/sample.mp4
printf '\x00\x00\x00\x18ftypisom' | dd of=/tmp/sample.mp4 bs=1 seek=0 conv=notrunc 2>/dev/null
go run . validate --file /tmp/sample.mp4  # VALID: mp4
go run . info --file /tmp/sample.mp4
go run . upload --source /tmp/sample.mp4 --dest /tmp/upload_dest --chunk-size 4M
ls /tmp/upload_dest/sample.mp4  # assembled file exists
sha256sum /tmp/sample.mp4 /tmp/upload_dest/sample.mp4  # should match
```

### Notes on YouTube-Scale

YouTube handles:
- Files up to 256GB (or 128GB) for verified users
- Resumable upload protocol via tus
- Chunked transfer with checksums
- Transcoding pipeline after upload

Your task simulates the upload ingestion part — focus on robust chunk handling, not transcoding.

Start by reading all Go files in `/app`, understanding TODOs, then implement.

Good luck!
