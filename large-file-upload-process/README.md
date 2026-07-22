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

## Completion Rates (online validation — commit 974d309, 2026-07-22)

- **Oracle**: **3/3** — validated
- **Opus 4.8 (agent)**: **5/5** — failed (solved every trial → too easy for this model)
- **GPT-5.5 (codex)**: **2/5** — validated (the 3 losses were infra errors, not test failures)
- **Avocado (metacode)**: **1/2** — validated
- avgReward **0.56**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. In the latest validation, **no model produced a genuine test failure** — every non-passing trial across GPT-5.5 and Avocado was `status=error` (Daytona `ThrottlerException: Too Many Requests` / build-harness failures that score 0), while every *clean* (completed) trial passed.

- **Opus 4.8 (agent) — 5/5 clean passes.** Flagged too easy for this model.
- **GPT-5.5 (codex) — 2/5.** Both completed trials passed; the 3 losses were `status=error` infra flakes. No real test failures.
- **Avocado (metacode) — clean trials all pass.** Every loss was `status=error` infra. No real test failures.
- **Oracle — 3/3.**

**Assessment:** once infrastructure noise is removed, all models solve the task on clean trials — it is currently **too easy** (Opus already 5/5, and GPT-5.5/Avocado show zero genuine test failures). The sub-5/5 headline scores are provisioning instability, not reasoning difficulty. To reach the 20-80% sweet spot the task needs real hardening (e.g. stricter resume / manifest-corruption edge cases, tighter streaming-memory enforcement, or adversarial format-detection cases) rather than relying on flaky infra to suppress pass rates.

> Note: an earlier run on the prior commit showed genuine misses (Opus on the exact `ASSEMBLE COMPLETE:` output string per instruction.md §138; GPT-5.5 on `test_memory_efficiency_code_scan`), but those did not reproduce as failures in the latest clean trials.

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
