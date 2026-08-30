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
	AlertNodeDown AlertType = "node_down"
	AlertHighLatency AlertType = "high_latency"
	AlertHighErrorRate AlertType = "high_error_rate"
	AlertReplicationLag AlertType = "replication_lag"
	AlertConnectionExhaustion AlertType = "connection_exhaustion"
)

type AlertSeverity string
const (
	SeverityCritical AlertSeverity = "critical"
	SeverityWarning AlertSeverity = "warning"
	SeverityInfo AlertSeverity = "info"
)

type Alert struct {
	ID string
	NodeID string
	Type AlertType
	Severity AlertSeverity
	Timestamp time.Time
	Message string
	Value float64
}

type HealthScore struct {
	NodeID string
	Score float64
	Factors map[string]float64
	LastUpdated time.Time
	Trend string
}

type RiskLevel string
const (
	RiskLow RiskLevel = "low"
	RiskMedium RiskLevel = "medium"
	RiskHigh RiskLevel = "high"
	RiskCritical RiskLevel = "critical"
)

type FailurePrediction struct {
	NodeID string
	RiskLevel RiskLevel
	Probability float64
	PredictedFailureWithin time.Duration
	Reasons []string
	Timestamp time.Time
}

type ReliabilityReport struct {
	Timestamp time.Time
	OverallScore float64
	Nodes []HealthScore
	Predictions []FailurePrediction
	ClusterHealth string
	Recommendations []string
}

type checkRecord struct {
	Timestamp time.Time
	LatencyMs float64
	Success bool
	ReplicationLagMs float64
	Connections int
}

type nodeData struct {
	nodeID string
	consecutiveFailures int
	totalChecks int
	failedChecks int
	history []checkRecord
	lastCheck time.Time
	currentConnections int
	lastReplicationLag float64
	highLatencySuppressed bool
}

type Monitor struct {
	cfg Config
	mu sync.RWMutex
	nodes map[string]*nodeData
	alerts []Alert
	alertCounter uint64
}

func NewMonitor(cfg Config) *Monitor {
	cfg = cfg.withDefaults()
	return &Monitor{cfg: cfg, nodes: make(map[string]*nodeData)}
}

func (m *Monitor) RecordCheck(result CheckResult) []Alert {
	if result.NodeID == "" { return []Alert{} }
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
	if !ok { nd = &nodeData{nodeID: result.NodeID}; m.nodes[result.NodeID] = nd }
	nd.totalChecks++
	if !result.Success { nd.failedChecks++; nd.consecutiveFailures++ } else { nd.consecutiveFailures = 0 }
	nd.lastCheck = ts
	nd.currentConnections = conns
	nd.lastReplicationLag = repLag
	rec := checkRecord{Timestamp: ts, LatencyMs: latency, Success: result.Success, ReplicationLagMs: repLag, Connections: conns}
	nd.history = append(nd.history, rec)
	maxHist := m.cfg.WindowSize*2
	if maxHist < 20 { maxHist = 20 }
	if len(nd.history) > maxHist { nd.history = nd.history[len(nd.history)-maxHist:] }
	var timeWindow []checkRecord
	cutoff := ts.Add(-60 * time.Second)
	for _, r := range nd.history {
		if !r.Timestamp.Before(cutoff) {
			timeWindow = append(timeWindow, r)
		}
	}
	failedInTimeWindow := 0
	for _, r := range timeWindow { if !r.Success { failedInTimeWindow++ } }
	errorRate := 0.0
	if len(timeWindow) > 0 { errorRate = float64(failedInTimeWindow)/float64(len(timeWindow)) }
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
	if len(window) > 0 { avgLat = sumLat/float64(len(window)) }
	isHealthy := nd.consecutiveFailures < m.cfg.DownThreshold
	return NodeStatus{NodeID: nd.nodeID, IsHealthy: isHealthy, ConsecutiveFailures: nd.consecutiveFailures, TotalChecks: nd.totalChecks, FailedChecks: nd.failedChecks, AvgLatencyMs: avgLat, LastCheck: nd.lastCheck, CurrentConnections: nd.currentConnections, LastReplicationLagMs: nd.lastReplicationLag}
}

func (m *Monitor) GetNodeStatus(nodeID string) (NodeStatus, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]
	if !ok { return NodeStatus{}, false }
	status := m.computeNodeStatusLocked(nd)
	return status, true
}
func (m *Monitor) GetAllNodes() []NodeStatus {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]NodeStatus, 0, len(m.nodes))
	for _, nd := range m.nodes { out = append(out, m.computeNodeStatusLocked(nd)) }
	sort.Slice(out, func(i,j int) bool { return out[i].NodeID < out[j].NodeID })
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
	for _, a := range m.alerts { if a.NodeID == nodeID { out = append(out, a) } }
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
	uptime := float64(nd.totalChecks-nd.failedChecks)/float64(nd.totalChecks)*100.0
	if uptime <0 { uptime=0 }
	if uptime>100 { uptime=100 }
	return uptime
}
func (m *Monitor) computeHealthScoreLocked(nd *nodeData) HealthScore {
	windowSize := m.cfg.WindowSize
	histLen := len(nd.history)
	startIdx := 0
	if histLen > windowSize { startIdx = histLen - windowSize }
	window := nd.history[startIdx:]
	var timeWindow []checkRecord
	if len(nd.history) > 0 {
		cutoff := nd.lastCheck.Add(-60*time.Second)
		for _, r := range nd.history {
			if !r.Timestamp.Before(cutoff) {
				timeWindow = append(timeWindow, r)
			}
		}
	}
	failedInTimeWindow := 0
	for _, r := range timeWindow { if !r.Success { failedInTimeWindow++ } }
	errorRate := 0.0
	if len(timeWindow) > 0 { errorRate = float64(failedInTimeWindow)/float64(len(timeWindow)) }
	var sumLat, sumLag float64
	for _, r := range window { sumLat+=r.LatencyMs; sumLag+=r.ReplicationLagMs }
	avgLat:=0.0; avgLag:=0.0
	if len(window)>0 { avgLat=sumLat/float64(len(window)); avgLag=sumLag/float64(len(window)) }
	factors:=make(map[string]float64)
	var errDeduction float64; if errorRate>0 { errDeduction=errorRate*50.0 }; factors["error_rate"]=errDeduction
	var latDeduction float64
	if avgLat>m.cfg.LatencyThresholdMs {
		over:=(avgLat-m.cfg.LatencyThresholdMs)/m.cfg.LatencyThresholdMs; latDeduction=over*20.0; if latDeduction>30 { latDeduction=30 }
	} else if avgLat>m.cfg.LatencyThresholdMs*0.8 { latDeduction=5 }
	factors["latency"]=latDeduction
	var lagDeduction float64
	if avgLag>m.cfg.ReplicationLagThresholdMs {
		over:=(avgLag-m.cfg.ReplicationLagThresholdMs)/m.cfg.ReplicationLagThresholdMs; lagDeduction=over*15.0; if lagDeduction>20 { lagDeduction=20 }
	} else if avgLag>m.cfg.ReplicationLagThresholdMs*0.8 { lagDeduction=3 }
	factors["replication_lag"]=lagDeduction
	var cfDeduction float64=float64(nd.consecutiveFailures)*10; if cfDeduction>40 { cfDeduction=40 }; factors["consecutive_failures"]=cfDeduction
	var connDeduction float64
	if nd.currentConnections>m.cfg.ConnectionThreshold { connDeduction=15 } else if nd.currentConnections>int(float64(m.cfg.ConnectionThreshold)*0.8) { connDeduction=5 }
	factors["connections"]=connDeduction
	score:=100.0-(errDeduction+latDeduction+lagDeduction+cfDeduction+connDeduction)
	if score<0 { score=0 }; if score>100 { score=100 }
	trend:=m.computeOverallTrendLocked(nd)
	return HealthScore{NodeID: nd.nodeID, Score: score, Factors: factors, LastUpdated: time.Now(), Trend: trend}
}
func (m *Monitor) computeOverallTrendLocked(nd *nodeData) string {
	latTrend:=m.getTrendLocked(nd, "latency"); errTrend:=m.getTrendLocked(nd, "error_rate"); lagTrend:=m.getTrendLocked(nd, "replication_lag")
	if latTrend=="degrading"||errTrend=="degrading"||lagTrend=="degrading" { return "degrading" }
	if latTrend=="improving"||errTrend=="improving"||lagTrend=="improving" { return "improving" }
	return "stable"
}
func (m *Monitor) getTrendLocked(nd *nodeData, metric string) string {
	if nd==nil { return "stable" }
	hist:=nd.history; n:=len(hist); if n<4 { return "stable" }
	var filtered []checkRecord
	consec := 0
	for _, r := range hist {
		if !r.Success { consec++ } else { consec=0 }
		isDown := consec >= m.cfg.DownThreshold
		if !isDown {
			filtered = append(filtered, r)
		}
	}
	hist = filtered
	n = len(hist)
	if n<4 { return "stable" }
	half:=n/2; older:=hist[:half]; newer:=hist[n-half:]
	var olderAvg, newerAvg float64
	switch metric {
	case "latency":
		var sumOld,sumNew float64; for _,r:=range older { sumOld+=r.LatencyMs }; for _,r:=range newer { sumNew+=r.LatencyMs }
		if len(older)>0 { olderAvg=sumOld/float64(len(older)) }; if len(newer)>0 { newerAvg=sumNew/float64(len(newer)) }
	case "replication_lag":
		var sumOld,sumNew float64; for _,r:=range older { sumOld+=r.ReplicationLagMs }; for _,r:=range newer { sumNew+=r.ReplicationLagMs }
		if len(older)>0 { olderAvg=sumOld/float64(len(older)) }; if len(newer)>0 { newerAvg=sumNew/float64(len(newer)) }
	case "connections":
		var sumOld,sumNew float64; for _,r:=range older { sumOld+=float64(r.Connections) }; for _,r:=range newer { sumNew+=float64(r.Connections) }
		if len(older)>0 { olderAvg=sumOld/float64(len(older)) }; if len(newer)>0 { newerAvg=sumNew/float64(len(newer)) }
	case "error_rate":
		var failOld,failNew int; for _,r:=range older { if !r.Success { failOld++ } }; for _,r:=range newer { if !r.Success { failNew++ } }
		if len(older)>0 { olderAvg=float64(failOld)/float64(len(older)) }; if len(newer)>0 { newerAvg=float64(failNew)/float64(len(newer)) }
	default:
		return "stable"
	}
	if olderAvg==0 && newerAvg==0 { return "stable" }
	if olderAvg==0 && newerAvg>0 { return "degrading" }
	if newerAvg==0 && olderAvg>0 { return "improving" }
	if newerAvg>olderAvg*1.1 { return "degrading" }
	if newerAvg<olderAvg*0.9 { return "improving" }
	return "stable"
}
func (m *Monitor) GetTrend(nodeID string, metric string) string {
	m.mu.RLock(); defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]; if !ok { return "stable" }
	return m.getTrendLocked(nd, metric)
}
func (m *Monitor) GetHealthScore(nodeID string) (HealthScore, bool) {
	m.mu.RLock(); defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]; if !ok { return HealthScore{}, false }
	hs:=m.computeHealthScoreLocked(nd)
	fCopy:=make(map[string]float64, len(hs.Factors)); for k,v:=range hs.Factors { fCopy[k]=v }; hs.Factors=fCopy
	return hs, true
}
func (m *Monitor) GetAllHealthScores() []HealthScore {
	m.mu.RLock(); defer m.mu.RUnlock()
	out:=make([]HealthScore,0,len(m.nodes))
	for _, nd:=range m.nodes {
		hs:=m.computeHealthScoreLocked(nd)
		fCopy:=make(map[string]float64, len(hs.Factors)); for k,v:=range hs.Factors { fCopy[k]=v }; hs.Factors=fCopy; out=append(out, hs)
	}
	sort.Slice(out, func(i,j int) bool { return out[i].NodeID < out[j].NodeID })
	return out
}
func (m *Monitor) PredictFailure(nodeID string) (FailurePrediction, bool) {
	m.mu.RLock(); defer m.mu.RUnlock()
	nd, ok := m.nodes[nodeID]; if !ok { return FailurePrediction{}, false }
	hs:=m.computeHealthScoreLocked(nd); score:=hs.Score; trend:=hs.Trend
	windowSize:=m.cfg.WindowSize; histLen:=len(nd.history); startIdx:=0
	if histLen>windowSize { startIdx=histLen-windowSize }
	window:=nd.history[startIdx:]
	var timeWindow []checkRecord
	if len(nd.history)>0 {
		cutoff:=nd.lastCheck.Add(-60*time.Second)
		for _, r:=range nd.history {
			if !r.Timestamp.Before(cutoff) {
				timeWindow=append(timeWindow, r)
			}
		}
	}
	failedInTimeWindow:=0
	for _, r:=range timeWindow { if !r.Success { failedInTimeWindow++ } }
	errorRate:=0.0
	if len(timeWindow)>0 { errorRate=float64(failedInTimeWindow)/float64(len(timeWindow)) }
	var sumLat,sumLag float64
	for _, r:=range window { sumLat+=r.LatencyMs; sumLag+=r.ReplicationLagMs }
	avgLat:=0.0; avgLag:=0.0
	if len(window)>0 { avgLat=sumLat/float64(len(window)); avgLag=sumLag/float64(len(window)) }
	var risk RiskLevel; var prob float64
	if nd.consecutiveFailures>=m.cfg.DownThreshold { risk=RiskCritical; prob=0.95 } else if score<20 { risk=RiskCritical; prob=0.9 } else if score<40 { risk=RiskCritical; prob=0.8 } else if score<50 { risk=RiskHigh; prob=0.7 } else if score<60 { risk=RiskHigh; prob=0.6 } else if score<75 {
		if trend=="degrading" { risk=RiskMedium; prob=0.4 } else { risk=RiskLow; prob=0.2 }
	} else {
		if trend=="degrading" { risk=RiskMedium; prob=0.35 } else { risk=RiskLow; prob=0.1 }
	}
	var within time.Duration
	switch risk { case RiskCritical: within=5*time.Minute; case RiskHigh: within=15*time.Minute; case RiskMedium: within=1*time.Hour; case RiskLow: within=4*time.Hour }
	var reasons []string
	if errorRate>m.cfg.ErrorRateThreshold { reasons=append(reasons, fmt.Sprintf("high error rate: %.1f%%", errorRate*100)) }
	if avgLat>m.cfg.LatencyThresholdMs { reasons=append(reasons, fmt.Sprintf("high latency: %.1fms > %.1fms", avgLat, m.cfg.LatencyThresholdMs)) }
	if avgLag>m.cfg.ReplicationLagThresholdMs { reasons=append(reasons, fmt.Sprintf("high replication lag: %.1fms > %.1fms", avgLag, m.cfg.ReplicationLagThresholdMs)) }
	if nd.consecutiveFailures>0 { reasons=append(reasons, fmt.Sprintf("consecutive failures: %d", nd.consecutiveFailures)) }
	if nd.currentConnections>int(float64(m.cfg.ConnectionThreshold)*0.8) { reasons=append(reasons, fmt.Sprintf("high connection usage: %d", nd.currentConnections)) }
	if m.getTrendLocked(nd, "latency")=="degrading" { reasons=append(reasons, "degrading latency trend") }
	if len(reasons)==0 && risk==RiskLow { reasons=append(reasons, "no significant issues") }
	return FailurePrediction{NodeID: nodeID, RiskLevel: risk, Probability: prob, PredictedFailureWithin: within, Reasons: reasons, Timestamp: time.Now()}, true
}
func (m *Monitor) GetReliabilityReport() ReliabilityReport {
	m.mu.RLock(); defer m.mu.RUnlock()
	healthScores:=make([]HealthScore,0,len(m.nodes))
	for _, nd:=range m.nodes {
		hs:=m.computeHealthScoreLocked(nd)
		fCopy:=make(map[string]float64, len(hs.Factors)); for k,v:=range hs.Factors { fCopy[k]=v }; hs.Factors=fCopy; healthScores=append(healthScores, hs)
	}
	sort.Slice(healthScores, func(i,j int) bool { return healthScores[i].NodeID < healthScores[j].NodeID })
	overall:=100.0
	if len(healthScores)>0 { var sum float64; for _, hs:=range healthScores { sum+=hs.Score }; overall=sum/float64(len(healthScores)) }
	clusterHealth:="healthy"
	switch { case overall>=80: clusterHealth="healthy"; case overall>=60: clusterHealth="degraded"; case overall>=40: clusterHealth="unhealthy"; default: clusterHealth="critical" }
	predictions:=make([]FailurePrediction,0,len(m.nodes))
	for _, nd:=range m.nodes {
		hs:=m.computeHealthScoreLocked(nd)
		windowSize:=m.cfg.WindowSize; histLen:=len(nd.history); startIdx:=0
		if histLen>windowSize { startIdx=histLen-windowSize }
		window:=nd.history[startIdx:]
		var timeWindow []checkRecord
		if len(nd.history)>0 {
			cutoff:=nd.lastCheck.Add(-60*time.Second)
			for _, r:=range nd.history {
				if !r.Timestamp.Before(cutoff) { timeWindow=append(timeWindow, r) }
			}
		}
		failedInTimeWindow:=0
		for _, r:=range timeWindow { if !r.Success { failedInTimeWindow++ } }
		errorRate:=0.0
		if len(timeWindow)>0 { errorRate=float64(failedInTimeWindow)/float64(len(timeWindow)) }
		var sumLat,sumLag float64
		for _, r:=range window { sumLat+=r.LatencyMs; sumLag+=r.ReplicationLagMs }
		avgLat:=0.0; avgLag:=0.0
		if len(window)>0 { avgLat=sumLat/float64(len(window)); avgLag=sumLag/float64(len(window)) }
		score:=hs.Score; trend:=hs.Trend
		var risk RiskLevel; var prob float64
		if nd.consecutiveFailures>=m.cfg.DownThreshold { risk=RiskCritical; prob=0.95 } else if score<20 { risk=RiskCritical; prob=0.9 } else if score<40 { risk=RiskCritical; prob=0.8 } else if score<50 { risk=RiskHigh; prob=0.7 } else if score<60 { risk=RiskHigh; prob=0.6 } else if score<75 {
			if trend=="degrading" { risk=RiskMedium; prob=0.4 } else { risk=RiskLow; prob=0.2 }
		} else {
			if trend=="degrading" { risk=RiskMedium; prob=0.35 } else { risk=RiskLow; prob=0.1 }
		}
		var within time.Duration
		switch risk { case RiskCritical: within=5*time.Minute; case RiskHigh: within=15*time.Minute; case RiskMedium: within=1*time.Hour; case RiskLow: within=4*time.Hour }
		var reasons []string
		if errorRate>m.cfg.ErrorRateThreshold { reasons=append(reasons, fmt.Sprintf("high error rate: %.1f%%", errorRate*100)) }
		if avgLat>m.cfg.LatencyThresholdMs { reasons=append(reasons, fmt.Sprintf("high latency: %.1fms > %.1fms", avgLat, m.cfg.LatencyThresholdMs)) }
		if avgLag>m.cfg.ReplicationLagThresholdMs { reasons=append(reasons, fmt.Sprintf("high replication lag: %.1fms > %.1fms", avgLag, m.cfg.ReplicationLagThresholdMs)) }
		if nd.consecutiveFailures>0 { reasons=append(reasons, fmt.Sprintf("consecutive failures: %d", nd.consecutiveFailures)) }
		if m.getTrendLocked(nd, "latency")=="degrading" { reasons=append(reasons, "degrading latency trend") }
		if len(reasons)==0 && risk==RiskLow { reasons=append(reasons, "no significant issues") }
		pred:=FailurePrediction{NodeID: nd.nodeID, RiskLevel: risk, Probability: prob, PredictedFailureWithin: within, Reasons: reasons, Timestamp: time.Now()}
		predictions=append(predictions, pred)
	}
	sort.Slice(predictions, func(i,j int) bool { return predictions[i].NodeID < predictions[j].NodeID })
	var recommendations []string
	var criticalNodes, highRiskNodes []string
	hasLatency, hasError, hasLag, hasConn, hasDegrading := false,false,false,false,false
	for _, hs:=range healthScores {
		if hs.Factors["latency"]>0 { hasLatency=true }
		if hs.Factors["error_rate"]>0 { hasError=true }
		if hs.Factors["replication_lag"]>0 { hasLag=true }
		if hs.Factors["connections"]>0 { hasConn=true }
		if hs.Trend=="degrading" { hasDegrading=true }
	}
	for _, p:=range predictions {
		if p.RiskLevel==RiskCritical { criticalNodes=append(criticalNodes, p.NodeID) }
		if p.RiskLevel==RiskHigh { highRiskNodes=append(highRiskNodes, p.NodeID) }
	}
	if len(criticalNodes)>0 { recommendations=append(recommendations, fmt.Sprintf("immediate investigation required for nodes: %s", joinList(criticalNodes))) }
	if len(highRiskNodes)>0 { recommendations=append(recommendations, fmt.Sprintf("proactive maintenance recommended for high-risk nodes: %s", joinList(highRiskNodes))) }
	if hasLatency { recommendations=append(recommendations, "consider scaling or optimizing queries for high latency nodes") }
	if hasError { recommendations=append(recommendations, "check application logs and database connectivity") }
	if hasLag { recommendations=append(recommendations, "check replica status and network") }
	if hasConn { recommendations=append(recommendations, "consider increasing connection pool or investigating connection leaks") }
	if hasDegrading { recommendations=append(recommendations, "monitor degrading trends and plan capacity") }
	if len(recommendations)==0 { recommendations=append(recommendations, "cluster operating normally") }
	return ReliabilityReport{Timestamp: time.Now(), OverallScore: overall, Nodes: healthScores, Predictions: predictions, ClusterHealth: clusterHealth, Recommendations: recommendations}
}
func joinList(list []string) string {
	result:=""
	for i,s:=range list { if i>0 { result+=", " }; result+=s }
	return result
}
GOCODE
go mod tidy
go vet ./... || true
echo "solution applied step1"
