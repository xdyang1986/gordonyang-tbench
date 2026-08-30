# Step 1: Build File Relevance Analyzer – Core Classification in Go

You need to build a system that analyzes file contents and determines relevance: business-critical, personally identifiable (PII), or non-essential.

## Working Directory
`/app` – Go module `file-analyzer` (already initialized with `go.mod`). All code at `/app`.

Build via:
```
go build -o file-analyzer .
```
Binary must be `./file-analyzer` (and `/app/file-analyzer`).

## CLI Contract – Must Match Exactly

```
file-analyzer --dir <PATH> --output <PATH>
file-analyzer --help
file-analyzer -h
file-analyzer help
file-analyzer (no args) -> help
```

- Flags:
  - `--dir <path>` : directory to scan recursively (required for scan). Accepts `--dir /path` and `--dir=/path`.
  - `--output <path>` : output JSON file path (required for scan). Accepts `--output /path` and `--output=/path`.
  - `--help`, `-h`, `help` : help. Any occurrence triggers help.
  - Help must print to stdout, contain strings `dir`, `output`, `help` (case-insensitive check) and exit 0. Bare binary no args must also print help exit 0.
  - Unknown flag (e.g., `--unknown`, `-x`) → stderr, no stdout, exit 2.
  - Missing required `--dir` or `--output` when not in help mode → stderr, exit 2.
  - `--dir` path must exist and be a directory → else stderr, exit 2.
  - Parent directories of `--output` must be created if needed (`mkdir -p`).

- Exit codes: 0 success, 2 invalid args/config.

- Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

## Scanning Behavior

- Recursively walk `--dir`.
- Only regular files. Ignore directories themselves, symlinks (do not follow, do not include symlink file if symlink), device files, etc. If entry is symlink, skip.
- If directory empty → output empty array `[]`.
- File reading: read entire file as text (UTF-8). If file empty (0 bytes) or whitespace only after TrimSpace → non-essential. If file contains null byte `\x00`, treat as binary → attempt to still classify but null does not by itself cause failure.
- Large files up to 10MB must be handled (reading whole file is okay in Step1). Do not crash on unreadable files: if ReadFile fails, skip file with warning to stderr but continue (or classify as non-essential). For simplicity, classify unreadable as non-essential.

## Classification – Step1 Basic Heuristics

Priority: `pii` > `business-critical` > `non-essential`

### PII detection (any match → pii)

Use these regexes (case-sensitive where noted, but email is case-insensitive for local checks; implement case-insensitive where practical):

- SSN: `\b\d{3}-\d{2}-\d{4}\b`  e.g., `123-45-6789`
- Email: `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`
- Phone US: `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b` and also `\(\d{3}\)\s*\d{3}[-.]\d{4}` – cover `(123) 456-7890`, `123-456-7890`, `123.456.7890`, `1234567890`? Require at least the first form; second optional.
- Credit card: `\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b` (16 digits with optional dash/space)

If any regex matches file content, classify as `pii`.

### Business-critical detection

Case-insensitive keyword search. List (all lowercased):

```
confidential
proprietary
trade secret
financial
revenue
budget
forecast
strategic
merger
acquisition
contract
nda
intellectual property
earnings
profit
balance sheet
board meeting
shareholder
```

If content lowercased contains any keyword (substring match) → `business-critical` unless already PII.

### Non-essential fallback

Otherwise → `non-essential`. Also empty files → non-essential.

## Output Format – Step1

JSON array sorted lexicographically by `file` path ascending.

Each element:
```json
{"file": "/absolute/path/to/file.txt", "category": "business-critical"}
```
- `file`: absolute path as discovered (join dir + relative). Use the actual path from Walk (should be absolute if --dir absolute). Preserve as absolute.
- `category`: exactly one of `business-critical`, `pii`, `non-essential`

- File must be valid JSON array, not object. Empty → `[]` not `null` (use non-nil empty slice).
- Atomic write: create temp file in same directory as output (`<output>.tmp.<pid>` or similar) via `os.CreateTemp`, write JSON, then `os.Rename` to final. No temp residue after success. Create parent dirs via `MkdirAll`.
- Use `json.MarshalIndent` or `json.Marshal` – either okay, but must be valid JSON. Sorting required.
- No extra fields required in Step1 (but may include extra if desired; tests only check file and category). Keeping only file and category is safest.

## Examples

```bash
go build -o file-analyzer .
./file-analyzer --help
./file-analyzer --dir /tmp/data --output /tmp/out.json
cat /tmp/out.json
# [{"file":"/tmp/data/a.txt","category":"business-critical"}, ...]
```

Test data example:
- `/tmp/data/biz.txt` containing "This document contains confidential financial revenue forecast for Q4 merger." → business-critical
- `/tmp/data/pii.txt` containing "Contact SSN: 123-45-6789 Email: john@example.org" → pii
- `/tmp/data/log.txt` containing "INFO 2024 start" → non-essential
- `/tmp/data/mixed.txt` containing both confidential and SSN → pii (priority)

## Constraints

- Go stdlib only, no external deps.
- Must build with `go build -o file-analyzer .` from `/app`.
- Handle help, unknown flags, missing args with exit 2 (help exits 0).
- Output sorted, valid JSON, atomic write.
- Recursive scan, ignore symlinks.
- Empty dir → [].

Implement at `/app` – this is Step1 core.
