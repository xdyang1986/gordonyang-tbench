# Database Reliability Monitoring — Multi-Turn Go Task

## Overview
Build a database reliability monitoring system in Go with two iterative steps.

**Format:** `terminal_bench_multi_turn` with 2 steps, `inherit_prior_session=true` on step2.

**Steps:**
- **Step1 Reactive Monitoring:** Implement a monitor that tracks per-node health via `CheckResult` feed, maintains sliding window (error rate, avg latency), detects failures (consecutive failures → node_down), emits alerts (high_latency, replication_lag, connection_exhaustion, high_error_rate) with deterministic ordering, provides `GetNodeStatus`, `GetAlerts`, `IsHealthy`, `Reset`, `GetUptimePercentage`, and is concurrency-safe (`sync.RWMutex`, `-race` tested).
- **Step2 Proactive Scoring:** Extend Step1 with health scoring (0-100) derived from worked input->Score examples (independent per-factor penalties each saturating, invariant 100 minus sum), trend analysis (latency, error_rate, replication_lag, connections) with outage exclusion (skip down-period checks) via older/newer half avg comparison, failure prediction with 4 risk levels and durations, and reliability report with recommendations. See step2 instruction for examples.

**Module:** `db-reliability`, package `db-reliability/reliability`, stdlib only, `go vet` clean, thread-safe.

See `steps/1_step_one/instruction.md` and `steps/2_step_two/instruction.md` for exact API and algorithm specs.

## Validation
Each step runs `go vet ./...` and pytest verifier that imports the Go package via temporary harness (`go run`, `-race` for concurrency). Reward derived from CTRF JSON under `python -I -S` for security.

## Authoring Policy
Before authoring, read Model usage policy v2 and Workplace v2 post. Task seed human-authored, AI as thought partner. No third-party model authors protected content without human review.
