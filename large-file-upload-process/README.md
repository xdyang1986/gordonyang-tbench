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

## Completion Rates (online validation — commit 37aa7fa, 2026-07-22)

- **Oracle**: **3/3** — validated
- **Opus 4.8 (agent)**: **1/5** — validated
- **GPT-5.5 (codex)**: **1/5** — validated
- **Avocado (metacode)**: **0/7** — failed
- avgReward **0.43**, validation passing — a hard task; only Oracle solves it reliably.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. Two distinct factors drive the low pass rates: genuine spec-compliance gaps and heavy infrastructure flakiness.

- **Infrastructure noise — the largest single factor.** A majority of non-passing trials across GPT-5.5 and Avocado were `status=error` (Daytona `ThrottlerException: Too Many Requests` / build-harness failures that score 0), not test failures. In their clean trials these models often passed, so the headline 1/5 and 0/7 overstate the true reasoning difficulty. Example: the GPT-5.5 validation job was 4× `status=error` + 1 clean pass; an Avocado job was 4× `status=error` + 3 clean passes.

- **Opus 4.8 — genuine miss on the `assemble` output contract.** Both real completed failures failed *only* `test_assemble_command` (18/19). The model assembled the file correctly (right size 15,728,640 bytes, correct SHA256, 3 chunks) but printed `ASSEMBLED: <path> Size: ... Checksum: ... Chunks: 3` — the `UPLOAD COMPLETE` format — instead of the spec-mandated `ASSEMBLE COMPLETE: <output_path>` (instruction.md §138). A spec-reading / output-contract failure, not an algorithmic one.

- **GPT-5.5 — genuine miss on the memory-efficiency scan.** Its one real completed failure failed *only* `test_memory_efficiency_code_scan` (16/17): the code tripped the forbidden whole-file-read check (`os.ReadFile` / `ioutil.ReadFile`) or lacked the required streaming patterns (`Seek`, `io.CopyBuffer`, `int64`).

- **Oracle — 3/3.** Reference solution passes every clean trial.

**Assessment:** the true discriminators observed are narrow (exact `assemble` success string; memory-efficiency code scan) rather than the deep streaming/format reasoning the task targets. Much of the low online pass rate is provisioning instability, not reasoning depth — the task is worth re-running to separate infra noise from real difficulty before treating 1/5–0/7 as its genuine hardness.

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
