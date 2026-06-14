---
name: review-task-tbench
description: Review the quality of a T-Bench (Harbor) task specification, its associated tests, and the reference solution. Produces a quality analysis and revised task specifications.
---

Your task is to **review the quality** of a T-Bench task specification, its associated tests, and the reference solution, then provide a **revised task description** to improve clarity.

# Input Context

You are provided with a T-Bench task directory with the following structure:

* `instruction.md` — The task specification (the prompt the evaluated agent sees)
* `solution/solve.sh` — Reference solution (ground truth)
* `solution/` — Additional solution files referenced by solve.sh (optional)
* `tests/test.sh` — Test entrypoint (runs pytest, writes reward file)
* `tests/test_outputs.py` — Pytest verification tests
* `environment/Dockerfile` — Docker sandbox setup
* `task.toml` — Task metadata (author, difficulty, timeouts, resources)

# Task Objective

Your objective is twofold:

1. **Quality Review**: Evaluate the quality of the original task specification, the provided tests, and the reference solution, identifying any ambiguities, misalignments, or other issues.

2. **Task Revision**: Create a revised, succinct task specification with improved clarity. The revised specification should enable any expert developer to produce a solution that passes all tests.

**Important**: The revised specification will be used as an evaluation prompt. It must remain clear, concise, and free of hints or privileged information about the solution.

# Task Workflow

## Stage 1: Exploration and Analysis

Read all task files:
1. Read `instruction.md`, `solution/solve.sh`, `tests/test_outputs.py`, `environment/Dockerfile`, and `task.toml`
2. Understand what the Dockerfile sets up (repo, dependencies, base state)
3. Understand what the tests verify — each pytest test checks one behavior
4. Understand how the solution achieves the task

## Stage 2: Test Verification

Execute the test suites to understand the expected functionality.

1. **Run tests WITHOUT the solution** — they should FAIL. Pay close attention to error messages.
2. **Apply the solution and run tests again** — all tests should PASS.
3. Use the Docker environment for testing:
   ```bash
   # Without solution (should fail)
   docker run --rm -v $(pwd)/tests:/tests <image> bash -c "bash /tests/test.sh; cat /logs/verifier/reward.txt"
   
   # With solution (should pass)
   docker run --rm -v $(pwd)/solution:/solution -v $(pwd)/tests:/tests <image> bash -c "bash /solution/solve.sh && bash /tests/test.sh; cat /logs/verifier/reward.txt"
   ```
4. Or use Harbor: `harbor run -p <task-name> -a oracle`

## Stage 3: Quality Assessment

### Criteria for a Good Task Specification

- **No Hints**: Must NOT contain hints about HOW to solve the task. It's an evaluation prompt.
- **Sufficient Detail**: Include details that cannot be inferred from the environment but are required to pass the tests (file paths, expected formats, specific behaviors).
- **No Overfitting**: Don't tailor the spec to the reference solution. Multiple valid solutions should be possible.
- **Brevity**: Don't include background knowledge an expert would already know.
- **Ultimate Criterion**: An expert developer should produce a passing solution based solely on the spec.

### Criteria for Good Tests (`test_outputs.py`)

- Tests should cover representative scenarios and common edge cases
- Tests should not be so loose they accept incorrect solutions
- Tests should not reject correct solutions due to overly strict expectations (e.g., hardcoded strings, exact output formats not mentioned in the spec)
- Each test should check one thing

### Criteria for a Good Solution (`solve.sh`)

- Correctly and completely solves the task
- Minimal — only changes necessary to address the task
- Follows conventions of the target codebase/environment
- Good engineering practices — no hardcoded values, no hacky workarounds

### Generate Quality Analysis

Create a `task_spec_quality_analysis.md` file summarizing missing information:

| # | Description | Inferable from Environment? | Explanation |
|---|-------------|----------------------------|-------------|
| 1 | {Missing info} | Yes / No / Not Sure | {Explanation} |

## Stage 4 (Optional): Additional Research

If you answered "Not Sure" to any items, explore the Docker environment further to determine if the information is available.

## Stage 5: Submission

### Part 1: Quality Assessment

#### 1. Clarity of Task Specification

> **Spec Clarity Rating:** { Good | Requires Some Guessing | Under Specified | Over Specified | Other Quality Issues }
>
> **Explanation:** {Brief explanation}

#### 2. Quality of Tests

> **Test Quality Rating:** { Good | Too Lenient | Too Restrictive | Other Quality Issues }
>
> **Explanation:** {Brief explanation}

#### 3. Quality of Solution

> **Solution Quality Rating:** { Good | Functionally Correct but Poor Engineering | Incomplete Solution | Excess Scope | Other Major Quality Issues }
>
> **Explanation:** {Brief explanation}

### Part 2: Revised Task Specification

Provide **three versions** with varying detail. All versions must:
- Enable an expert to create a passing solution
- NOT disclose test scenarios or solution hints
- Use imperative language in a conversational style
- Not include fenced code blocks

#### Succinct Version
> {Minimal but sufficient description}

#### Regular Version
> {Balanced description with key details}

#### Verbose Version
> {Comprehensive description with all necessary context}
