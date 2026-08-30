# Database Reliability Monitoring — Step 1: Reactive Monitoring (Hardened v2)

Build reactive monitoring. Terse spec, prior-violating rules, examples drive inference.

## Layout
```
/app/
  go.mod (module db-reliability)
  reliability/
    monitor.go
```
Import `db-reliability/reliability`.

## Types (exact)
```go
type Config struct {
    LatencyThresholdMs float64 // default 100
    ErrorRateThreshold float64 // default 0.05
    ReplicationLagThresholdMs float64 // default 500
    ConnectionThreshold int // default 100
    DownThreshold int // default 3
    WindowSize int // default 10
}
type CheckResult struct {
    NodeID string; Timestamp time.Time; LatencyMs float64; Success bool; ErrorType string; ReplicationLagMs float64; Connections int
}
type NodeStatus struct {
    NodeID string; IsHealthy bool; ConsecutiveFailures int; TotalChecks int; FailedChecks int; AvgLatencyMs float64; LastCheck time.Time; CurrentConnections int; LastReplicationLagMs float64
}
type AlertType string
const (
    AlertNodeDown AlertType = "node_down"
    AlertHighLatency AlertType = "high_latency"
    AlertHighErrorRate AlertType = "high_error_rate"
    AlertReplicationLag AlertType = "replication_lag"
    AlertConnectionExhaustion AlertType = "connection_exhaustion"
)
type AlertSeverity string
const (SeverityCritical AlertSeverity = "critical"; SeverityWarning AlertSeverity = "warning"; SeverityInfo AlertSeverity = "info")
type Alert struct { ID string; NodeID string; Type AlertType; Severity AlertSeverity; Timestamp time.Time; Message string; Value float64 }
type Monitor struct { }

func NewMonitor(cfg Config) *Monitor
func (m *Monitor) RecordCheck(result CheckResult) []Alert
func (m *Monitor) GetNodeStatus(nodeID string) (NodeStatus, bool)
func (m *Monitor) GetAllNodes() []NodeStatus
func (m *Monitor) GetAlerts() []Alert
func (m *Monitor) GetAlertsByNode(nodeID string) []Alert
func (m *Monitor) IsHealthy(nodeID string) bool
func (m *Monitor) Reset(nodeID string)
func (m *Monitor) GetUptimePercentage(nodeID string) float64
```

Config: zero/negative → default (100, 0.05, 500, 100, 3, 10).

## RecordCheck – hardened rules

- NodeID=="" → no-op, empty slice, no state.
- Timestamp zero → time.Now() for LastCheck and alert Timestamp.
- Latency<0, ReplLag<0, Conn<0 → 0 for avg and checks.
- Success false: consec++, failed++, total++; Success true: consec=0, healthy, total++.
- History per node up to max(WindowSize*2,20) for trend future, but:
  - **Mixed window semantics (prior-violating):** errorRate over **time window** last 60s of timestamps, avgLatency over **count window** last WindowSize. Models unify them – don't.

- **Alert order deterministic:** NodeDown, HighLatency, ReplicationLag, ConnectionExhaustion, HighErrorRate.

- **NodeDown:** when consec == DownThreshold (transition only). Critical, Value=consec, Message contains "down" case-insensitive. IsHealthy = consec < DownThreshold. Once down, further fails don't re-alert until recovered (success resets).

- **HighLatency with hysteresis (prior-violating):** after high_latency fires for a node, suppress further high_latency alerts until metric drops **below 80%** of threshold. Reset only when latency < threshold*0.8. Suppression is per-node, survives other alerts, cleared on Reset. Terse.

- **ReplicationLag:** if repLag > ReplicationLagThreshold → warning
- **ConnectionExhaustion:** if conns > ConnectionThreshold → warning
- **HighErrorRate:** errorRate over **time window** last 60s (checks with Timestamp >= currentTimestamp-60s). If len(timeWindow)>=3 and errorRate > ErrorRateThreshold → warning, Value=errorRate. No alert if window <3. No alert if exactly equals threshold.

- Alert ID non-empty unique (counter), Timestamp = result.Timestamp if non-zero else Now, Message non-empty, copy semantics for getters.

### Worked examples – infer rules from these

**ErrorRate time window:**
```
Threshold 0.5, timeWindow 60s, current ts=100s
History:
 ts=30 success → outside (100-60=40) excluded
 ts=50 fail → inside
 ts=70 fail → inside
 ts=90 success → inside
Window total 3, failed 2 → errorRate 0.666 → alert high_error_rate
```
```
ts=90 success, ts=95 success, current ts=100 → total 2 (<3) → NO high_error_rate even if previous fails outside window were many
```

**AvgLatency count window:**
```
WindowSize=3, history latencies [10,20,30] → Avg 20
Add 40 → history [10,20,30,40] window last 3 = [20,30,40] → Avg 30
ErrorRate example above used time window, not count window – different!
```

**Hysteresis:**
```
Threshold 100, 80% =80
Check latency 120 → [high_latency] (fires, suppress on)
Check 110 → [] (suppressed, 110>100 but still suppressed)
Check 90 → [] (90<100 but >80, no reset, still suppressed)
Check 70 → [] (70<80, reset suppression, no alert)
Check 110 → [high_latency] (fires again after reset)
```
If node resets, hysteresis cleared.

**Multiple alerts order:**
```
DownThreshold=1, latency 200, replLag 600, conn 150, success=false
→ [node_down, high_latency, replication_lag, connection_exhaustion] in that order
```

**NodeDown transition:**
```
DownThreshold=3, fails 1,2 → healthy, no node_down
Fail 3 → [node_down], unhealthy
Fail 4 → [] (no repeat)
Success 1 → healthy, consec 0
Fails 3 again → [node_down] again
```

## Other methods

- GetNodeStatus: IsHealthy = consec < DownThreshold, AvgLatency = avg over **count window** last WindowSize, other fields as stored, false if missing.
- GetAllNodes: copy, sorted by NodeID for determinism.
- GetAlerts, GetAlertsByNode: copy, order emission.
- IsHealthy: false if missing else consec<DownThreshold
- Reset: delete node entry, clears history and hysteresis, does NOT clear alerts. Empty NodeID → no-op.
- GetUptimePercentage: 100 if missing/total 0 else (total-failed)/total*100 clamped.

## Concurrency

Thread-safe RWMutex. Tests include `go run -race` 100 goroutines.

## Constraints

Stdlib only, `go vet ./...` pass, file `monitor.go` exists, no panics.

Implement reactive monitor with time-window errorRate and hysteresis; scoring is step2.
