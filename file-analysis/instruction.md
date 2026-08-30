# File Relevance Analyzer – Multi-Turn Go Task

Multi-turn: Turn1 builds core file classification (see steps/1_step_one/instruction.md), Turn2 improves efficiency with worker-pool concurrency and accuracy with Luhn, SSN validation, extension-aware and log-pattern heuristics (see steps/2_step_two/instruction.md).

Build: `go build -o file-analyzer .` in `/app/`, module `file-analyzer`, Go stdlib only.

