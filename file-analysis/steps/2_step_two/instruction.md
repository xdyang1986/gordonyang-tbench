# Step 2: Improve File Relevance Analyzer – Efficiency & Accuracy

You inherit prior session (Step1 code at `/app`). Step1 built a basic analyzer. Now make it more efficient and accurate.

## Working Directory
`/app` – same module. Binary `./file-analyzer`.

Build:
```
go build -o file-analyzer .
```

## Must Preserve Step1 Contract

- `--dir <path>` and `--output <path>` still required, support both `--flag value` and `--flag=value`.
- `--help`, `-h`, `help`, no args → help containing `dir`, `output`, `help` exit 0.
- Unknown flag → exit 2 no stdout.
- Missing args or invalid dir → exit 2.
- Recursive scan, ignore symlinks, regular files only.
- Output JSON array sorted lexicographically by file path.
- Each entry must have at least `file` and `category` (same values `business-critical`, `pii`, `non-essential`). Atomic write via temp+rename, parent dir creation.
- Binary at `./file-analyzer`.

## New Flag – Efficiency

Add:
- `--workers <int>` : number of worker goroutines, default `runtime.NumCPU()`. Accept `--workers 4` and `--workers=4`. Must be >0 else exit 2. Help must contain `workers`.

## Efficiency Requirements – Must Be Implemented

1. **Worker-pool concurrency**: Use goroutines pool to process files concurrently. Number of workers = `--workers`. Must produce deterministic sorted output regardless of concurrency (sort after collection).
2. **Buffered / streaming read**: Do not hold all files in memory at once. Use `bufio` or streaming. For large file (10MB) should not cause OOM. Reading whole file per file is acceptable if streamed via buffer, but you must not load entire directory into memory as single blob. Use incremental processing.
3. **Performance target**: Scanning 1000 small files (total ~10MB) must complete <5 seconds wall-clock on 1 CPU (measured in tests). Concurrency should help.
4. No lock residue, no temp residue.

## Accuracy Requirements – Must Be Implemented

Improve detection to reduce false positives and false negatives.

### PII – Improved with validation

- **SSN validation**: Beyond regex `\b\d{3}-\d{2}-\d{4}\b`, reject invalid SSNs:
  - First group (area): not `000`, not `666`, not `900-999`
  - Second group: not `00`
  - Third group: not `0000`
  - Example invalid that Step1 would classify pii but Step2 must NOT: `000-12-3456`, `666-45-6789`, `900-12-3456`, `123-00-6789`, `123-45-0000`

- **Credit card Luhn check**: Regex finds candidates `\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b` plus maybe 13-19 digits, but Step2 must validate:
  - Strip spaces and dashes, must be 13-19 digits, Luhn valid, not all same digit, not sequential trivial.
  - Implement Luhn algorithm.
  - Example: `4111-1111-1111-1111` valid Luhn → pii
  - Example: `4111-1111-1111-1112` invalid Luhn → NOT pii (Step1 would say pii, Step2 must say non-essential if no other signals)
  - Also reject if contains letters.

- **Email**: stricter, but still detect; ensure domain has TLD >=2, no consecutive dots.

- **Phone**: same regex but optionally filter.

If any VALID PII passes validation → pii.

### Business-critical – Weighted scoring

Reduce false positives from single keyword in log.

Rules:

- Count distinct keywords and total occurrences (case-insensitive substring).
- Detect financial patterns:
  - `\$\s*\d`  e.g., `$123`, `$ 123.45`
  - `\b\d+(\.\d+)?\s*%` e.g., `10%`
  - `\b\d+\s*(USD|EUR|dollars)\b` case-insensitive
  - Maybe table header? For simplicity, financial pattern = `$` + digit or `%`.

- Business-critical if ANY:
  - Distinct keyword count >=2
  - OR total occurrences >=2
  - OR (distinct >=1 AND financial pattern present)

Otherwise, even if 1 keyword present alone (e.g., file with only "budget" word in isolation like log "INFO budget processing") → NOT business-critical → non-essential (unless PII).

This improves accuracy: single keyword in log should be non-essential.

### Non-essential – Extension-aware & log-pattern & binary

- **Extension-aware**: If file extension (lowercased) in `[".log", ".tmp", ".cache", ".bak", ".old", ".swp", ".temp"]` → classify as non-essential UNLESS valid PII present. So extension overrides business-critical, but PII still wins.
  - Example: `notes.tmp` containing "confidential" but no PII → Step1 says business-critical, Step2 must say non-essential.

- **Empty/whitespace** → non-essential (confidence high).

- **Binary detection**: If file contains null byte `\x00` → treat as binary → non-essential unless PII present. Detect by reading first 1KB or scanning content for `\x00`.

- **Log-pattern detection**: If file looks like a log (majority lines match log timestamp pattern), treat as non-essential unless PII.
  - Define log line regex: `^\d{4}-\d{2}-\d{2}` or `^\[?\d{2}:\d{2}:\d{2}` or contains `\b(INFO|DEBUG|WARN|ERROR|TRACE)\b` with timestamp-ish.
  - If file has >=3 lines and >50% lines match log pattern → log file → non-essential unless PII.
  - Example: file with 10 lines all "2024-01-01 INFO budget processing" contains keyword budget but should be non-essential in Step2.

- Priority remains: valid PII > business-critical > non-essential, but extension/log-override applies to business-critical downgrading.

### Confidence and Reasons – Extended Output

Step2 output must include extra fields while preserving file and category:

Each element:
```json
{
  "file": "/path/a.txt",
  "category": "pii",
  "confidence": 0.95,
  "reasons": ["ssn pattern 123-45-6789", "email pattern john@example.org"]
}
```

- `confidence`: float 0..1
- `reasons`: array of strings explaining detection

Requirements:
- Must include `file`, `category`, `confidence`, `reasons` for each entry in Step2.
- `confidence`:
  - pii: 0.9-0.95 if strong valid PII, 0.8 if single.
  - business-critical: 0.5 + 0.1*distinct up to 0.95, plus 0.1 if financial pattern.
  - non-essential: 0.95 empty, 0.9 extension, 0.85 binary, 0.8 log pattern, 0.6 otherwise.
- `reasons`: non-empty array.
- Still sorted by file path lexicographically.
- Still valid JSON array, `[]` not `null`.

## Examples

```bash
go build -o file-analyzer .
./file-analyzer --help
./file-analyzer --dir /data --output /tmp/out.json --workers 4
./file-analyzer --dir=/data --output=/tmp/out.json --workers=8
```

Accuracy examples for tests:

- `valid_cc.txt` with "Card 4111-1111-1111-1111" → pii (valid Luhn)
- `invalid_cc.txt` with "Card 4111-1111-1111-1112" → non-essential (invalid Luhn, Step1 would be pii)
- `invalid_ssn.txt` with "SSN 000-12-3456" → non-essential (Step1 pii)
- `notes.tmp` with "confidential financial" → business-critical in Step1, non-essential in Step2 (extension override)
- `app.log` with 20 lines "2024-01-01 INFO budget processing completed" → business-critical in Step1, non-essential in Step2 (log pattern + single keyword)
- `biz_financial.txt` with "confidential revenue $50000 and budget forecast 10% increase merger" (2+ distinct + financial pattern) → business-critical in both, confidence higher in Step2

Performance example: 1000 files should finish <5s.

## Constraints

- Preserve Step1 flags and behavior, add `--workers`.
- Go stdlib only.
- Concurrency via worker pool, deterministic sorted output.
- Atomic write.
- Handle help, exit codes as before plus workers validation.
- Implement Luhn, SSN validation, extension-aware, binary detection, log-pattern.
- Output includes confidence and reasons in Step2.

Implement at `/app` extending Step1.

