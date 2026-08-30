# Database Reliability Monitoring — Multi-Turn Go Task

## Overview
Build a database reliability monitoring system in Go with two iterative steps.

**Format:** `terminal_bench_multi_turn` with 2 steps, `inherit_prior_session=true` on step2.

**Steps:**
- **Step1 Reactive Monitoring:** Implement a monitor that tracks per-node health via `CheckResult` feed, maintains sliding window (error rate, avg latency), detects failures (consecutive failures → node_down), emits alerts (high_latency, replication_lag, connection_exhaustion, high_error_rate) with deterministic ordering, provides `GetNodeStatus`, `GetAlerts`, `IsHealthy`, `Reset`, `GetUptimePercentage`, and is concurrency-safe (`sync.RWMutex`, `-race` tested).
- **Step2 Proactive Scoring:** Extend Step1 with health scoring (0-100) using weighted deductions (error_rate*50, latency over*20 cap30, lag over*15 cap20, consec*10 cap40, connections 15/5), trend analysis (latency, error_rate, replication_lag, connections) via older/newer half avg comparison 1.1/0.9 thresholds, failure prediction (RiskLow/Medium/High/Critical with probabilities 0.1/0.35/0.6-0.7/0.8-0.95 and durations 4h/1h/15m/5m), and reliability report (OverallScore avg, ClusterHealth healthy/degraded/unhealthy/critical, Recommendations with required substrings).

**Module:** `db-reliability`, package `db-reliability/reliability`, stdlib only, `go vet` clean, thread-safe.

See `steps/1_step_one/instruction.md` and `steps/2_step_two/instruction.md` for exact API and algorithm specs.

## Validation
Each step runs `go vet ./...` and pytest verifier that imports the Go package via temporary harness (`go run`, `-race` for concurrency). Reward derived from CTRF JSON under `python -I -S` for security.

## Authoring Policy
Before authoring, read Model usage policy v2 and Workplace v2 post. Task seed human-authored, AI as thought partner. No third-party model authors protected content without human review.
