# File Relevance Analyzer – Multi-Turn Go Task

Multi-turn: Step1 core classification by content relevance (see steps/1_step_one/instruction.md). Step2 adds efficiency (worker pool) and accuracy with non-obvious rules: backup precedence inversion, sibling-dependent log downgrade, relative path flag (see steps/2_step_two/instruction.md).

Build: `go build -o file-analyzer .` in `/app`, module `file-analyzer`, Go stdlib only.

