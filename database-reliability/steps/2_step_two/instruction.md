# Database Reliability Monitoring — Step 2: Proactive Scoring

Extends Step1 reactive monitor. Keep backward compat: Step1 tests still pass.

## Layout
```
/app/
  go.mod (module db-reliability)
  reliability/
    monitor.go
    scoring.go (optional)
```
Import `db-reliability/reliability`.

## Types (exact – same as before plus extras)
```go
type HealthScore struct {
    NodeID string; Score float64; Factors map[string]float64; LastUpdated time.Time; Trend string
}
type RiskLevel string
const (RiskLow RiskLevel="low"; RiskMedium RiskLevel="medium"; RiskHigh RiskLevel="high"; RiskCritical RiskLevel="critical")
type FailurePrediction struct {
    NodeID string; RiskLevel RiskLevel; Probability float64; PredictedFailureWithin time.Duration; Reasons []string; Timestamp time.Time
}
type ReliabilityReport struct {
    Timestamp time.Time; OverallScore float64; Nodes []HealthScore; Predictions []FailurePrediction; ClusterHealth string; Recommendations []string
}
func (m *Monitor) GetHealthScore(nodeID string) (HealthScore, bool)
func (m *Monitor) GetAllHealthScores() []HealthScore
func (m *Monitor) PredictFailure(nodeID string) (FailurePrediction, bool)
func (m *Monitor) GetReliabilityReport() ReliabilityReport
func (m *Monitor) GetTrend(nodeID string, metric string) string
```

Metric for GetTrend: "latency", "error_rate", "replication_lag", "connections". Return "stable" if unknown metric/node or history<4.

## Proactive rules

### Data
Per node history up to max(WindowSize*2,20). Store normalized latency, lag, connections, success.

- AvgLatency for status and scoring: count window last WindowSize.
- ErrorRate for alerts and scoring: time window last 60s, defined as checks where Timestamp >= currentTimestamp-60s, including current check. Count failed/total in that window.
- CurrentConnections = last check's conns.

### HealthScore

Invariant: Score = 100 - sum(independent per-factor penalties). Each penalty saturates at a cap. Factors map must contain all 5 keys: error_rate, latency, replication_lag, consecutive_failures, connections (even if 0). LastUpdated=Now. Trend overall: if any of latency/error_rate/replication_lag is degrading → degrading, else if any improving and none degrading → improving, else stable.

Score clamped 0-100. Factors map must contain all 5 keys even if deduction 0. Copy-safety: mutating returned Factors map must not affect internal monitor state; GetAllHealthScores must return deep copies.

**Derive penalties from combined observations below. Examples use defaults: LatencyThreshold=100, LagThreshold=500, ConnThreshold=100, DownThreshold=3, WindowSize=10, ErrorThreshold=0.05. Baseline others zero (errorRate 0, latency 50, lag 100, consec 0, conn 10) → Score 100.**

Combined observations – each row is (errorRate, avgLatency, avgLag, consec, conn) → Score (errorRate includes current check within 60s window):

1. (0.2, 120, 600, 0, 10) → 83
2. (0.2, 120, 600, 1, 90) → 68
3. (0.5, 250, 100, 2, 70) → 25
4. (0.2, 85, 600, 1, 150) → 57
5. (0.1, 150, 1000, 0, 90) → 65
6. (0.4, 50, 100, 4, 10) → 40
7. (0.5, 200, 2000, 1, 90) → 20
8. (1.0, 250, 2000, 4, 150) → 0 (clamped, sum exceeds 100)

Interpretation: errorRate contributes proportionally, latency/lag have low-watermark and over-threshold scaling with caps, consec scales with cap, connections has two tiers. Infer exact per-factor functions from these rows – they fully determine the model.

### GetTrend

- history <4 → stable, unknown metric/node → stable
- Metric: latency (history lat), replication_lag (lag), connections (conns), error_rate (fail bool as 1/0)
- GetTrend must skip checks recorded while node was down (consec >= DownThreshold), since outage skews halves.
- Split filtered history into older = first n/2, newer = last n/2 (middle ignored when odd). Compute avg older vs newer.
- If newer==0 && older==0 → stable; older==0 && newer>0 → degrading; newer==0 && older>0 → improving; else newer > older*1.1 → degrading, newer < older*0.9 → improving, else stable.

Worked:
```
latency history [10,20,30,40,50,60,70,80] → older avg (10+20+30+40)/4=25, newer avg (50+60+70+80)/4=65 → 65>25*1.1 → degrading
latency [80,70,60,50,40,30,20,10] → improving
With outage: fails make node down, those checks skipped in trend
```

### PredictFailure

Returns bool false if node missing. Compute HealthScore then:

Priority order:
1. consec >= DownThreshold → critical 0.95, within 5m
2. score <20 → critical 0.9, within 5m
3. score <40 → critical 0.8, within 5m
4. score <50 → high 0.7, within 15m
5. score <60 → high 0.6, within 15m
6. score <75: degrading→medium 0.4 within 1h else low 0.2 within 4h
7. >=75: degrading→medium 0.35 within 1h else low 0.1 within 4h

Within mapping: critical 5m, high 15m, medium 1h, low 4h. Probabilities and durations are exact per band as listed above and are tested.

Reasons: built from time-window errorRate, count-window avgLatency/avgLag, consec, conn>0.8*thresh, and degrading trends. If low risk and no issues → ["no significant issues"] or empty allowed; high/medium/critical must have ≥1 reason.

Timestamp Now. Predictions sorted by NodeID for determinism.

### GetAllHealthScores

Copy, sorted by NodeID. Factors maps deep-copied.

### GetReliabilityReport

- Timestamp Now
- Nodes = snapshot of all health scores derived from one atomic point-in-time view
- OverallScore = avg Nodes Score, 100 if none
- ClusterHealth: >=80 healthy, >=60 degraded, >=40 unhealthy, <40 critical
- Predictions = per node PredictFailure derived from same snapshot as Nodes, sorted by NodeID, mutually consistent with Nodes (same underlying data)
- Snapshot-consistency requirement: Nodes and Predictions in a single report must derive from one atomic snapshot holding the monitor lock once. Implementation that calls GetAllHealthScores() then loops PredictFailure() with separate locks can produce a torn report where a concurrent RecordCheck changes state between calls. To pass concurrency tests, report must be built under a single lock acquisition copying needed state once.
- Recommendations: at least one, containing substrings:
  - any critical → "immediate investigation required for nodes: <list>"
  - any high → "proactive maintenance recommended for high-risk nodes: <list>"
  - any latency factor>0 → "consider scaling or optimizing queries for high latency nodes"
  - any error_rate>0 → "check application logs and database connectivity"
  - any repl_lag>0 → "check replica status and network"
  - any conn>0 → "consider increasing connection pool or investigating connection leaks"
  - any degrading → "monitor degrading trends and plan capacity"
  - if healthy (overall>=80 no factors) → "cluster operating normally"

Order not strict but substrings checked – all 8 are tested in various scenarios.

### Concurrency

All methods RWMutex, race detector. Report snapshot test runs concurrent RecordCheck during GetReliabilityReport.

### Backward compat

Step1 hysteresis and time-window must stay. ErrorRate window definition includes current check (timestamp >= cur-60s inclusive).

### Constraints

Stdlib only, vet pass, no panics.
