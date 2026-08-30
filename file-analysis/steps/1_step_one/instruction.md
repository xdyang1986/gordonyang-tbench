# Step 1: Build File Relevance Analyzer – Core

Build a system to analyze file contents and determine relevance: business-critical, personally identifiable, or non-essential.

## Working Directory
`/app` – Go module `file-analyzer`. Build via `go build -o file-analyzer .` Binary must be `./file-analyzer`.

## CLI
```
file-analyzer --dir <PATH> --output <PATH>
file-analyzer --help, -h, help, no args -> help
```

- Flags support both `--flag value` and `--flag=value`.
- Required: `--dir` (directory to scan recursively) and `--output` (output JSON). Missing → stderr, exit 2.
- `--dir` must exist and be directory → else exit 2.
- Unknown flag → stderr, no stdout, exit 2.
- Help (any help token, or no args) prints to stdout containing `dir`, `output`, `help` and exits 0.
- Create parent dirs of `--output`.
- Exit 0 success, 2 invalid.
- Stdlib only.

## Scanning
- Recursively walk `--dir`, regular files only, ignore symlinks, do not follow.
- Empty dir → `[]`.
- Empty or whitespace-only → non-essential.
- Unreadable file (os.ReadFile fails) → non-essential.
- Binary file containing null byte `\x00` → non-essential.

## Classification
Priority and ordering (must be implemented in this order to match reference):

1. Empty or whitespace-only → non-essential.
2. PII check → if any PII pattern found → pii. PII takes precedence over everything else, including log heuristic and binary check.
3. Binary null-byte check → if file contains `\x00` → non-essential (after PII).
4. Log-file heuristic → if file has at least 3 lines (guard: single line containing timestamp/level token is NOT a log file), and ≥50% of non-empty lines match log pattern, then file is non-essential even if it contains business words. Log pattern: timestamp anchored at start of line `^yyyy-mm-dd` or `^hh:mm:ss` (e.g., `2024-01-01` or `12:34:56`) OR level tokens `INFO|DEBUG|WARN|ERROR|TRACE` matching anywhere in line. Anchors `^` apply per-line for timestamps, level tokens may appear anywhere.

5. Business-critical (Step1 rule – single occurrence sufficient): In Step1, a single occurrence of a business term is sufficient to be business-critical. The keyword set is fixed and closed to: confidential, proprietary, trade secret, financial, revenue, budget, forecast, strategic, merger, acquisition, contract, nda, intellectual property, earnings, profit, balance sheet, board meeting, shareholder. Case-insensitive substring match.

6. Otherwise → non-essential.

- **Business-critical**: files containing sensitive business information. Example keywords above suggest business-critical.

- **PII (Step1 heuristic)**: Files containing patterns like SSN (xxx-xx-xxxx), email, phone (xxx-xxx-xxxx or (xxx) xxx-xxxx), credit card (xxxx-xxxx-xxxx-xxxx). Heuristic matching is acceptable in Step1; you will make it precise in Step2. Phone number with context like `Call 123-456-7890` counts as PII.

- **Non-essential**: everything else, including logs, temporary files, empty files, unreadable files, binary files, and predominantly log files as defined above.

## Output (Step1)
JSON array sorted lexicographically by absolute file path. Each `{"file": "<abs path>", "category": "business-critical|pii|non-essential"}`. Empty → `[]` not `null`. Atomic write via temp+rename, no residue.

```
go build -o file-analyzer .
./file-analyzer --dir /data --output /tmp/out.json
```

Implement at `/app`.
