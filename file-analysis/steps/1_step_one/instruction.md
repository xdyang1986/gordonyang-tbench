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
- Required: `--dir` (directory to scan recursively) and `--output` (output JSON file). Missing → stderr, exit 2.
- `--dir` must exist and be a directory → else exit 2.
- Unknown flag → stderr, no stdout, exit 2.
- Help (any help token, or no args) prints to stdout containing `dir`, `output`, `help` and exits 0.
- Create parent dirs of `--output` if needed.
- Exit 0 success, 2 invalid.

- Stdlib only: `go.mod` must have no external requires.

## Scanning
- Recursively walk `--dir`, regular files only, ignore symlinks, do not follow.
- Empty dir → output empty array.
- Empty or whitespace-only file → non-essential.
- Handle unreadable files gracefully.

## Classification Goal
Decide per file whether content is business-critical, PII, or non-essential.

Priority: PII > business-critical > non-essential. If a file contains both business and PII signals, it is PII.

- **Business-critical**: files containing sensitive business information such as financial data, strategic plans, mergers, confidential business discussions. Example keywords include confidential, financial, revenue – the full set is fixed and closed, do not invent new keywords.

- **PII**: files containing personally identifiable information such as social security numbers, email addresses, phone numbers, credit card numbers. For step1, heuristic pattern matching is acceptable (you will make it more precise in step2).

- **Non-essential**: everything else, including logs, temporary files, empty files, and files without sensitive signals. Specifically, if a file's content is predominantly log lines (timestamp + level like INFO/DEBUG/WARN/ERROR), it should be considered non-essential even if it contains business words.

## Output (Step1)
JSON array sorted lexicographically by file path (absolute path as discovered). Each entry `{"file": "<absolute path>", "category": "business-critical|pii|non-essential"}`. Empty → `[]` not `null`. Atomic write via temp file in same directory then rename, no residue.

Example workflow:
```
go build -o file-analyzer .
./file-analyzer --dir /data --output /tmp/out.json
```

Implement at `/app`.
