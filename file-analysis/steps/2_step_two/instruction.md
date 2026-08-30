# Step 2: Improve – Efficiency & Accuracy with Explicit Thresholds and Inversions

Inherit prior session (Step1 code at `/app`). Make system more efficient and accurate. Step2 overturns a Step1 rule and adds non-obvious precedence.

## Preserve Step1
- Still support `--dir`, `--output`, help, exit codes, recursive scan ignoring symlinks, atomic write, sorted output.
- Binary `./file-analyzer`, stdlib only.

## New Flags
- `--workers <int>` default `runtime.NumCPU()`, >0 else exit 2. Help must contain `workers`.
- `--relative` bool flag (presence or `--relative=true`). When present, output file paths are relative to `--dir` (e.g., `/data/sub/a.txt` → `sub/a.txt`). When absent, absolute. Sorting by output path. Help must contain `relative`. This invalidates Step1 absolute-path assumption.
- Support both `--flag value` and `--flag=value`.

## Efficiency
- Worker-pool concurrency with `--workers`, deterministic sorted output.
- Handle 1000 files <5s. Buffered streaming.

## Accuracy – Explicit Thresholds (restored)

**Step1→Step2 inversion (key discriminator):**
- In Step1, a single occurrence of a business term was sufficient for business-critical.
- In Step2, this rule is overturned. Business-critical now requires weighted thresholds:
  - distinct keywords ≥2, OR
  - total keyword occurrences ≥2, OR
  - (≥1 keyword AND financial pattern present: `$` followed by digit like `$5000` or number with `%` like `10%`)
  Otherwise, even with 1 keyword, file is non-essential. Documented reversal forces refactor of Turn-1 logic.

- **Keyword set remains fixed and closed** to: confidential, proprietary, trade secret, financial, revenue, budget, forecast, strategic, merger, acquisition, contract, nda, intellectual property, earnings, profit, balance sheet, board meeting, shareholder. Case-insensitive.

- **Structurally impossible SSNs must be rejected**: SSN pattern `###-##-####` is PII only if valid: area ≠ `000` and ≠ `666` and < `900`, group ≠ `00`, serial ≠ `0000`. Example invalid that must be non-essential: `000-12-3456`.

- **Structurally valid credit cards**: CC is PII only if 13-19 digits after stripping non-digits, Luhn valid, not all same digit (e.g., `1111-1111-1111-1111` not PII). Invalid checksum like `4111-1111-1111-1112` must be non-essential.

- **Email validation**: email rejected as PII if contains consecutive dots `..` or domain missing dot (e.g., `a@b` or `a@b..c` not PII).

- **Phone validation**: phone is PII only if ≥10 digits and not all same digit (e.g., `111-111-1111` not PII). Pattern `xxx-xxx-xxxx` or `(xxx) xxx-xxxx` with context `Call xxx-xxx-xxxx` counts.

- **Null byte / binary**: any file containing null byte `\x00` is non-essential unless valid PII was already found (PII check before binary check).

- **Extension-aware**: files with extensions `.log`, `.tmp`, `.cache`, `.old`, `.swp`, `.temp` that would otherwise be business-critical are downgraded to non-essential, but valid PII still wins.

- **Precedence inversion for .bak**: backup files `.bak` are always non-essential even if they contain valid PII. This contrasts with rest of extension list where PII wins. Intentionally contradicts obvious "PII always wins".

- **Predominantly log files are not business-critical**: if ≥50% of non-empty lines match log pattern (`yyyy-mm-dd` or `hh:mm:ss` or `INFO|DEBUG|WARN|ERROR|TRACE`), file is non-essential even with business words, except escape hatch: if distinct keywords ≥2 AND financial pattern present, keep as business-critical.

- **Sibling-dependent reclassification**: if >70% of files in scanned root are `.log` extension, then any file in that directory that would otherwise be business-critical is downgraded to non-essential. Requires two-phase: count first, then classify.

- **Phone vs CC overlap**: if a CC candidate string contains phone pattern, resolve to phone handling only (do not double-count as CC).

## Output (Step2)
JSON array sorted by output path (absolute or relative). Each element:
`{"file": "<path>", "category": "...", "confidence": 0.0-1.0, "reasons": ["..."]}`
- `file`: absolute unless `--relative` set → relative to `--dir`
- `category`: `business-critical|pii|non-essential`
- `confidence`: 0-1, `reasons`: non-empty
- Empty → `[]`

Example:
```
go build -o file-analyzer .
./file-analyzer --dir /data --output /tmp/out.json --workers 4 --relative
```

Implement at `/app`.
