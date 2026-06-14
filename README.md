# terminal-bench-template

A template repository for creating [Harbor](https://harborframework.com) benchmark tasks for evaluating coding agents in containerized terminal environments..

## Getting Started

Install [Harbor](https://harborframework.com/docs/getting-started), then run the included tasks:

```bash
harbor run -d hello-world -a oracle
```

Or initialize a new task from scratch:

```bash
harbor tasks init "<task-name>"
```

## Task Structure

Each task lives in its own directory:

```
<task-name>/
├── task.toml              # Task metadata and configuration
├── instruction.md         # The prompt given to the agent
├── environment/
│   └── Dockerfile         # Container environment the agent works in
├── tests/
│   ├── test.sh            # Verifier entrypoint (runs tests, writes reward)
│   └── test_<name>.py     # Pytest test cases to verify the agent's solution
└── solution/              # (Optional) Reference solution for oracle testing
    └── solve.sh
```

### `task.toml`

Defines task metadata, timeouts, and resource limits:

```toml
version = "1.0"

[metadata]
author_name = "Your Name"
author_email = ""
difficulty = "easy"              # easy | medium | hard
category = "software-engineering"
tags = ["hello-world"]
expert_time_estimate_min = 5.0   # (Optional) Estimated time for an expert
junior_time_estimate_min = 15.0  # (Optional) Estimated time for a junior dev

[verifier]
timeout_sec = 60.0               # Max time for the test suite

[agent]
timeout_sec = 120.0              # Max time for the agent to solve the task

[environment]
cpus = 2                         # (Optional) CPU cores
memory = "4G"                    # (Optional) Memory limit
storage = "10G"                  # (Optional) Storage limit
build_timeout_sec = 600.0        # (Optional) Docker build timeout
# docker_image = "user/image:tag"  # (Optional) Pre-built image instead of Dockerfile
```

### `instruction.md`

The task prompt shown to the agent. Should clearly describe what to build and where to put it.

### `environment/Dockerfile`

Sets up the container the agent works in:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
```

### `tests/test.sh`

The verifier entrypoint. Installs dependencies, runs pytest, and writes a reward file to `/logs/verifier/`:

- `reward.txt` — a single number (`1` for pass, `0` for fail)
- `reward.json` — (alternative) a JSON object with multiple metrics

### `tests/test_<name>.py`

Standard pytest test cases that verify the agent's solution.

### `solution/solve.sh` (Optional)

A reference solution script. When provided, you can validate your task end-to-end using the Oracle agent (`harbor run -p <task-name> -a oracle`).

## Included Tasks

| Task | Difficulty | Description |
|------|-----------|-------------|
| `hello-world` | Easy | Write a Python script that computes the sum of ASCII values of a name |

## Creating a New Task

1. Run `harbor tasks init "<task-name>"` or copy the `hello-world/` directory
2. Update `task.toml` with your metadata, timeouts, and resource limits
3. Write your task prompt in `instruction.md`
4. Set up the container environment in `environment/Dockerfile`
5. Write pytest tests in `tests/test_<name>.py`
6. Update `tests/test.sh` to point to your test file
7. (Optional) Add a reference solution in `solution/solve.sh` and validate with `harbor run -p <task-name> -a oracle`

For more examples, see [terminal-bench-2](https://github.com/codimango/terminal-bench-2) which contains 89 tasks across diverse domains. For full documentation, see the [Harbor task docs](https://harborframework.com/docs/tasks) and the [task tutorial](https://harborframework.com/docs/tasks/task-tutorial).
