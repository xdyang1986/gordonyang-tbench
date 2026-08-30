# Database Reliability Monitoring — Step 2: Proactive Scoring Mechanism

This step extends Step 1's reactive monitor with proactive health scoring, trend analysis, failure prediction, and reliability reporting.

You inherit all files from Step 1. Keep backward compatibility: all Step 1 functionality must continue to pass.

## Extended Package Layout

```
/app/
  go.mod (module db-reliability)
  reliability/
    monitor.go   (from step1, extended or kept)
    scoring.go   (new file for scoring logic, or you can keep all in monitor.go)
```

You may split into multiple files but package must remain `reliability` and import path `db-reliability/reliability`.

## New Types to Add (must match exactly)

```go
type HealthScore struct {
    NodeID      string
    Score       float64            // 0-100
    Factors     map[string]float64 // breakdown: error_rate, latency, replication_lag, consecutive_failures, connections
    LastUpdated time.Time
    Trend       string // "improving", "degrading", "stable"
}

type RiskLevel string
const (
    RiskLow      RiskLevel = "low"
    RiskMedium   RiskLevel = "medium"
    RiskHigh     RiskLevel = "high"
    RiskCritical RiskLevel = "critical"
)

type FailurePrediction struct {
    NodeID                 string
    RiskLevel              RiskLevel
    Probability            float64       // 0.0-1.0
    PredictedFailureWithin time.Duration
    Reasons                []string
    Timestamp              time.Time
}

type ReliabilityReport struct {
    Timestamp       time.Time
    OverallScore    float64 // avg of all nodes, 100 if no nodes
    Nodes           []HealthScore
    Predictions     []FailurePrediction
    ClusterHealth   string // "healthy", "degraded", "unhealthy", "critical"
    Recommendations []string
}
```

## New Methods to Add to Monitor

```go
func (m *Monitor) GetHealthScore(nodeID string) (HealthScore, bool)
func (m *Monitor) GetAllHealthScores() []HealthScore
func (m *Monitor) PredictFailure(nodeID string) (FailurePrediction, bool)
func (m *Monitor) GetReliabilityReport() ReliabilityReport
func (m *Monitor) GetTrend(nodeID string, metric string) string
```

`metric` for GetTrend is one of: "latency", "error_rate", "replication_lag", "connections". Return "stable" for unknown metric or unknown node or insufficient history (<4 checks).

## Scoring Algorithm (must implement exactly for deterministic tests)

This algorithm defines expected behavior. Tests will verify your scoring follows these rules (with tolerance).

### Data you must track per node (beyond Step1)

- Keep history of last WindowSize entries (or more to compute trend, up to maybe 20, but at least WindowSize). For trend analysis, you need enough history. To make it deterministic, maintain slice of last up to 20 checks (or WindowSize*2). But for average we already use WindowSize. For trend we need to split history into older half and newer half.
- For each check, store normalized values: LatencyMs (negative=>0), ReplicationLagMs (neg=>0), Connections (neg=>0), Success bool.

### HealthScore calculation

Start with 100.

Compute per-node aggregates over window (last WindowSize, or fewer if not enough history):
- `errorRate` = failed in window / windowLen (0 if windowLen 0)
- `avgLatency` = average latency in window
- `avgReplicationLag` = average replication lag in window
- `currentConnections` = last check's connections (or 0 if no checks)
- `consecutiveFailures` = as tracked in Step1
- `windowLen` = number of entries in window (min(total, WindowSize))

Deductions:
- **error_rate**: if errorRate>0 => deduction = errorRate * 50. Cap not specified, but can be up to 50. Factor["error_rate"] = deduction. If errorRate==0, factor 0.
- **latency**:
  if avgLatency > cfg.LatencyThresholdMs:
    over := (avgLatency - threshold)/threshold
    deduction := over * 20
    if deduction >30 { deduction=30 }
    Factor["latency"]=deduction
  else if avgLatency > threshold*0.8:
    Factor["latency"]=5
  else:
    Factor["latency"]=0
- **replication_lag**:
  if avgReplicationLag > cfg.ReplicationLagThresholdMs:
    over := (avg - threshold)/threshold
    deduction := over*15
    if deduction>20 { deduction=20 }
    Factor["replication_lag"]=deduction
  else if avgReplicationLag > threshold*0.8:
    Factor["replication_lag"]=3
  else:
    Factor["replication_lag"]=0
- **consecutive_failures**:
  deduction := float64(consecutiveFailures)*10
  if deduction>40 { deduction=40 }
  Factor["consecutive_failures"]=deduction
- **connections**:
  if currentConnections > cfg.ConnectionThreshold:
    Factor["connections"]=15
  else if currentConnections > int(float64(cfg.ConnectionThreshold)*0.8):
    Factor["connections"]=5
  else:
    Factor["connections"]=0

Score = 100 - sum(deductions). Clamped 0-100.
If windowLen==0 (no checks) Score=100? Actually if node exists but no checks? That cannot happen because node created only on check. But for safety, 100.

Factors map must contain all 5 keys above (even if 0).

LastUpdated = time.Now()

Trend overall:
Use GetTrend for metrics "latency", "error_rate", "replication_lag". If any of those is "degrading" => HealthScore.Trend = "degrading". Else if any is "improving" and none degrading => "improving". Else "stable".

### GetTrend implementation

Signature: GetTrend(nodeID, metric) string

- If node not found or history <4 => "stable"
- Metric cases:
  - "latency": use history latencies
  - "replication_lag": use replication lags
  - "connections": use connections
  - "error_rate": need per-window error? Simpler: use success bool converted to error (0 for success,1 for fail) and compute trend of error occurrence? Approach: take error values (1 for fail) history and compare averages.
- Algorithm generic:
  Take stored history slice (in order of occurrence, old to new). Let n = len(history). If n<4, stable.
  Split into two halves: older = first n/2, newer = last n/2 (if odd, middle element ignored or included? For determinism: older = history[:n/2], newer = history[n-n/2:] so both size floor(n/2). Example n=5 => older 2, newer 2 (middle ignored). n=4 => 2 and 2.
  Compute avg older vs avg newer.
  If metric is error_rate: avg is error rate (failed/total) in each half? Or average of error bool. Equivalent to fail count / halfLen.
  For other metrics: average value.
  Then:
    if newerAvg > olderAvg*1.1 => "degrading" (increase)
    if newerAvg < olderAvg*0.9 => "improving" (decrease)
    else "stable"
  Special for error_rate: same logic (increasing error = degrading)
  Edge when olderAvg ==0:
    - If newerAvg==0 => stable
    - If newerAvg>0 && olderAvg==0 => if newerAvg>0.1? Actually if older 0 and newer >0, consider degrading if newer>0. For latency: if older 0 and newer >0, degrading. For robustness: if older==0 and newer==0 => stable, else if older==0 and newer>0 => degrading, else if newer==0 and older>0 => improving.
- Unknown metric string => "stable"
- Return one of three strings exactly.

### PredictFailure

Returns FailurePrediction and bool (false if node not found).

- Compute HealthScore for node.
- Determine RiskLevel and Probability based on Score and ConsecutiveFailures and Trend:

Rules in priority order (first matching):
1. If ConsecutiveFailures >= DownThreshold => RiskCritical, Probability 0.95, Reason must include "consecutive failures" or "approaching failure" or "already down"
2. Else if Score <20 => RiskCritical, Probability 0.9
3. Else if Score <40 => RiskCritical, Probability 0.8
4. Else if Score <50 => RiskHigh, Probability 0.7
5. Else if Score <60 => RiskHigh, Probability 0.6
6. Else if Score <75:
   - if Trend=="degrading" => RiskMedium, Probability 0.4
   - else => RiskLow, Probability 0.2
7. Else (>=75):
   - if Trend=="degrading" => RiskMedium, Probability 0.35
   - else => RiskLow, Probability 0.1

- PredictedFailureWithin based on RiskLevel:
  Critical => 5 * time.Minute
  High => 15 * time.Minute
  Medium => 1 * time.Hour
  Low => 4 * time.Hour

- Reasons: slice of strings explaining issues. Build based on factors:
  - if errorRate > cfg.ErrorRateThreshold => add fmt.Sprintf("high error rate: %.1f%%", errorRate*100)
  - if avgLatency > cfg.LatencyThresholdMs => fmt.Sprintf("high latency: %.1fms > %.1fms", avgLatency, threshold)
  - if avgReplicationLag > cfg.ReplicationLagThresholdMs => "high replication lag: Xms"
  - if consecutiveFailures>0 => fmt.Sprintf("consecutive failures: %d", consecutiveFailures)
  - if currentConnections > int(float64(cfg.ConnectionThreshold)*0.8) => "high connection usage"
  - if GetTrend(node, "latency")=="degrading" => "degrading latency trend"
  - similarly for error_rate, replication_lag, connections if degrading
  - If no reasons (healthy node) => slice may contain "no significant issues" or be empty but tests expect at least 0? For healthy node, reasons can be empty or contain that string; we will accept either, but encourage at least one reason like "no significant issues" when low risk. To make tests pass, for low risk with no issues, Reasons can be empty or contain "no significant issues" – both accepted.
  For higher risk, at least one reason must be present.

- Timestamp = time.Now()

### GetAllHealthScores

Returns slice copy of HealthScore for all nodes, order sorted by NodeID? Not required but make deterministic sorted for easier testing.

### GetReliabilityReport

- Timestamp = time.Now()
- Nodes = GetAllHealthScores()
- OverallScore = average of all Nodes Score, if no nodes => 100
- ClusterHealth based on OverallScore:
  >=80 => "healthy"
  >=60 => "degraded"
  >=40 => "unhealthy"
  <40 => "critical"
- Predictions = for each node, call PredictFailure and collect (order same as Nodes)
- Recommendations: []string built from aggregated issues:
  Must include at least one recommendation. Generate based on:
  - if any node RiskCritical => add "immediate investigation required for nodes: <comma list of critical node ids>"
  - if any node RiskHigh => add "proactive maintenance recommended for high-risk nodes: <list>"
  - if any node Factors["latency"]>0 => add "consider scaling or optimizing queries for high latency nodes"
  - if any Factors["error_rate"]>0 => add "check application logs and database connectivity"
  - if any Factors["replication_lag"]>0 => add "check replica status and network"
  - if any Factors["connections"]>0 => add "consider increasing connection pool or investigating connection leaks"
  - if any Trend degrading => add "monitor degrading trends and plan capacity"
  - if overallScore >=80 and no issues => add "cluster operating normally"
  Order not strict but must contain substrings for relevant cases. Tests will check for presence of expected substrings.

  Ensure Recommendations non-empty.

### Concurrency

All new methods must be concurrency-safe same mutex as Step1.

### Backward compatibility

All Step1 methods must continue to work and produce same semantics.

### Constraints

- Stdlib only
- go vet ./... and go build ./... must pass
- Files must exist: reliability/monitor.go (or scoring.go)
- No panics

### Example

```go
m := reliability.NewMonitor(reliability.Config{})
m.RecordCheck(reliability.CheckResult{NodeID:"db1", LatencyMs:150, Success:true})
score, ok := m.GetHealthScore("db1")
pred, ok := m.PredictFailure("db1")
report := m.GetReliabilityReport()
trend := m.GetTrend("db1", "latency")
```

Implement scoring carefully to match spec algorithm exactly; tests will compute expected values following this spec with tolerance.
