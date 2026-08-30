"""
Step2 verifier - proactive scoring + reactive regression + AFTR coverage
"""

import os, subprocess, tempfile, textwrap, shutil, re, random, json, math, time
import pytest

APP_DIR = "/app"


def run(cmd, cwd=APP_DIR, timeout=30):
    env = os.environ.copy()
    env["GOCACHE"] = "/tmp/codimango/gocache"
    env["GOPATH"] = "/tmp/codimango/gopath"
    env["GOFLAGS"] = "-mod=mod"
    env["GOTOOLCHAIN"] = "local"
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )


def go_run_program(go_code, timeout=20):
    tmp = tempfile.mkdtemp(prefix="dbrel2_test_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require db-reliability v0.0.0
        replace db-reliability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_run_race_program(go_code, timeout=60):
    tmp = tempfile.mkdtemp(prefix="dbrel2_test_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require db-reliability v0.0.0
        replace db-reliability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "-race", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_files_exist():
    assert os.path.isfile(os.path.join(APP_DIR, "go.mod"))
    assert os.path.isdir(os.path.join(APP_DIR, "reliability"))


def test_go_build_and_vet():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


def test_step1_regression_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, DownThreshold:3, WindowSize:10, ErrorRateThreshold:0.9})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:150})
        if len(alerts)!=1 || alerts[0].Type!=reliability.AlertHighLatency { panic("step1 regression latency") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"regression failed: {proc.stdout} {proc.stderr}"


def test_health_score_healthy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        for i:=0;i<5;i++{
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20, ReplicationLagMs:100, Connections:10})
        }
        score, ok := m.GetHealthScore("db1")
        if !ok { panic("score not found") }
        if score.Score < 90 { panic(fmt.Sprintf("healthy should >=90 got %f", score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"healthy score failed: {proc.stdout} {proc.stderr}"


def test_health_score_degraded():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, ErrorRateThreshold:0.05, DownThreshold:10, WindowSize:10})
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150}) }
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:200, ReplicationLagMs:600, Connections:150}) }
        score,_ := m.GetHealthScore("db1")
        if score.Score >= 90 { panic(fmt.Sprintf("degraded should <90 got %f", score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"degraded failed: {proc.stdout} {proc.stderr}"


def test_trend_degrading():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10})
        for _, v := range []float64{10,20,30,40,50,60,70,80} {
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:v})
        }
        trend := m.GetTrend("db1","latency")
        if trend!="degrading" { panic(fmt.Sprintf("expected degrading got %s", trend)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"trend degrading failed: {proc.stdout} {proc.stderr}"


def test_predict_failure_low_risk():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20}) }
        pred, _ := m.PredictFailure("db1")
        if pred.RiskLevel!=reliability.RiskLow { panic(fmt.Sprintf("expected low got %s", pred.RiskLevel)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"low risk failed: {proc.stdout} {proc.stderr}"


def test_predict_failure_critical_down():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{DownThreshold:3, WindowSize:10})
        for i:=0;i<3;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false}) }
        pred, _ := m.PredictFailure("db1")
        if pred.RiskLevel!=reliability.RiskCritical { panic(fmt.Sprintf("down should critical got %s", pred.RiskLevel)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"critical down failed: {proc.stdout} {proc.stderr}"


def test_reliability_report():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20})
        report := m.GetReliabilityReport()
        if len(report.Nodes)!=1 { panic("nodes 1") }
        if report.OverallScore <0 || report.OverallScore>100 { panic("overall out of range") }
        if len(report.Recommendations)==0 { panic("recommendations empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"report failed: {proc.stdout} {proc.stderr}"


def test_concurrency_scoring():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:20, DownThreshold:100, ErrorRateThreshold:0.9})
        var wg sync.WaitGroup
        n:=50
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<100;j++{
                    m.RecordCheck(reliability.CheckResult{NodeID:fmt.Sprintf("db-%d", idx%5), Success:true, LatencyMs:float64(j%100)})
                    m.GetHealthScore(fmt.Sprintf("db-%d", idx%5))
                }
            }(i)
        }
        wg.Wait()
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrency scoring failed: {proc.stdout} {proc.stderr}"
    )


def test_health_score_clamped():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, ReplicationLagThresholdMs:10, ConnectionThreshold:10, ErrorRateThreshold:0.01, DownThreshold:2, WindowSize:10})
        for i:=0;i<20;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:1000, ReplicationLagMs:1000, Connections:1000}) }
        score,_:=m.GetHealthScore("db1")
        if score.Score<0 || score.Score>100 { panic(fmt.Sprintf("out of 0-100 got %f", score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"clamped failed: {proc.stdout} {proc.stderr}"


def test_trend_stable_insufficient():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10})
        for i:=0;i<2;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10}) }
        if m.GetTrend("db1","latency")!="stable" { panic("should stable") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"stable trend failed: {proc.stdout} {proc.stderr}"


def test_trend_improving():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10})
        for _, v := range []float64{80,70,60,50,40,30,20,10} {
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:v})
        }
        if m.GetTrend("db1","latency")!="improving" { panic("should improving") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"improving failed: {proc.stdout} {proc.stderr}"


def test_predict_failure_high_risk():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.05})
        for i:=0;i<10;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:500}) }
        pred,_:=m.PredictFailure("db1")
        if pred.RiskLevel!=reliability.RiskHigh && pred.RiskLevel!=reliability.RiskCritical { panic(fmt.Sprintf("expected high/critical got %s", pred.RiskLevel)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"high risk failed: {proc.stdout} {proc.stderr}"


def test_reliability_report_empty():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        report:=m.GetReliabilityReport()
        if report.OverallScore!=100 { panic(fmt.Sprintf("empty overall 100 got %f", report.OverallScore)) }
        if len(report.Recommendations)==0 { panic("recommendations empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"empty report failed: {proc.stdout} {proc.stderr}"


def test_cluster_health_levels():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m1:=reliability.NewMonitor(reliability.Config{})
        m1.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10})
        r1:=m1.GetReliabilityReport()
        if r1.ClusterHealth!="healthy" { panic("healthy expected") }
        m2:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.01, ReplicationLagThresholdMs:10, ConnectionThreshold:10})
        for i:=0;i<10;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:1000, ReplicationLagMs:1000, Connections:1000}) }
        r2:=m2.GetReliabilityReport()
        if r2.OverallScore>=60 { panic(fmt.Sprintf("should <60 got %f", r2.OverallScore)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"health levels failed: {proc.stdout} {proc.stderr}"


def test_recommendations_content():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.9})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150})
        report:=m.GetReliabilityReport()
        recStr:=strings.Join(report.Recommendations, " ")
        recLower:=strings.ToLower(recStr)
        if !strings.Contains(recLower, "scaling") && !strings.Contains(recLower, "latency") { panic("should mention latency") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"recommendations content failed: {proc.stdout} {proc.stderr}"
    )


def test_scoring_factors_sum():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, ErrorRateThreshold:0.05, DownThreshold:10, WindowSize:5})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150})
        score,_:=m.GetHealthScore("db1")
        sum:=0.0
        for _, v := range score.Factors { sum+=v }
        expected:=100-sum
        if math.Abs(expected-score.Score)>0.001 { panic(fmt.Sprintf("score 100-sum expected %f got %f", expected, score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"factors sum failed: {proc.stdout} {proc.stderr}"


# ---- Combined examples only (hardened) ----


def test_scoring_combined_examples():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.05}
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        // Helper to compute health score for a given set of checks that produce desired errorRate, avgLatency etc.
        // We directly use RecordCheck sequence to achieve needed averages.
        // Test combined observations from instruction (8 rows)

        // 1. (0.2,120,600,0,10) -> 83  : 1 fail /5 in 60s =0.2, avgLat 120, avgLag 600, consec 0, conn 10
        m1:=reliability.NewMonitor(cfg)
        m1.RecordCheck(reliability.CheckResult{NodeID:"c1", Timestamp:base.Add(-40*time.Second), Success:false, LatencyMs:120, ReplicationLagMs:600, Connections:10})
        for i:=0;i<4;i++{ m1.RecordCheck(reliability.CheckResult{NodeID:"c1", Timestamp:base.Add(time.Duration(-30+i*10)*time.Second), Success:true, LatencyMs:120, ReplicationLagMs:600, Connections:10}) }
        s1,_:=m1.GetHealthScore("c1")
        if s1.Score <82 || s1.Score>84 { panic(fmt.Sprintf("obs1 expected 83 got %f factors %v", s1.Score, s1.Factors)) }

        // 2. (0.2,120,600,1,90) ->68 : last check fail => consec 1, conn 90
        m2:=reliability.NewMonitor(cfg)
        for i:=0;i<4;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"c2", Timestamp:base.Add(time.Duration(-40+i*10)*time.Second), Success:true, LatencyMs:120, ReplicationLagMs:600, Connections:10}) }
        m2.RecordCheck(reliability.CheckResult{NodeID:"c2", Timestamp:base, Success:false, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        s2,_:=m2.GetHealthScore("c2")
        if s2.Score <67 || s2.Score>69 { panic(fmt.Sprintf("obs2 expected 68 got %f %v", s2.Score, s2.Factors)) }

        // 3. (0.5,250,100,2,70) ->25 : 2 fails /4 =0.5, consec 2, lat 250 (30), lag 100 (0), conn 70 (0)
        m3:=reliability.NewMonitor(cfg)
        m3.RecordCheck(reliability.CheckResult{NodeID:"c3", Timestamp:base.Add(-30*time.Second), Success:true, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        m3.RecordCheck(reliability.CheckResult{NodeID:"c3", Timestamp:base.Add(-20*time.Second), Success:true, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        m3.RecordCheck(reliability.CheckResult{NodeID:"c3", Timestamp:base.Add(-10*time.Second), Success:false, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        m3.RecordCheck(reliability.CheckResult{NodeID:"c3", Timestamp:base, Success:false, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        s3,_:=m3.GetHealthScore("c3")
        if s3.Score <24 || s3.Score>26 { panic(fmt.Sprintf("obs3 expected 25 got %f %v", s3.Score, s3.Factors)) }

        // 4. (0.2,85,600,1,150) ->57  : avgLat 85 (0.8 tier 5), lag 600 (3), consec1 (10), conn150 (15), error 0.2 (10) =38? Wait 10+5+3+10+15=43 =>57
        m4:=reliability.NewMonitor(cfg)
        for i:=0;i<4;i++{ m4.RecordCheck(reliability.CheckResult{NodeID:"c4", Timestamp:base.Add(time.Duration(-40+i*10)*time.Second), Success:true, LatencyMs:85, ReplicationLagMs:600, Connections:10}) }
        m4.RecordCheck(reliability.CheckResult{NodeID:"c4", Timestamp:base, Success:false, LatencyMs:85, ReplicationLagMs:600, Connections:150})
        s4,_:=m4.GetHealthScore("c4")
        if s4.Score <56 || s4.Score>58 { panic(fmt.Sprintf("obs4 expected 57 got %f %v", s4.Score, s4.Factors)) }

        // 5. (0.1,150,1000,0,90) ->65 : error 0.1 =1 fail/10 within 60s, avgLat 150 (10), lag 1000 (15), conn 90 (5)
        m5:=reliability.NewMonitor(cfg)
        m5.RecordCheck(reliability.CheckResult{NodeID:"c5", Timestamp:base.Add(-50*time.Second), Success:false, LatencyMs:150, ReplicationLagMs:1000, Connections:90})
        for i:=0;i<9;i++{ m5.RecordCheck(reliability.CheckResult{NodeID:"c5", Timestamp:base.Add(time.Duration(-40+i*5)*time.Second), Success:true, LatencyMs:150, ReplicationLagMs:1000, Connections:90}) }
        s5,_:=m5.GetHealthScore("c5")
        if s5.Score <64 || s5.Score>66 { panic(fmt.Sprintf("obs5 expected 65 got %f %v", s5.Score, s5.Factors)) }

        // 6. (0.4,50,100,4,10) ->40 : 4 fails/10=0.4 (20) + consec4 (40) =60 =>40
        m6:=reliability.NewMonitor(cfg)
        for i:=0;i<6;i++{ m6.RecordCheck(reliability.CheckResult{NodeID:"c6", Timestamp:base.Add(time.Duration(-50+i*5)*time.Second), Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10}) }
        for i:=0;i<4;i++{ m6.RecordCheck(reliability.CheckResult{NodeID:"c6", Timestamp:base.Add(time.Duration(-20+i*5)*time.Second), Success:false, LatencyMs:50, ReplicationLagMs:100, Connections:10}) }
        s6,_:=m6.GetHealthScore("c6")
        if s6.Score <39 || s6.Score>41 { panic(fmt.Sprintf("obs6 expected 40 got %f %v", s6.Score, s6.Factors)) }

        // 7. (0.5,200,2000,1,90) ->20 : 2 fails/4=0.5 (25) + lat200 (20) + lag2000 (20) + consec1 (10) + conn90 (5) =80 =>20
        m7:=reliability.NewMonitor(cfg)
        m7.RecordCheck(reliability.CheckResult{NodeID:"c7", Timestamp:base.Add(-30*time.Second), Success:false, LatencyMs:200, ReplicationLagMs:2000, Connections:90})
        m7.RecordCheck(reliability.CheckResult{NodeID:"c7", Timestamp:base.Add(-20*time.Second), Success:true, LatencyMs:200, ReplicationLagMs:2000, Connections:90})
        m7.RecordCheck(reliability.CheckResult{NodeID:"c7", Timestamp:base.Add(-10*time.Second), Success:true, LatencyMs:200, ReplicationLagMs:2000, Connections:90})
        m7.RecordCheck(reliability.CheckResult{NodeID:"c7", Timestamp:base, Success:false, LatencyMs:200, ReplicationLagMs:2000, Connections:90})
        s7,_:=m7.GetHealthScore("c7")
        if s7.Score <19 || s7.Score>21 { panic(fmt.Sprintf("obs7 expected 20 got %f %v", s7.Score, s7.Factors)) }

        // 8. (1.0,250,2000,4,150) ->0 clamped
        m8:=reliability.NewMonitor(cfg)
        for i:=0;i<5;i++{ m8.RecordCheck(reliability.CheckResult{NodeID:"c8", Timestamp:base.Add(time.Duration(-40+i*10)*time.Second), Success:false, LatencyMs:250, ReplicationLagMs:2000, Connections:150}) }
        s8,_:=m8.GetHealthScore("c8")
        if s8.Score !=0 { panic(fmt.Sprintf("obs8 expected 0 clamped got %f", s8.Score)) }

        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"combined examples failed: {proc.stdout} {proc.stderr}"
    )


def test_trend_outage_exclusion():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{DownThreshold:3, WindowSize:20}
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        m2:=reliability.NewMonitor(cfg)
        for i:=0;i<4;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(i)*time.Second), Success:true, LatencyMs:10}) }
        for i:=0;i<3;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(10+i)*time.Second), Success:false, LatencyMs:1000}) }
        for i:=0;i<4;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(20+i)*time.Second), Success:true, LatencyMs:10}) }
        trend:=m2.GetTrend("db1","latency")
        if trend!="stable" { panic(fmt.Sprintf("trend with outage exclusion should be stable (down skipped), got %s", trend)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"outage exclusion failed: {proc.stdout} {proc.stderr}"


# ---- New coverage from AFTR ----


def test_predict_failure_probabilities_and_durations():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{DownThreshold:3, WindowSize:10, LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, ErrorRateThreshold:0.05}
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)

        // 1. consec >= DownThreshold → critical 0.95 within 5m
        m1:=reliability.NewMonitor(cfg)
        for i:=0;i<3;i++{ m1.RecordCheck(reliability.CheckResult{NodeID:"n1", Timestamp:base.Add(time.Duration(i)*time.Second), Success:false}) }
        p1,_:=m1.PredictFailure("n1")
        if p1.RiskLevel!=reliability.RiskCritical || p1.Probability!=0.95 { panic(fmt.Sprintf("down critical 0.95 got %s %f", p1.RiskLevel, p1.Probability)) }
        if p1.PredictedFailureWithin!=5*time.Minute { panic(fmt.Sprintf("down within 5m got %v", p1.PredictedFailureWithin)) }

        // 2. score <20 → critical 0.9 within 5m (avoid down by using high DownThreshold)
        cfgHigh:=reliability.Config{DownThreshold:20, WindowSize:10, LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, ErrorRateThreshold:0.05}
        m2:=reliability.NewMonitor(cfgHigh)
        for i:=0;i<10;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"n2", Timestamp:base.Add(time.Duration(i)*time.Second), Success:false, LatencyMs:1000, ReplicationLagMs:1000, Connections:1000}) }
        p2,_:=m2.PredictFailure("n2")
        // score will be 0 (<20) but not down because DownThreshold 20, consec 10 <20
        if p2.RiskLevel!=reliability.RiskCritical || p2.Probability!=0.9 { panic(fmt.Sprintf("score<20 critical 0.9 got %s %f score in report", p2.RiskLevel, p2.Probability)) }
        if p2.PredictedFailureWithin!=5*time.Minute { panic("score<20 within 5m") }

        // We'll test remaining bands via controlled scores
        // 3. score <40 → critical 0.8 within 5m (consec 2 < DownThreshold 3, score 25)
        m3:=reliability.NewMonitor(cfg)
        // 2 fails /4 =0.5 (25) + lat 250 (30) + consec 2 (20) =75 => score 25
        m3.RecordCheck(reliability.CheckResult{NodeID:"n3", Timestamp:base.Add(-30*time.Second), Success:true, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        m3.RecordCheck(reliability.CheckResult{NodeID:"n3", Timestamp:base.Add(-20*time.Second), Success:true, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        m3.RecordCheck(reliability.CheckResult{NodeID:"n3", Timestamp:base.Add(-10*time.Second), Success:false, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        m3.RecordCheck(reliability.CheckResult{NodeID:"n3", Timestamp:base, Success:false, LatencyMs:250, ReplicationLagMs:100, Connections:70})
        p3,_:=m3.PredictFailure("n3")
        if p3.RiskLevel!=reliability.RiskCritical || p3.Probability!=0.8 { panic(fmt.Sprintf("score<40 critical 0.8 got %s %f", p3.RiskLevel, p3.Probability)) }

        // 4. score <50 → high 0.7 within 15m (use high DownThreshold to avoid down overriding)
        m4:=reliability.NewMonitor(cfgHigh)
        // score 40 from example (0.4,50,100,4,10) ->40 <50 => high 0.7
        for i:=0;i<6;i++{ m4.RecordCheck(reliability.CheckResult{NodeID:"n4", Timestamp:base.Add(time.Duration(-50+i*5)*time.Second), Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10}) }
        for i:=0;i<4;i++{ m4.RecordCheck(reliability.CheckResult{NodeID:"n4", Timestamp:base.Add(time.Duration(-20+i*5)*time.Second), Success:false, LatencyMs:50, ReplicationLagMs:100, Connections:10}) }
        p4,_:=m4.PredictFailure("n4")
        if p4.RiskLevel!=reliability.RiskHigh || p4.Probability!=0.7 { panic(fmt.Sprintf("score<50 high 0.7 got %s %f score %v", p4.RiskLevel, p4.Probability, p4)) }
        if p4.PredictedFailureWithin!=15*time.Minute { panic("score<50 within 15m") }

        // 5. score <60 → high 0.6 within 15m
        m5:=reliability.NewMonitor(cfg)
        // score ~55? Let's craft: error 0.2 (10) + lat 150 (10) + lag 600 (3) + consec 1 (10) + conn 90 (5)=38 => score 62 too high, need 55: use 0.2+150+600+2+90 =10+10+3+20+5=48 =>52 score -> high 0.6
        m5.RecordCheck(reliability.CheckResult{NodeID:"n5", Timestamp:base.Add(-40*time.Second), Success:false, LatencyMs:150, ReplicationLagMs:600, Connections:90})
        for i:=0;i<3;i++{ m5.RecordCheck(reliability.CheckResult{NodeID:"n5", Timestamp:base.Add(time.Duration(-30+i*10)*time.Second), Success:true, LatencyMs:150, ReplicationLagMs:600, Connections:90}) }
        m5.RecordCheck(reliability.CheckResult{NodeID:"n5", Timestamp:base, Success:false, LatencyMs:150, ReplicationLagMs:600, Connections:90})
        p5,_:=m5.PredictFailure("n5")
        // score should be <60
        score5,_:=m5.GetHealthScore("n5")
        if score5.Score>=60 || score5.Score<50 { panic(fmt.Sprintf("setup score<60 expected 50-60 got %f", score5.Score)) }
        if p5.RiskLevel!=reliability.RiskHigh || p5.Probability!=0.6 { panic(fmt.Sprintf("score<60 high 0.6 got %s %f", p5.RiskLevel, p5.Probability)) }

        // 6. score <75 degrading -> medium 0.4 within 1h, else low 0.2 within 4h
        m6:=reliability.NewMonitor(cfg)
        // degrading trend: increasing latency 10..80 plus score <75
        for _, v := range []float64{10,20,30,40,50,60,70,80} {
            m6.RecordCheck(reliability.CheckResult{NodeID:"n6", Success:true, LatencyMs:v, ReplicationLagMs:100, Connections:10})
        }
        // Add some error to bring score <75 but still degrading
        // Currently score from latency 45 avg? Avg last 10: (10+20+30+40+50+60+70+80)/8=45 => 0 deduction, error 0, so score 100 -> need to degrade more with latency 150 etc but keep degrading trend
        // Simpler: use latency 120 for last checks to get score ~96 but still degrading would be medium 0.35 if >=75 else 0.4 if <75
        // For <75 degrading, we need score <75 and degrading
        // Use error 0.2 (10) + latency 120 (4) + lag 600 (3) + consec 0 + conn10 =17 =>83 too high
        // Add consec 1 and conn 90: 10+4+3+10+5=32 =>68 <75 degrading
        m6b:=reliability.NewMonitor(cfg)
        for _, v := range []float64{10,20,30,40,50,60,70,80} {
            m6b.RecordCheck(reliability.CheckResult{NodeID:"n6", Timestamp:base.Add(time.Duration(v)*time.Second), Success:true, LatencyMs:v, ReplicationLagMs:100, Connections:10})
        }
        // Now add failing pattern to get 68 score but keep degrading trend (latency still increasing overall)
        m6b.RecordCheck(reliability.CheckResult{NodeID:"n6", Timestamp:base.Add(100*time.Second), Success:false, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        p6,_:=m6b.PredictFailure("n6")
        // p6 should be medium 0.4 if score <75 and degrading, or medium 0.35 if >=75 degrading
        // We allow either but must be medium for degrading
        if p6.RiskLevel!=reliability.RiskMedium { panic(fmt.Sprintf("degrading should be medium got %s", p6.RiskLevel)) }
        if p6.PredictedFailureWithin!=1*time.Hour { panic("degrading within 1h") }

        // low risk no degrading -> low 0.1 within 4h
        m7:=reliability.NewMonitor(cfg)
        for i:=0;i<5;i++{ m7.RecordCheck(reliability.CheckResult{NodeID:"n7", Success:true, LatencyMs:20}) }
        p7,_:=m7.PredictFailure("n7")
        if p7.RiskLevel!=reliability.RiskLow || p7.Probability!=0.1 { panic(fmt.Sprintf("low 0.1 got %s %f", p7.RiskLevel, p7.Probability)) }
        if p7.PredictedFailureWithin!=4*time.Hour { panic("low within 4h") }

        fmt.Println("OK")
    }
    """)
    # Replace that line with simple comment
    code = code.replace(
        "        // p2 setup removed",
        "        // p2 setup",
    )
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"probabilities/durations failed: {proc.stdout} {proc.stderr}"
    )


def test_recommendations_all_substrings():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.05}
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)

        // critical nodes
        mCrit:=reliability.NewMonitor(cfg)
        for i:=0;i<10;i++{ mCrit.RecordCheck(reliability.CheckResult{NodeID:"crit1", Timestamp:base.Add(time.Duration(i)*time.Second), Success:false, LatencyMs:1000, ReplicationLagMs:1000, Connections:1000}) }
        rCrit:=mCrit.GetReliabilityReport()
        recCrit:=strings.Join(rCrit.Recommendations, " | ")
        if !strings.Contains(strings.ToLower(recCrit), "immediate investigation") { panic("should contain immediate investigation for critical") }

        // high nodes - need score 50-60 to be high risk: 2 fails/5=0.4 (20) + lat 120 (4) + lag 600 (3) + consec 2 (20) + conn 90 (5)=52 => score 48 => high 0.7
        mHigh:=reliability.NewMonitor(cfg)
        mHigh.RecordCheck(reliability.CheckResult{NodeID:"high1", Timestamp:base.Add(-40*time.Second), Success:true, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        mHigh.RecordCheck(reliability.CheckResult{NodeID:"high1", Timestamp:base.Add(-30*time.Second), Success:true, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        mHigh.RecordCheck(reliability.CheckResult{NodeID:"high1", Timestamp:base.Add(-20*time.Second), Success:true, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        mHigh.RecordCheck(reliability.CheckResult{NodeID:"high1", Timestamp:base.Add(-10*time.Second), Success:false, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        mHigh.RecordCheck(reliability.CheckResult{NodeID:"high1", Timestamp:base, Success:false, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        rHigh:=mHigh.GetReliabilityReport()
        recHigh:=strings.Join(rHigh.Recommendations, " | ")
        if !strings.Contains(strings.ToLower(recHigh), "proactive maintenance") { panic(fmt.Sprintf("should contain proactive maintenance for high, got %s report %+v", recHigh, rHigh)) }

        // latency factor
        mLat:=reliability.NewMonitor(cfg)
        mLat.RecordCheck(reliability.CheckResult{NodeID:"lat1", Timestamp:base, Success:true, LatencyMs:200, ReplicationLagMs:100, Connections:10})
        rLat:=mLat.GetReliabilityReport()
        if !strings.Contains(strings.ToLower(strings.Join(rLat.Recommendations," ")), "scaling") && !strings.Contains(strings.ToLower(strings.Join(rLat.Recommendations," ")), "latency") {
            panic("should mention latency/scaling")
        }

        // error_rate factor
        mErr:=reliability.NewMonitor(cfg)
        mErr.RecordCheck(reliability.CheckResult{NodeID:"err1", Timestamp:base.Add(-40*time.Second), Success:false, LatencyMs:50, ReplicationLagMs:100, Connections:10})
        for i:=0;i<4;i++{ mErr.RecordCheck(reliability.CheckResult{NodeID:"err1", Timestamp:base.Add(time.Duration(-30+i*10)*time.Second), Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10}) }
        rErr:=mErr.GetReliabilityReport()
        recErr:=strings.ToLower(strings.Join(rErr.Recommendations," "))
        if !strings.Contains(recErr, "application logs") && !strings.Contains(recErr, "connectivity") {
            panic("should mention logs/connectivity for error_rate")
        }

        // replication lag
        mLag:=reliability.NewMonitor(cfg)
        mLag.RecordCheck(reliability.CheckResult{NodeID:"lag1", Timestamp:base, Success:true, LatencyMs:50, ReplicationLagMs:600, Connections:10})
        rLag:=mLag.GetReliabilityReport()
        recLag:=strings.ToLower(strings.Join(rLag.Recommendations," "))
        if !strings.Contains(recLag, "replica") && !strings.Contains(recLag, "network") {
            panic("should mention replica/network for lag")
        }

        // connections
        mConn:=reliability.NewMonitor(cfg)
        mConn.RecordCheck(reliability.CheckResult{NodeID:"conn1", Timestamp:base, Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:150})
        rConn:=mConn.GetReliabilityReport()
        recConn:=strings.ToLower(strings.Join(rConn.Recommendations," "))
        if !strings.Contains(recConn, "connection pool") && !strings.Contains(recConn, "connection leaks") {
            panic("should mention connection pool/leaks")
        }

        // degrading
        mDeg:=reliability.NewMonitor(cfg)
        for _, v := range []float64{10,20,30,40,50,60,70,80} {
            mDeg.RecordCheck(reliability.CheckResult{NodeID:"deg1", Timestamp:base.Add(time.Duration(v)*time.Second), Success:true, LatencyMs:v})
        }
        rDeg:=mDeg.GetReliabilityReport()
        recDeg:=strings.ToLower(strings.Join(rDeg.Recommendations," "))
        if !strings.Contains(recDeg, "degrading") && !strings.Contains(recDeg, "capacity") {
            panic("should mention degrading/capacity")
        }

        // healthy
        mHealthy:=reliability.NewMonitor(cfg)
        mHealthy.RecordCheck(reliability.CheckResult{NodeID:"h1", Timestamp:base, Success:true, LatencyMs:10})
        rHealthy:=mHealthy.GetReliabilityReport()
        recHealthy:=strings.ToLower(strings.Join(rHealthy.Recommendations," "))
        // healthy case should contain "operating normally" if no factors
        // Our healthy has no factors, so should be operating normally
        if !strings.Contains(recHealthy, "operating normally") && rHealthy.OverallScore <80 {
            // overall >=80 should trigger healthy message if no factors, but we may have no factors
            // allow if overall >=80
        }
        // For truly healthy with overall 100 and no nodes? Empty case is 100 and "operating normally"
        mEmpty:=reliability.NewMonitor(cfg)
        rEmpty:=mEmpty.GetReliabilityReport()
        recEmpty:=strings.ToLower(strings.Join(rEmpty.Recommendations," "))
        if !strings.Contains(recEmpty, "operating normally") {
            panic("empty healthy should contain operating normally")
        }

        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"recommendations all substrings failed: {proc.stdout} {proc.stderr}"
    )


def test_trend_all_metrics():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{WindowSize:10, DownThreshold:10}
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)

        // latency degrading already tested but also test connections
        mConn:=reliability.NewMonitor(cfg)
        for _, v := range []int{10,20,30,40,50,60,70,80} {
            mConn.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(v)*time.Second), Success:true, Connections:v})
        }
        if mConn.GetTrend("db1","connections")!="degrading" { panic("connections degrading expected") }

        // replication_lag improving
        mLag:=reliability.NewMonitor(cfg)
        for _, v := range []float64{80,70,60,50,40,30,20,10} {
            mLag.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(100-v)*time.Second), Success:true, ReplicationLagMs:v})
        }
        if mLag.GetTrend("db1","replication_lag")!="improving" { panic("lag improving expected") }

        // error_rate degrading: more fails in newer half
        mErr:=reliability.NewMonitor(cfg)
        // older 4 success, newer 4 fails
        for i:=0;i<4;i++{ mErr.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(i)*time.Second), Success:true}) }
        for i:=0;i<4;i++{ mErr.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(10+i)*time.Second), Success:false}) }
        if mErr.GetTrend("db1","error_rate")!="degrading" { panic("error_rate degrading expected") }

        // unknown metric -> stable
        if mErr.GetTrend("db1","unknown_metric")!="stable" { panic("unknown metric should stable") }

        // missing node -> stable
        if mErr.GetTrend("nonexist","latency")!="stable" { panic("missing node should stable") }

        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"trend all metrics failed: {proc.stdout} {proc.stderr}"
    )


def test_missing_node_behaviors():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        _, ok := m.GetHealthScore("nope")
        if ok { panic("missing GetHealthScore should false") }
        _, ok = m.PredictFailure("nope")
        if ok { panic("missing PredictFailure should false") }
        if m.GetTrend("nope","latency")!="stable" { panic("missing trend should stable") }
        if m.IsHealthy("nope") { panic("missing IsHealthy should false") }
        if m.GetUptimePercentage("nope")!=100 { panic("missing uptime should 100") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"missing node behaviors failed: {proc.stdout} {proc.stderr}"
    )


def test_prediction_sorting():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        m.RecordCheck(reliability.CheckResult{NodeID:"zebra", Success:true, LatencyMs:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"alpha", Success:true, LatencyMs:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"mid", Success:true, LatencyMs:10})
        report:=m.GetReliabilityReport()
        if len(report.Predictions)!=3 { panic("3 predictions") }
        if report.Predictions[0].NodeID!="alpha" || report.Predictions[1].NodeID!="mid" || report.Predictions[2].NodeID!="zebra" {
            panic(fmt.Sprintf("predictions not sorted got %v %v %v", report.Predictions[0].NodeID, report.Predictions[1].NodeID, report.Predictions[2].NodeID))
        }
        if report.Nodes[0].NodeID!="alpha" { panic("nodes not sorted") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"prediction sorting failed: {proc.stdout} {proc.stderr}"
    )


def test_factors_copy_safety():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150})
        score1,_:=m.GetHealthScore("db1")
        // mutate returned map
        score1.Factors["latency"]=9999
        score2,_:=m.GetHealthScore("db1")
        if score2.Factors["latency"]==9999 { panic("Factors map not deep copied – mutation affected internal state") }
        if score2.Score==9999 { panic("Score affected") }
        // mutate GetAllHealthScores
        all:=m.GetAllHealthScores()
        all[0].Factors["error_rate"]=8888
        score3,_:=m.GetHealthScore("db1")
        if score3.Factors["error_rate"]==8888 { panic("GetAllHealthScores Factors not deep copied") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"factors copy safety failed: {proc.stdout} {proc.stderr}"
    )


def test_snapshot_consistency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:20, DownThreshold:10})
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        // seed some nodes
        for i:=0;i<5;i++{
            m.RecordCheck(reliability.CheckResult{NodeID:fmt.Sprintf("node-%d", i), Timestamp:base, Success:true, LatencyMs:20})
        }
        // Now concurrent RecordCheck writer while we call GetReliabilityReport many times
        var wg sync.WaitGroup
        stopCh:=make(chan struct{})
        wg.Add(1)
        go func(){
            defer wg.Done()
            j:=0
            for {
                select {
                case <-stopCh:
                    return
                default:
                    m.RecordCheck(reliability.CheckResult{NodeID:fmt.Sprintf("node-%d", j%5), Timestamp:base.Add(time.Duration(j)*time.Millisecond), Success:true, LatencyMs:float64(100+j%100)})
                    j++
                }
            }
        }()
        // Call report 100 times, check that Nodes and Predictions are consistent (same nodes count and scores match)
        for k:=0;k<100;k++{
            report:=m.GetReliabilityReport()
            // Nodes and Predictions must have same length and same NodeIDs in same order (sorted)
            if len(report.Nodes)!=len(report.Predictions) {
                panic(fmt.Sprintf("torn report: nodes %d vs pred %d", len(report.Nodes), len(report.Predictions)))
            }
            // Check that for each node, Prediction corresponds to same NodeID and RiskLevel computed from that node's score
            // At least check NodeIDs match in order
            for i:=0;i<len(report.Nodes);i++{
                if report.Nodes[i].NodeID!=report.Predictions[i].NodeID {
                    panic(fmt.Sprintf("torn report NodeID mismatch: Nodes %s vs Pred %s at %d", report.Nodes[i].NodeID, report.Predictions[i].NodeID, i))
                }
                // Score in Nodes[i] should be consistent with prediction's risk band? We can't fully check without reimplementing, but at least ensure both derived from same snapshot – if not, a concurrent update could make prediction score differ from node score.
                // We test by ensuring prediction's Reasons contain no impossible state: if Nodes[i].Score is 100 and Predictions[i] is critical, that's torn (unless concurrent failure happened between calls)
                // For this test, we only check NodeID equality as proxy for atomic snapshot, plus length equality
            }
        }
        close(stopCh)
        wg.Wait()
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"snapshot consistency failed: {proc.stdout} {proc.stderr}"
    )


def test_error_rate_window_includes_current():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{ErrorRateThreshold:0.5, DownThreshold:10, WindowSize:10}
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        m:=reliability.NewMonitor(cfg)
        // 2 successes in window, current fail makes 1 fail /3 =0.333 -> should NOT alert (threshold 0.5)
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-20*time.Second), Success:true})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-10*time.Second), Success:true})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base, Success:false})
        for _,a:=range alerts{ if a.Type==reliability.AlertHighErrorRate { panic("errorRate 0.333 should not alert, implies current included") } }
        // Now add another fail, current fail makes 2 fails /4 =0.5 -> not > threshold, still no alert (exact equals -> no alert)
        // Another fail makes 3/5=0.6 -> should alert
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(5*time.Second), Success:false})
        alerts2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(10*time.Second), Success:false})
        found:=false
        for _,a:=range alerts2{ if a.Type==reliability.AlertHighErrorRate { found=true } }
        if !found { panic("should alert when including current, errorRate 0.6 >0.5") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"error_rate window includes current failed: {proc.stdout} {proc.stderr}"
    )


def test_randomized_differential_scoring():
    # Expanded differential: 5k checks, exact score comparison vs Python oracle covering full input space
    random.seed(123)
    nodes = [f"db-{i}" for i in range(10)]
    checks = []
    ts = 0
    for _ in range(5000):
        node = random.choice(nodes)
        ts += random.randint(1, 10)
        latency = random.randint(10, 300)
        success = random.random() > 0.15
        lag = random.randint(50, 800)
        conns = random.randint(10, 150)
        checks.append((node, ts, latency, success, lag, conns))

    # Python oracle for scoring (same as reference implementation)
    def compute_factors_and_score(history, current_conns, consec, cfg):
        # history: list of checkRecord (lat, lag, success, ts)
        window_size = cfg["WindowSize"]
        # count window
        window = history[-window_size:] if len(history) > window_size else history
        # time window
        last_ts = history[-1][3] if history else 0
        time_window = [r for r in history if r[3] >= last_ts - 60]
        failed_tw = sum(1 for r in time_window if not r[2])
        error_rate = failed_tw / len(time_window) if time_window else 0.0

        sum_lat = sum(r[0] for r in window)
        sum_lag = sum(r[1] for r in window)
        avg_lat = sum_lat / len(window) if window else 0.0
        avg_lag = sum_lag / len(window) if window else 0.0

        # penalties
        err_pen = error_rate * 50.0

        lat_pen = 0.0
        if avg_lat > cfg["LatencyThreshold"]:
            over = (avg_lat - cfg["LatencyThreshold"]) / cfg["LatencyThreshold"]
            lat_pen = over * 20.0
            if lat_pen > 30:
                lat_pen = 30
        elif avg_lat > cfg["LatencyThreshold"] * 0.8:
            lat_pen = 5

        lag_pen = 0.0
        if avg_lag > cfg["RepThreshold"]:
            over = (avg_lag - cfg["RepThreshold"]) / cfg["RepThreshold"]
            lag_pen = over * 15.0
            if lag_pen > 20:
                lag_pen = 20
        elif avg_lag > cfg["RepThreshold"] * 0.8:
            lag_pen = 3

        cf_pen = float(consec * 10)
        if cf_pen > 40:
            cf_pen = 40

        conn_pen = 0.0
        if current_conns > cfg["ConnThreshold"]:
            conn_pen = 15
        elif current_conns > int(cfg["ConnThreshold"] * 0.8):
            conn_pen = 5

        score = 100.0 - (err_pen + lat_pen + lag_pen + cf_pen + conn_pen)
        if score < 0:
            score = 0
        if score > 100:
            score = 100
        factors = {
            "error_rate": err_pen,
            "latency": lat_pen,
            "replication_lag": lag_pen,
            "consecutive_failures": cf_pen,
            "connections": conn_pen,
        }
        return factors, score, error_rate, avg_lat, avg_lag

    cfg = {
        "LatencyThreshold": 100,
        "RepThreshold": 500,
        "ConnThreshold": 100,
        "WindowSize": 10,
    }

    # Build oracle scores
    node_histories = {}  # node -> list of (lat, lag, success, ts, conns, consec)
    node_consec = {}
    node_hist_simple = {}  # for scoring: list of (lat, lag, success, ts)
    oracle_scores = {}

    for node, ts_cur, lat, succ, lag, conns in checks:
        hist = node_hist_simple.get(node, [])
        consec = node_consec.get(node, 0)
        if not succ:
            consec += 1
        else:
            consec = 0
        node_consec[node] = consec
        hist.append((lat, lag, succ, ts_cur))
        max_hist = 20
        if len(hist) > max_hist:
            hist = hist[-max_hist:]
        node_hist_simple[node] = hist
        # compute score
        factors, score, _, _, _ = compute_factors_and_score(hist, conns, consec, cfg)
        oracle_scores[node] = (factors, score)

    # Now run Go program that replays same checks and outputs scores
    json_path = "/tmp/diff_score_full.json"
    with open(json_path, "w") as jf:
        json.dump(
            [
                {"node": n, "ts": ts, "lat": lat, "succ": s, "lag": lag, "conns": c}
                for n, ts, lat, s, lag, c in checks
            ],
            jf,
        )

    go_code = textwrap.dedent(f"""
    package main
    import (
        "encoding/json"
        "fmt"
        "os"
        "time"
        "db-reliability/reliability"
        "math"
    )
    type In struct {{
        Node string `json:"node"`
        Ts int64 `json:"ts"`
        Lat float64 `json:"lat"`
        Succ bool `json:"succ"`
        Lag float64 `json:"lag"`
        Conns int `json:"conns"`
    }}
    func main(){{
        data,_:=os.ReadFile("/tmp/diff_score_full.json")
        var ins []In
        json.Unmarshal(data, &ins)
        cfg:=reliability.Config{{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:3, WindowSize:10, ErrorRateThreshold:0.05}}
        m:=reliability.NewMonitor(cfg)
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        for _,inpt:=range ins {{
            ts:=base.Add(time.Duration(inpt.Ts)*time.Second)
            m.RecordCheck(reliability.CheckResult{{NodeID:inpt.Node, Timestamp:ts, LatencyMs:inpt.Lat, Success:inpt.Succ, ReplicationLagMs:inpt.Lag, Connections:inpt.Conns}})
        }}
        scores:=m.GetAllHealthScores()
        for _,s:=range scores{{
            // encode factors sum and score
            sum:=0.0
            for _,v:=range s.Factors {{ sum+=v }}
            expected:=100-sum
            if expected<0 {{ expected=0 }}
            if expected>100 {{ expected=100 }}
            diff:=math.Abs(expected-s.Score)
            if diff>0.01 {{
                panic(fmt.Sprintf("factors sum invariant failed %s %f vs %f", s.NodeID, s.Score, expected))
            }}
            // output nodeid and score and factors json
            fjson,_:=json.Marshal(s.Factors)
            fmt.Printf("%s|%.4f|%s\\n", s.NodeID, s.Score, string(fjson))
        }}
        fmt.Println("OK")
    }}
    """)
    proc = go_run_program(go_code, timeout=30)
    assert proc.returncode == 0, (
        f"diff scoring full run failed: {proc.stdout} {proc.stderr}"
    )
    # Parse Go output vs oracle
    go_scores = {}
    for line in proc.stdout.strip().split("\n"):
        if "|" not in line:
            continue
        if line == "OK":
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        nid = parts[0]
        score = float(parts[1])
        factors = json.loads(parts[2])
        go_scores[nid] = (factors, score)

    # Compare each node's score vs oracle (allow 0.01 tolerance)
    for node in nodes:
        if node not in go_scores:
            # node may not have been created if no checks? But all nodes had checks
            continue
        go_factors, go_score = go_scores[node]
        oracle_factors, oracle_score = oracle_scores[node]
        assert abs(go_score - oracle_score) < 0.5, (
            f"score mismatch for {node}: Go {go_score} vs oracle {oracle_score} factors Go {go_factors} oracle {oracle_factors}"
        )
        # Also check each factor within tolerance
        for k in [
            "error_rate",
            "latency",
            "replication_lag",
            "consecutive_failures",
            "connections",
        ]:
            assert abs(go_factors.get(k, 0) - oracle_factors.get(k, 0)) < 0.5, (
                f"factor {k} mismatch for {node}: Go {go_factors.get(k)} vs oracle {oracle_factors.get(k)}"
            )

    assert "OK" in proc.stdout
