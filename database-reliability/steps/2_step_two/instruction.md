# Database Reliability Monitoring — Step 2: Proactive Scoring (Hardened v2)

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

## Proactive rules – hardened

### Data
Per node history up to max(WindowSize*2,20). Store normalized latency, lag, connections, success.

- AvgLatency for status and scoring: **count window** last WindowSize.
- ErrorRate for alerts and scoring: **time window** last 60s (ts >= cur-60s). Mixed semantics – models unify.
- CurrentConnections = last check's conns.

### HealthScore

Invariant: Score = 100 - sum(independent per-factor penalties). Each penalty saturates. Factors map must contain all 5 keys: error_rate, latency, replication_lag, consecutive_failures, connections (even if 0). LastUpdated=Now. Trend overall: if any of latency/error_rate/replication_lag is degrading → degrading, else if any improving and none degrading → improving, else stable.

**Instead of formulas, derive penalties from worked examples below. Examples use defaults: LatencyThreshold=100, LagThreshold=500, ConnThreshold=100, DownThreshold=3, WindowSize=10, ErrorThreshold=0.05.**

Single-factor examples (others zero, consec 0, conn 10, latency 50, lag 100, errorRate 0 → Score 100):

- errorRate 0.2 → Score 90
- errorRate 0.5 → Score 75
- errorRate 1.0 → Score 50
- avgLatency 120 → Score 96
- avgLatency 150 → Score 90
- avgLatency 250 → Score 70
- avgLatency 85 → Score 95
- avgLag 600 → Score 97
- avgLag 1000 → Score 85
- avgLag 2000 → Score 80
- consec 1 → Score 90
- consec 2 → Score 80
- consec 4 → Score 60
- consec 5 → Score 60
- conn 150 → Score 85
- conn 90 → Score 95
- conn 70 → Score 100

Combined examples:
- errorRate 0.2 (deduction 10) + avgLatency 120 (4) + avgLag 600 (3) + consec 1 (10) + conn 90 (5) = deductions 32 → Score 68
- errorRate 0.0 + avgLatency 50 + avgLag 100 + consec 0 + conn 10 → Score 100
- errorRate 0.5 (25) + avgLatency 250 (30 cap) + avgLag 2000 (20 cap) + consec 4 (40 cap) + conn 150 (15) =130 → Score clamped 0

From these, infer:
- error_rate penalty = errorRate*50
- latency: if avg > threshold: over=(avg-thresh)/thresh, penalty=over*20 cap 30; else if avg > thresh*0.8 penalty 5 else 0
- lag: if avg > thresh: over*15 cap20 else if >0.8*thresh 3 else 0
- consec: *10 cap40
- conn: > thresh 15 else >0.8*thresh 5 else 0

Score clamped 0-100.

### GetTrend – with outage exclusion (prior-violating)

- history <4 → stable, unknown metric/node → stable
- Metric: latency (history lat), replication_lag (lag), connections (conns), error_rate (fail bool as 1/0)
- **Outage exclusion:** GetTrend must skip checks recorded while node was down (consec >= DownThreshold), since outage skews halves.
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
1. consec >= DownThreshold → critical 0.95
2. score <20 → critical 0.9
3. score <40 → critical 0.8
4. score <50 → high 0.7
5. score <60 → high 0.6
6. score <75: degrading→medium 0.4 else low 0.2
7. >=75: degrading→medium 0.35 else low 0.1

Within: critical 5m, high 15m, medium 1h, low 4h.

Reasons: built from time-window errorRate, count-window avgLatency/avgLag, consec, conn>0.8*thresh, and degrading trends. If low risk and no issues → ["no significant issues"] or empty allowed; high/medium/critical must have ≥1 reason.

Timestamp Now.

### GetAllHealthScores

Copy, sorted by NodeID.

### GetReliabilityReport

- Timestamp Now
- Nodes = GetAllHealthScores()
- OverallScore = avg Nodes Score, 100 if none
- ClusterHealth: >=80 healthy, >=60 degraded, >=40 unhealthy, <40 critical
- Predictions = per node PredictFailure sorted
- Recommendations: at least one, containing substrings:
  - any critical → "immediate investigation required for nodes: <list>"
  - any high → "proactive maintenance recommended for high-risk nodes: <list>"
  - any latency factor>0 → "consider scaling or optimizing queries for high latency nodes"
  - any error_rate>0 → "check application logs and database connectivity"
  - any repl_lag>0 → "check replica status and network"
  - any conn>0 → "consider increasing connection pool or investigating connection leaks"
  - any degrading → "monitor degrading trends and plan capacity"
  - if healthy (overall>=80 no factors) → "cluster operating normally"

Order not strict but substrings checked.

### Concurrency

All methods RWMutex, race detector.

### Backward compat

Step1 hysteresis and time-window must stay.

### Constraints

Stdlib only, vet pass, no panics.

Implement scoring by inferring penalties from examples; don't hardcode example table, infer rule.
