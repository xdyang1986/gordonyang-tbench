#!/bin/bash
set -euo pipefail
cd /app
mkdir -p reliability
cat > go.mod <<'GOMOD'
module db-reliability
go 1.22
GOMOD
cat > reliability/monitor.go <<'GOCODE'
package reliability

import (
	"fmt"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

type Config struct {
	LatencyThresholdMs        float64
	ErrorRateThreshold        float64
	ReplicationLagThresholdMs float64
	ConnectionThreshold       int
	DownThreshold             int
	WindowSize                int
}

func (c Config) withDefaults() Config {
	if c.LatencyThresholdMs <= 0 { c.LatencyThresholdMs = 100 }
	if c.ErrorRateThreshold <= 0 { c.ErrorRateThreshold = 0.05 }
	if c.ReplicationLagThresholdMs <= 0 { c.ReplicationLagThresholdMs = 500 }
	if c.ConnectionThreshold <= 0 { c.ConnectionThreshold = 100 }
	if c.DownThreshold <= 0 { c.DownThreshold = 3 }
	if c.WindowSize <= 0 { c.WindowSize = 10 }
	return c
}

type CheckResult struct {
	NodeID           string
	Timestamp        time.Time
	LatencyMs        float64
	Success          bool
	ErrorType        string
	ReplicationLagMs float64
	Connections      int
}

type NodeStatus struct {
	NodeID               string
	IsHealthy            bool
	ConsecutiveFailures  int
	TotalChecks          int
	FailedChecks         int
	AvgLatencyMs         float64
	LastCheck            time.Time
	CurrentConnections   int
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
	Value     float64
}

type checkRecord struct {
	Timestamp        time.Time
	LatencyMs        float64
	Success          bool
	ReplicationLagMs float64
	Connections      int
}

type nodeData struct {
	nodeID                string
	consecutiveFailures   int
	totalChecks           int
	failedChecks          int
	history               []checkRecord
	lastCheck             time.Time
	currentConnections    int
	lastReplicationLag    float64
	highLatencySuppressed bool
}

type Monitor struct {
	cfg          Config
	mu           sync.RWMutex
	nodes        map[string]*nodeData
	alerts       []Alert
	alertCounter uint64
}

func NewMonitor(cfg Config) *Monitor {
	cfg = cfg.withDefaults()
	return &Monitor{cfg: cfg, nodes: make(map[string]*nodeData)}
}

func (m *Monitor) RecordCheck(result CheckResult) []Alert {
	if result.NodeID == "" {
		return []Alert{}
	}
	latency := result.LatencyMs
	if latency < 0 { latency = 0 }
	repLag := result.ReplicationLagMs
	if repLag < 0 { repLag = 0 }
	conns := result.Connections
	if conns < 0 { conns = 0 }
	ts := result.Timestamp
	if ts.IsZero() { ts = time.Now() }
	m.mu.Lock()
	defer m.mu.Unlock()
	nd, ok := m.nodes[result.NodeID]
	if !ok {
		nd = &nodeData{nodeID: result.NodeID}
		m.nodes[result.NodeID] = nd
	}
	nd.totalChecks++
	if !result.Success {
		nd.failedChecks++
		nd.consecutiveFailures++
	} else {
		nd.consecutiveFailures = 0
	}
	nd.lastCheck = ts
	nd.currentConnections = conns
	nd.lastReplicationLag = repLag
	rec := checkRecord{Timestamp: ts, LatencyMs: latency, Success: result.Success, ReplicationLagMs: repLag, Connections: conns}
	nd.history = append(nd.history, rec)
	maxHist := m.cfg.WindowSize * 2
	if maxHist < 20 { maxHist = 20 }
	if len(nd.history) > maxHist {
		nd.history = nd.history[len(nd.history)-maxHist:]
	}
	var timeWindow []checkRecord
	cutoff := ts.Add(-60 * time.Second)
	for _, r := range nd.history {
		if !r.Timestamp.Before(cutoff) {
			timeWindow = append(timeWindow, r)
		}
	}
	failedInTimeWindow := 0
	for _, r := range timeWindow {
		if !r.Success { failedInTimeWindow++ }
	}
	errorRate := 0.0
	if len(timeWindow) > 0 {
		errorRate = float64(failedInTimeWindow) / float64(len(timeWindow))
	}
	var alerts []Alert
	if nd.consecutiveFailures == m.cfg.DownThreshold {
		id := fmt.Sprintf("%s-%s-%d", result.NodeID, AlertNodeDown, atomic.AddUint64(&m.alertCounter, 1))
		alert := Alert{ID: id, NodeID: result.NodeID, Type: AlertNodeDown, Severity: SeverityCritical, Timestamp: ts, Message: fmt.Sprintf("Node %s is down: %d consecutive failures", result.NodeID, nd.consecutiveFailures), Value: float64(nd.consecutiveFailures)}
		m.alerts = append(m.alerts, alert)
		alerts = append(alerts, alert)
	}
	if latency > m.cfg.LatencyThresholdMs {
		if !nd.highLatencySuppressed {
			id := fmt.Sprintf("%s-%s-%d", result.NodeID, AlertHighLatency, atomic.AddUint64(&m.alertCounter, 1))
			alert := Alert{ID: id, NodeID: result.NodeID, Type: AlertHighLatency, Severity: SeverityWarning, Timestamp: ts, Message: fmt.Sprintf("High latency on node %s: %.2fms > %.2fms", result.NodeID, latency, m.cfg.LatencyThresholdMs), Value: latency}
			m.alerts = append(m.alerts, alert)
			alerts = append(alerts, alert)
			nd.highLatencySuppressed = true
		}
	} else {
		if latency < m.cfg.LatencyThresholdMs*0.8 {
			nd.highLatencySuppressed = false
		}
	}
	if repLag > m.cfg.ReplicationLagThresholdMs {
		id := fmt.Sprintf("%s-%s-%d", result.NodeID, AlertReplicationLag, atomic.AddUint64(&m.alertCounter, 1))
		alert := Alert{ID: id, NodeID: result.NodeID, Type: AlertReplicationLag, Severity: SeverityWarning, Timestamp: ts, Message: fmt.Sprintf("High replication lag on node %s: %.2fms > %.2fms", result.NodeID, repLag, m.cfg.ReplicationLagThresholdMs), Value: repLag}
		m.alerts = append(m.alerts, alert)
		alerts = append(alerts, alert)
	}
	if conns > m.cfg.ConnectionThreshold {
		id := fmt.Sprintf("%s-%s-%d", result.NodeID, AlertConnectionExhaustion, atomic.AddUint64(&m.alertCounter, 1))
		alert := Alert{ID: id, NodeID: result.NodeID, Type: AlertConnectionExhaustion, Severity: SeverityWarning, Timestamp: ts, Message: fmt.Sprintf("Connection exhaustion on node %s: %d > %d", result.NodeID, conns, m.cfg.ConnectionThreshold), Value: float64(conns)}
		m.alerts = append(m.alerts, alert)
		alerts = append(alerts, alert)
	}
	if len(timeWindow) >= 3 && errorRate > m.cfg.ErrorRateThreshold {
		id := fmt.Sprintf("%s-%s-%d", result.NodeID, AlertHighErrorRate, atomic.AddUint64(&m.alertCounter, 1))
		alert := Alert{ID: id, NodeID: result.NodeID, Type: AlertHighErrorRate, Severity: SeverityWarning, Timestamp: ts, Message: fmt.Sprintf("High error rate on node %s: %.2f%% > %.2f%%", result.NodeID, errorRate*100, m.cfg.ErrorRateThreshold*100), Value: errorRate}
		m.alerts = append(m.alerts, alert)
		alerts = append(alerts, alert)
	}
	return alerts
}

func (m *Monitor) computeNodeStatusLocked(nd *nodeData) NodeStatus {
	windowSize := m.cfg.WindowSize
	histLen := len(nd.history)
	startIdx := 0
	if histLen > windowSize { startIdx = histLen - windowSize }
	window := nd.history[startIdx:]
	var sumLat float64
	for _, r := range window { sumLat += r.LatencyMs }
	avgLat := 0.0
	if len(window) > 0 { avgLat = sumLat / float64(len(window)) }
	isHealthy := nd.consecutiveFailures < m.cfg.DownThreshold
	return NodeStatus{NodeID: nd.nodeID, IsHealthy: isHealthy, ConsecutiveFailures: nd.consecutiveFailures, TotalChecks: nd.totalChecks, FailedChecks: nd.failedChecks, AvgLatencyMs: avgLat, LastCheck: nd.lastCheck, CurrentConnections: nd.currentConnections, LastReplicationLagMs: nd.lastReplicationLag}
}

func (m *Monitor) GetNodeStatus(nodeID string) (NodeStatus, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]
	if !ok { return NodeStatus{}, false }
	return m.computeNodeStatusLocked(nd), true
}
func (m *Monitor) GetAllNodes() []NodeStatus {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]NodeStatus, 0, len(m.nodes))
	for _, nd := range m.nodes {
		out = append(out, m.computeNodeStatusLocked(nd))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].NodeID < out[j].NodeID })
	copied := make([]NodeStatus, len(out))
	copy(copied, out)
	return copied
}
func (m *Monitor) GetAlerts() []Alert {
	m.mu.RLock()
	defer m.mu.RUnlock()
	copied := make([]Alert, len(m.alerts))
	copy(copied, m.alerts)
	return copied
}
func (m *Monitor) GetAlertsByNode(nodeID string) []Alert {
	m.mu.RLock()
	defer m.mu.RUnlock()
	var out []Alert
	for _, a := range m.alerts {
		if a.NodeID == nodeID { out = append(out, a) }
	}
	copied := make([]Alert, len(out))
	copy(copied, out)
	return copied
}
func (m *Monitor) IsHealthy(nodeID string) bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]
	if !ok { return false }
	return nd.consecutiveFailures < m.cfg.DownThreshold
}
func (m *Monitor) Reset(nodeID string) {
	if nodeID == "" { return }
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.nodes, nodeID)
}
func (m *Monitor) GetUptimePercentage(nodeID string) float64 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]
	if !ok || nd.totalChecks == 0 { return 100.0 }
	uptime := float64(nd.totalChecks-nd.failedChecks) / float64(nd.totalChecks) * 100.0
	if uptime < 0 { uptime = 0 }
	if uptime > 100 { uptime = 100 }
	return uptime
}
GOCODE
go mod tidy
go vet ./... || true
echo "solution applied step1 minimal"
