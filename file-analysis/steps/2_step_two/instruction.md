# Step 2: Improve – Efficiency & Accuracy with Non-Obvious Rules

Inherit prior session (Step1 code at `/app`). Make system more efficient and accurate, and introduce semantics that contradict obvious implementation.

## Preserve Step1
- Still support `--dir`, `--output`, help, exit codes, recursive scan ignoring symlinks, atomic write, sorted output.
- Binary `./file-analyzer`, stdlib only.

## New Flags
- `--workers <int>` default `runtime.NumCPU()`, must be >0 else exit 2. Help must contain `workers`.
- `--relative` bool flag (no value, just presence, also support `--relative=true`). When present, output file paths are relative to `--dir` (e.g., `--dir /data` file `/data/sub/a.txt` → output `sub/a.txt`). When absent, absolute paths as before. Sorting is by the path that is output (relative when flag present). Help must contain `relative`. This invalidates Step1's absolute-path assumption and forces refactor.

## Efficiency Requirements
- Use worker-pool concurrency with `--workers` goroutines, deterministic sorted output regardless of concurrency.
- Handle 1000 files efficiently (<5s). Use buffered streaming, not loading whole directory blob.

## Accuracy – Goal Oriented (not literal regexes)

Improve detection to reject structurally impossible identifiers and reduce false positives:

- **Structurally impossible SSNs**: reject SSNs with impossible area/group/serial (e.g., all-zero groups, well-known invalid area codes). Goal: reduce false positives from syntactically correct but impossible SSNs.

- **Structurally valid credit cards**: require Luhn check and reasonable length, not all same digit. Goal: invalid checksum cards should not be considered PII.

- **Predominantly log files are not business-critical**: if a file's content is predominantly log lines (timestamp + level), it should be considered non-essential even if it contains business words.

- **Extension-aware with precedence inversion**: backup files with `.bak` extension are always considered non-essential, even if they contain valid PII. This is intentional and contradicts the natural "PII always wins" rule. You must implement this inversion.

- **Sibling-dependent reclassification**: if more than 70% of files in the scanned root directory are log files (by `.log` extension), then any file in that directory that would otherwise be business-critical is downgraded to non-essential. This requires a two-phase pass: first count file types, then decide categories. It defeats per-file decomposition and gives concurrency teeth.

- **Phone vs credit card overlap**: if a 16-digit candidate overlaps with phone pattern context, careful span handling is needed; do not double-count.

## Output (Step2)
JSON array sorted by output path (absolute or relative depending on flag). Each element must have:
`{"file": "<path>", "category": "...", "confidence": 0.0-1.0, "reasons": ["..."]}`
- `file`: absolute when `--relative` absent, relative to `--dir` when present.
- `category`: `business-critical|pii|non-essential`
- `confidence`: float 0-1, `reasons`: non-empty array explaining decision.
- Empty → `[]`.

Backward compat: `--dir` and `--output` still work, output still sorted and valid JSON.

Example:
```
go build -o file-analyzer .
./file-analyzer --dir /data --output /tmp/out.json --workers 4 --relative
```

Implement improvements extending Step1 at `/app`.
