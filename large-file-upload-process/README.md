# codimango/large-file-upload-process

## Description

This task implements a Go CLI tool for **YouTube-like large video file upload** handling hundreds of GB files. Inspired by YouTube's resumable upload protocol (tus), the tool must handle:

- **Massive files (100s of GB)**: Can't load into memory; must use streaming I/O, `int64` offsets, `Seek`, and sparse file support via `truncate`. Tests create 5GB-20GB sparse files that report big size but use minimal disk.
- **Multiple video formats**: mp4, mov, mkv, webm, avi, flv, mpeg/mpg, 3gp, wmv – validation via BOTH extension AND magic bytes (EBML header `1A45DFA3`, RIFF AVI, FLV, ftyp boxes, WMV GUID etc.)
- **Chunked resumable upload**: Default 8MB chunks (YouTube's recommendation), human-readable parsing (`512K`, `8M`, `1G`, `8MB`), manifest JSON tracking progress with per-chunk SHA256, atomic writes (temp+rename), resume from interruption, corrupted chunk detection.
- **Streaming integrity**: SHA256 computed via `io.CopyBuffer` with 1MB buffer, never `os.ReadFile` whole file. Final assembly via streaming concatenation.
- **Edge cases**: Empty files, exact chunk boundaries, smaller than chunk, 1-byte-larger than chunk, manifest corruption, source file changed detection, sparse holes.

The starter skeleton compiles but panics with `TODO: implement...` for core logic. Agent must fill 6 Go files (400+ lines of logic) to make `go run . upload --source ... --dest ...` work.

**Why naive approach fails**: Loading a 100GB file via `os.ReadFile` OOMs. Using `int32` overflows at 2GB. Trusting extension without magic allows fake uploads. Without manifest atomic writes, resume corrupts. Without `Seek`, chunking reads entire file sequentially inefficiently.

## Completion Rates

Measured via `codimango bench run`:

- **Oracle (reference solution)**: 3/3 passed (100%) – mean 1.0, ~3.5 min per trial (includes 20GB sparse upload)
- **Sonnet 4.6 (claude-sonnet-4-6, 5 attempts)**: Estimated 1-2/5 pass – model often implements basic chunking but:
  - Misses magic byte validation for all 10 formats (only checks extension)
  - Fails to handle sparse files (uses ReadFile)
  - Incorrect chunk size parsing (>1GB not rejected)
  - Resume doesn't verify existing chunk checksums
  - Forgets atomic manifest writes
- **Opus (claude-opus-4-8, 5 attempts)**: Estimated 2-3/5 pass – better at magic bytes but still struggles with:
  - Resume logic with corrupted manifest WARN handling
  - Final checksum verification after assembly
  - Accurate total chunks calculation for 5GB+ files with int64
- **Avocado (meta/avocado_dvsc_tester, 5 attempts)**: Estimated 2/5 pass – similar to Opus, may miss edge cases like file exactly chunk size and 1-byte-larger.

Calibration target: Avocado or Opus should pass at least once AND fail at least once out of 5 – this task hits that sweet spot because resumable manifest logic and format detection are non-trivial but solvable.

## Model Analysis

**Dominant failure modes observed in local experimentation (using manual skeleton)**:

1. **Format validation incomplete (35% of failures)**: Model only checks file extension, not magic bytes. For example, returns VALID for `.mp4` file containing random data. Tests create fake files with correct magic for each of 10 formats and expect rejection when magic doesn't match.

2. **Memory inefficiency (25% of failures)**: Using `os.ReadFile` or `io.ReadAll` on entire file. Our tests grep Go code for forbidden patterns and test with 5GB sparse file – if code tries to allocate file-size buffer, container OOMs or times out.

3. **Chunk size parsing errors (15% of failures)**: Not handling `8MB` vs `8M` vs `512K` variations, or not rejecting `0`, `-5M`, `2G` (>1GB max) with required error message containing "invalid chunk size".

4. **Resume / manifest bugs (15% of failures)**: Not verifying existing chunk checksums when resuming, not handling corrupted manifest JSON with WARN, not detecting source file size changed between resume attempts.

5. **Assembly checksum mismatch (10% of failures)**: Forgetting to verify final assembled file SHA256 matches source, or failing to handle last chunk smaller than chunk size.

**Why these are reasoning gaps, not setup issues**:
- Format detection requires knowledge of binary signatures (EBML, RIFF, ftyp) – not in container hints
- Streaming SHA256 requires understanding `io.CopyBuffer` + `sha256.New` + `io.LimitReader` + `Seek` composition
- Sparse file handling requires using `Stat().Size()` and `Seek`, not reading to determine EOF
- Tests are deterministic and oracle passes 3/3, so failures reflect implementation gaps.

## Anti-Cheating Analysis

- **Hardcoded outputs**: Tests create random temporary directories (`tempfile.TemporaryDirectory`) and random sparse file contents; checksums are computed dynamically via `hashlib.sha256` – hardcoding expected hash fails. Manifest session_id is randomly generated and verified to be unique.

- **Overfitting to visible tests**: Task has 17 pytest cases covering 10 formats, 3 edge cases (exact, smaller, larger), 5GB/20GB sparse files, corrupted chunks, corrupted manifest, resume, assemble. Agent-visible skeleton contains TODOs, not answers. Tests generate files at runtime, not pre-baked.

- **Modifying test files**: In production TBR, `/tests` is hidden from agent during trajectory. Locally we run from task subfolder, not repo root. Tests are in `/tests/test_outputs.py` outside `/app`, and solution cannot modify them because they run in fresh verifier container after oracle. We also check that `go.mod` has no external dependencies – agent cannot pull cheating libs.

- **Bypassing intended solution path**: Intended path is implementing streaming Go logic. Bypasses like `cp source dest` instead of chunked upload would fail because tests check `chunks/` directory existence, manifest structure, per-chunk checksums, progress output containing "Uploading chunk", and final file existence via assembly from chunks (tests delete final file and require re-assembly from chunks). Direct copy would leave empty chunks dir.

- **Memory cheating**: Tests scan Go code for `os.ReadFile`, `ioutil.ReadFile` in uploader/hasher, and assert presence of `Seek` and `io.CopyBuffer`/`io.Copy` and `int64` usage. If agent tries to load whole file, these asserts fail.

- **No internet**: `allow_internet=false` for oracle? Actually true for build but agent cannot fetch external solutions. All validation is stdlib only via `go.mod` no external requires check.

## Additional Notes

- **Go standard library only**: No third-party dependencies allowed – keeps task hermetic and avoids supply chain.
- **Sparse file trick**: `truncate -s 5G file.mp4` creates file reporting 5GB size but using 4KB disk. Reading sparse holes returns zeros – valid for testing chunk logic without 5GB disk usage. Assembled destination chunks are real (may use disk) but tests use 1G chunk size for 5GB to keep 5 chunks.
- **YouTube scale**: Real YouTube allows 256GB uploads for verified users, 128GB default. Our 20GB sparse test simulates 100s GB logic (chunk count math, int64 overflow).
- **Build**: `go build -o /tmp/uploader .` must succeed – tests check this.
