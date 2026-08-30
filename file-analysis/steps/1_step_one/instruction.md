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
- Binary file containing null byte `\x00` → non-essential (unless PII – see step2 for refinement, in step1 treat as non-essential if no PII pattern, to keep simple you may treat as non-essential).

## Classification
Priority: PII > business-critical > non-essential. If both business and PII signals, it is PII.

- **Business-critical (Step1 rule – single occurrence sufficient)**: In Step1, a single occurrence of a business term is sufficient to be business-critical. The keyword set is fixed and closed to: confidential, proprietary, trade secret, financial, revenue, budget, forecast, strategic, merger, acquisition, contract, nda, intellectual property, earnings, profit, balance sheet, board meeting, shareholder. Case-insensitive substring match.

- **PII (Step1 heuristic)**: Files containing patterns like SSN (xxx-xx-xxxx), email, phone (xxx-xxx-xxxx or (xxx) xxx-xxxx), credit card (xxxx-xxxx-xxxx-xxxx). Heuristic matching is acceptable in Step1; you will make it precise in Step2. Phone number with context like `Call 123-456-7890` counts as PII.

- **Non-essential**: everything else. Specifically, if file content is predominantly log lines – defined as ≥50% of non-empty lines matching timestamp `yyyy-mm-dd` or `hh:mm:ss` or level `INFO|DEBUG|WARN|ERROR|TRACE` – then it is non-essential even if it contains business words. This discriminator is intentionally in Step1 to increase difficulty.

## Output (Step1)
JSON array sorted lexicographically by absolute file path. Each `{"file": "<abs path>", "category": "business-critical|pii|non-essential"}`. Empty → `[]` not `null`. Atomic write via temp+rename, no residue.

```
go build -o file-analyzer .
./file-analyzer --dir /data --output /tmp/out.json
```

Implement at `/app`.
