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
- Unreadable file → non-essential.
- Binary file containing null byte `\x00` → non-essential.

Input files may be hundreds of megabytes and may contain no newline characters at all. Your program must not load an entire file into memory; process each file with a bounded read buffer. Because you read in fixed-size pieces, a pattern can span the boundary between two reads — carry enough trailing bytes from each read into the next so that no match is missed at a boundary.

## Classification
Priority: PII > business-critical > non-essential. If both business and PII signals, it is PII. Implement checks in this order:

1. Empty or whitespace-only → non-essential.
2. Unreadable → non-essential.
3. PII check → if any PII pattern found → pii. PII takes precedence over everything else, including log heuristic and binary check.
4. Binary null-byte check → if file contains `\x00` → non-essential (after PII).
5. Log-file heuristic → applies only to files with at least 3 non-empty lines; a single line that happens to contain a timestamp or level token is not a log file. If >50% of non-empty lines match log pattern, then file is non-essential even if it contains business words. Log pattern: timestamp anchored at start of line `^yyyy-mm-dd` or `^hh:mm:ss` (e.g., `2024-01-01` or `12:34:56`) OR level tokens `INFO|DEBUG|WARN|ERROR|TRACE` matching anywhere in line. Anchors `^` apply per-line for timestamps, level tokens may appear anywhere.

6. Business-critical (Step1 rule – single occurrence sufficient): In Step1, a single occurrence of a business term is sufficient to be business-critical. The keyword set is fixed and closed to: confidential, proprietary, trade secret, financial, revenue, budget, forecast, strategic, merger, acquisition, contract, nda, intellectual property, earnings, profit, balance sheet, board meeting, shareholder. Case-insensitive substring match.

7. Otherwise → non-essential.

- **Business-critical**: files containing sensitive business information. Example keywords above.

- **PII (Step1 heuristic)**: Files containing patterns like SSN (xxx-xx-xxxx), email, phone (xxx-xxx-xxxx or (xxx) xxx-xxxx), credit card (xxxx-xxxx-xxxx-xxxx). Heuristic matching is acceptable in Step1; you will make it precise in Step2. Phone number with context like `Call 123-456-7890` counts as PII.

- **Non-essential**: everything else, including logs, temporary files, empty files, unreadable files, binary files, and predominantly log files as defined above.

## Output (Step1)
JSON array sorted lexicographically by absolute file path. Each `{"file": "<abs path>", "category": "business-critical|pii|non-essential"}`. Empty → `[]` not `null`. Atomic write via temp+rename, no residue.

```
go build -o file-analyzer .
./file-analyzer --dir /data --output ./out.json
```

Implement at `/app`.
