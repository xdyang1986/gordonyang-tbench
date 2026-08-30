# Database Reliability Monitoring — Step 1: Reactive Monitoring

Build a database reliability monitoring system in Go that reactively detects failures and emits alerts.

## Package layout expected

```
/app/
  go.mod (module db-reliability)
  reliability/
    monitor.go
    types.go (optional, if you want to split)
```

Public API must be in package `reliability`. Tests import `db-reliability/reliability`.

Module name must be `db-reliability` (`go mod init db-reliability`).

## Types (must match exactly)

```go
package reliability

import "time"

type Config struct {
    LatencyThresholdMs          float64 // default 100 ms
    ErrorRateThreshold          float64 // default 0.05 (5%)
    ReplicationLagThresholdMs   float64 // default 500 ms
    ConnectionThreshold         int     // default 100
    DownThreshold               int     // default 3 consecutive failures
    WindowSize                  int     // default 10, sliding window for avg/errorRate
}

type CheckResult struct {
    NodeID            string
    Timestamp         time.Time
    LatencyMs         float64
    Success           bool
    ErrorType         string
    ReplicationLagMs  float64
    Connections       int
}

type NodeStatus struct {
    NodeID              string
    IsHealthy           bool
    ConsecutiveFailures int
    TotalChecks         int
    FailedChecks        int
    AvgLatencyMs        float64
    LastCheck           time.Time
    CurrentConnections  int
    LastReplicationLagMs float64
}

type AlertType string
const (
    AlertNodeDown              AlertType = "node_down"
    AlertHighLatency           AlertType = "high_latency"
    AlertHighErrorRate         AlertType = "high_error_rate"
    AlertReplicationLag        AlertType = "replication_lag"
    AlertConnectionExhaustion  AlertType = "connection_exhaustion"
)

type AlertSeverity string
const (
    SeverityCritical AlertSeverity = "critical"
    SeverityWarning  AlertSeverity = "warning"
    SeverityInfo     AlertSeverity = "info"
)

type Alert struct {
    ID        string
    NodeID    string
    Type      AlertType
    Severity  AlertSeverity
    Timestamp time.Time
    Message   string
    Value     float64 // relevant metric value that triggered alert
}

type Monitor struct { /* unexported fields */ }

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

## Config defaults

If a field is zero or negative, use default:
- LatencyThresholdMs: 100
- ErrorRateThreshold: 0.05
- ReplicationLagThresholdMs: 500
- ConnectionThreshold: 100
- DownThreshold: 3
- WindowSize: 10

Config may be passed zero-valued to trigger defaults. Negative values also fallback to defaults.

## RecordCheck detailed semantics

- If NodeID == "" => no-op, return empty slice, do not create node, do not store alert, do not panic.
- If Timestamp.IsZero() => use time.Now() for LastCheck and Alert Timestamp.
- LatencyMs <0 treated as 0 for purpose of avg and high-latency check (no alert if negative).
- ReplicationLagMs <0 treated as 0 for avg and threshold check.
- Connections <0 treated as 0.
- Success == false: increment consecutive failures, increment FailedChecks, TotalChecks.
- Success == true: set consecutiveFailures=0, IsHealthy true, increment TotalChecks only.
- Maintain per-node history up to WindowSize most recent checks (for sliding calculations). Keep circular buffer or slice with max WindowSize.
  - Error rate = failed checks in current window / window length (window length = min(TotalChecks (or history len), WindowSize) but actually number of entries in window buffer).
  - AvgLatencyMs = average of LatencyMs values in window (with negative treated as 0).
  - For NodeStatus: AvgLatencyMs is window avg, LastCheck = result Timestamp (or now if zero), CurrentConnections = normalized connections, LastReplicationLagMs = normalized replication lag (negative=>0).
  - TotalChecks is overall count since creation/reset, not just window.
  - FailedChecks overall count.
- Alerts generation order must be deterministic: check in order:
  1. NodeDown
  2. HighLatency
  3. ReplicationLag
  4. ConnectionExhaustion
  5. HighErrorRate
  Return slice in that order if multiple triggered by same check.

  - **NodeDown**: if after this check, ConsecutiveFailures == DownThreshold (transition to down, not every tick after). Emit once per outage. Severity critical, Type node_down, Value = float64(ConsecutiveFailures), Message non-empty containing node id and "down" substring case-insensitive.
    IsHealthy becomes false when ConsecutiveFailures >= DownThreshold. Any success after resets to healthy.
  - **HighLatency**: if normalized LatencyMs > LatencyThresholdMs, emit high_latency warning, Value = LatencyMs.
  - **ReplicationLag**: if normalized ReplicationLagMs > ReplicationLagThresholdMs, emit replication_lag warning, Value = ReplicationLagMs.
  - **ConnectionExhaustion**: if normalized Connections > ConnectionThreshold, emit connection_exhaustion warning, Value = float64(Connections).
  - **HighErrorRate**: compute errorRate over window (as defined). If window length >=3 and errorRate > ErrorRateThreshold, emit high_error_rate warning, Value = errorRate (0-1). Note: window length must be at least 3 to avoid flapping on tiny windows; if history len <3, do NOT emit high_error_rate even if rate > threshold. This prevents early false positives. Also, if errorRate exactly equals threshold, no alert (requires >).
- Alert ID: must be non-empty unique string per alert. E.g., fmt.Sprintf("%s-%s-%d", nodeID, alertType, atomicCounter) or using timestamp. Uniqueness required: two alerts must not share same ID even if generated in same nanosecond? Use counter or uuid style.
- Alert Timestamp: use result.Timestamp if non-zero else time.Now(). Must be non-zero.
- Alert Message: non-empty descriptive string.
- All methods must copy data to avoid external mutation: GetNodeStatus returns copy, GetAllNodes returns copy slice (not sharing underlying), GetAlerts returns copy.

## Other methods

- **GetNodeStatus(nodeID)**: if node exists, returns status and true. Status IsHealthy = consecutiveFailures < DownThreshold. ConsecutiveFailures, TotalChecks, FailedChecks as stored. AvgLatency computed over window. If not exists, return zero NodeStatus, false.
- **GetAllNodes()**: return slice copy of all NodeStatus, order unspecified but deterministic (sorted by NodeID recommended but not required for grading, except we test existence).
- **GetAlerts()**: return copy of all alerts ever emitted in order of emission. Must not expose internal slice directly.
- **GetAlertsByNode(nodeID)**: filter alerts by nodeID, preserve order, return copy.
- **IsHealthy(nodeID)**: if node not found, return false. Else true if consecutiveFailures < DownThreshold.
- **Reset(nodeID)**: if NodeID empty, no-op. If node exists, clear its history, stats, remove from map, remove? Reset means delete node entry: after reset, GetNodeStatus should return false, GetAllNodes should not include it, but GetAlerts still retains historical alerts? Spec: Reset clears node stats but does NOT clear alerts history. Historical alerts remain in GetAlerts. After reset, new checks for same NodeID start fresh (TotalChecks from 0).
- **GetUptimePercentage(nodeID)**: if node not found or TotalChecks==0, return 100.0. Else (Total-Failed)/Total *100. Clamped 0-100.

## Concurrency

All Monitor methods must be concurrency-safe. Use sync.RWMutex or equivalent. Tests run with `go run -race` with 100 goroutines performing RecordCheck concurrently and concurrent readers of GetNodeStatus/GetAlerts.

## Constraints

- Stdlib only, no external dependencies. `go.mod` should have no require outside stdlib.
- `go vet ./...` and `go build ./...` must pass.
- File `reliability/monitor.go` must exist.
- No panics on empty/negative/zero inputs.
- Deterministic alert ordering as defined.

## Grading

Binary pass/fail.

Implement full reactive monitor in this step; do NOT yet implement scoring/proactive features (they will be added in step 2).

## Example usage

```go
cfg := reliability.Config{
  LatencyThresholdMs: 100,
  ErrorRateThreshold: 0.05,
  ReplicationLagThresholdMs: 500,
  ConnectionThreshold: 100,
  DownThreshold: 3,
  WindowSize: 10,
}
m := reliability.NewMonitor(cfg)
alerts := m.RecordCheck(reliability.CheckResult{
  NodeID: "db-primary-1",
  LatencyMs: 150,
  Success: true,
  ReplicationLagMs: 200,
  Connections: 80,
})
// alerts should contain high_latency if 150 > 100
status, ok := m.GetNodeStatus("db-primary-1")
```
