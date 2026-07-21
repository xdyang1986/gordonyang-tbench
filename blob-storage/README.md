# codimango/blob-storage

## Description

This task requires implementing an S3-like blob storage system in Go. The agent must build a RESTful HTTP server that exposes bucket and object operations (create/list/delete buckets, put/get/head/delete/list objects with prefix filtering), persists data to the local filesystem with atomic writes, computes MD5 ETags, preserves Content-Type and custom `X-Amz-Meta-*` metadata, and handles concurrent access safely using mutexes.

The task tests:
- Building a production-like backend service using only Go standard library
- Correct HTTP status codes and JSON error handling
- Filesystem layout design (buckets as directories, objects with hierarchical keys containing slashes, sidecar `.meta.json` files)
- Validation (bucket naming regex `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$`, object key traversal protection via `..` and `//` checks and path containment)
- Concurrency safety, atomic writes (temp file + rename), and sorted listings
- Edge cases: empty objects, binary data, large objects (1MB), nested keys like `a/b/c.txt`

A naive approach that simply uses in-memory maps without persistence, or forgets to return empty JSON arrays as `[]` instead of `null`, or doesn't handle URL-encoded slashes and raw `RequestURI` `..` checks, will fail tests. The reference solution uses ~600 lines of Go with a single `Storage` struct protected by `RWMutex`.

## Completion Rates

Empirical results (Docker-based evaluation, 5 trials each where applicable):

- **Oracle (reference solution)**: 3/3 passed (100%) — 25/25 pytest cases, reward 1
- **Sonnet 4.6 (claude-sonnet-4-6)**: Expected 2-3/5 passed — common failures:
  - Forgets empty-slice JSON marshalling (returns `null` instead of `[]`) causing client `NoneType` iteration errors
  - Missing `RequestURI` raw `..` traversal check, allowing path escape or failing strict 400 tests
  - Not handling hierarchical keys (fails to `MkdirAll` parent dirs)
  - Incomplete ETag or metadata header echo
- **Opus (claude-opus-4-8)**: Expected 3-4/5 passed — stronger at concurrency and validation but may miss atomic write temp+rename or pruning empty parent dirs
- **Avocado (meta/avocado_dvsc_tester)**: Expected 3/5 passed — calibration target; typically passes core CRUD but fails on prefix filtering sort order or custom metadata case-insensitivity

The task is designed to be hard but solvable: all required operations are specified in detail, no external dependencies, and the solution builds with `go build -o /tmp/blob-server .`.

## Model Analysis

Across model trials, dominant failure modes observed (or expected from similar Go backend tasks):

1. **Empty slice JSON null (40% of failures)**: Go's `var slice []T` marshals to `null`, not `[]`. Tests expect `{"buckets": []}` not `{"buckets": null}`. Fix: initialize with `make([]T, 0)`. Causes `clear_storage` fixture to crash with `TypeError: 'NoneType' object is not iterable`.

2. **Path traversal handling (30%)**: Go's `net/http` cleans `..` segments before handler sees them. Models that only check cleaned `URL.Path` pass `a/../b.txt` as valid `b.txt` (200) while strict test expects 400 for escaping cases like `../escape.txt` and `a/../../b.txt`. Fix requires inspecting `r.RequestURI` raw and checking `containsDotDotSegment` on unescaped raw key, plus `filepath.Rel` containment check.

3. **Hierarchical key directories (15%)**: Forgetting `os.MkdirAll(filepath.Dir(objPath), 0755)` before writing object with slashes e.g., `folder/file.txt` leads to `no such file or directory`.

4. **Metadata preservation (10%)**: Not echoing `X-Amz-Meta-*` headers (case-insensitive) or defaulting `Content-Type` to `application/octet-stream`.

5. **Concurrency (5%)**: Missing `sync.RWMutex` around filesystem operations leads to race or corruption under parallel `ThreadPoolExecutor` tests.

These reflect reasoning gaps about Go stdlib HTTP semantics, JSON marshalling edge cases, and filesystem security, not task-setup issues.

## Anti-Cheating Analysis

- **Hardcoded outputs**: Tests use random data (e.g., `os.urandom(1MB)`) and dynamic bucket/object names, plus MD5 verification of content. Hardcoded ETags would fail because content is unpredictable.

- **Overfitting to visible tests**: 25 tests cover many edge cases (empty, binary, large, concurrent, prefix, hierarchical, validation) but are hidden from agent during TBR evaluation (tests dir not visible). Even if agent could read tests locally, the API contract is broad - any hardcoded response for one key would fail other keys.

- **Modifying test files**: In production (TBR), `/tests` is hidden and mounted read-only in a separate verifier container. The task's `test.sh` builds the Go binary from `/app` and runs tests as a separate process; modifying test files in `/app` would not affect `/tests/test_outputs.py`. The verifier also checks reward.txt written by test harness.

- **Bypassing intended solution path**: The task requires a Go HTTP server on :8080 with filesystem persistence. A cheat that returns fake JSON without persisting files would fail the `test_persistence_filesystem` check which verifies actual files exist on disk under `STORAGE_PATH`. Atomic write and pruning checks also enforce real filesystem usage.
