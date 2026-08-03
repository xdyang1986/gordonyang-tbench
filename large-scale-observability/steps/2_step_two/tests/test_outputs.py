"""
Step2 verifier HARDENED: ~80 tests for large-scale hardening
"""

import os, subprocess, tempfile, re, textwrap, shutil, time
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


def go_run_program(go_code: str, timeout=30):
    tmp = tempfile.mkdtemp(prefix="obs_test2_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_run_with_race(go_code: str, timeout=30):
    tmp = tempfile.mkdtemp(prefix="obs_test2_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "-race", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_files_exist_step2():
    assert os.path.isdir(os.path.join(APP_DIR, "observability"))
    for fname in ["tracing.go", "metrics.go", "logger.go"]:
        assert os.path.isfile(os.path.join(APP_DIR, "observability", fname))


def test_go_build_vet_step2():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


def test_sampler_always():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        on := observability.NewAlwaysOnSampler()
        off := observability.NewAlwaysOffSampler()
        if on.Description()=="" { panic("on desc empty") }
        if off.Description()=="" { panic("off desc empty") }
        p := observability.SamplingParameters{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"test"}
        if on.ShouldSample(p)!=observability.DecisionRecordAndSample { panic("AlwaysOn should sample") }
        if off.ShouldSample(p)!=observability.DecisionDrop { panic("AlwaysOff should drop") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"always sampler failed: {proc.stdout} {proc.stderr}"


def test_sampler_ratio_boundaries():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        zero := observability.NewTraceIDRatioSampler(0.0)
        one := observability.NewTraceIDRatioSampler(1.0)
        half := observability.NewTraceIDRatioSampler(0.5)
        p := observability.SamplingParameters{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"s"}
        if zero.ShouldSample(p)!=observability.DecisionDrop { panic("ratio 0 drop") }
        if one.ShouldSample(p)!=observability.DecisionRecordAndSample { panic("ratio 1 sample") }
        neg := observability.NewTraceIDRatioSampler(-0.1)
        over := observability.NewTraceIDRatioSampler(1.5)
        if neg.ShouldSample(p)!=observability.DecisionDrop { panic("neg drop") }
        if over.ShouldSample(p)!=observability.DecisionRecordAndSample { panic(">1 sample") }
        _ = half
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ratio boundaries failed: {proc.stdout} {proc.stderr}"


def test_sampler_ratio_statistical():
    code = textwrap.dedent("""
    package main
    import (
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randTraceID() string {
        b := make([]byte, 16)
        rand.Read(b)
        return hex.EncodeToString(b)
    }
    func main(){
        sampler := observability.NewTraceIDRatioSampler(0.1)
        n:=10000
        sampled:=0
        for i:=0;i<n;i++{
            tid := randTraceID()
            p := observability.SamplingParameters{TraceID:tid, SpanName:"test"}
            if sampler.ShouldSample(p)==observability.DecisionRecordAndSample { sampled++ }
        }
        ratio := float64(sampled)/float64(n)
        fmt.Printf("sampled %d/%d ratio %f\\n", sampled, n, ratio)
        if ratio < 0.05 || ratio > 0.15 {
            panic(fmt.Sprintf("ratio 0.1 expected ~0.1, got %f", ratio))
        }
        tid := "0102030405060708090a0b0c0d0e0f10"
        p1 := observability.SamplingParameters{TraceID:tid, SpanName:"a"}
        d1 := sampler.ShouldSample(p1)
        d2 := sampler.ShouldSample(p1)
        if d1!=d2 { panic("not deterministic") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"ratio statistical failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_ratio_half():
    code = textwrap.dedent("""
    package main
    import (
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randTraceID() string {
        b := make([]byte, 16)
        rand.Read(b)
        return hex.EncodeToString(b)
    }
    func main(){
        sampler := observability.NewTraceIDRatioSampler(0.5)
        n:=10000
        sampled:=0
        for i:=0;i<n;i++{
            tid := randTraceID()
            p := observability.SamplingParameters{TraceID:tid, SpanName:"t"}
            if sampler.ShouldSample(p)==observability.DecisionRecordAndSample { sampled++ }
        }
        ratio := float64(sampled)/float64(n)
        if ratio < 0.4 || ratio > 0.6 { panic(fmt.Sprintf("expected ~0.5 got %f", ratio)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ratio half failed: {proc.stdout} {proc.stderr}"


def test_sampler_determinism_prefix():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewTraceIDRatioSampler(0.5)
        // same prefix, different suffix should give same decision if impl uses first 16 chars
        base := "0102030405060708"
        tid1 := base + "0102030405060708"
        tid2 := base + "aabbccddeeff0011"
        tid3 := base + "ffffffffffffffff"
        p1 := observability.SamplingParameters{TraceID:tid1, SpanName:"test"}
        p2 := observability.SamplingParameters{TraceID:tid2, SpanName:"test"}
        p3 := observability.SamplingParameters{TraceID:tid3, SpanName:"test"}
        d1 := sampler.ShouldSample(p1)
        d2 := sampler.ShouldSample(p2)
        d3 := sampler.ShouldSample(p3)
        if d1!=d2 || d1!=d3 {
            panic(fmt.Sprintf("sampler should be deterministic on prefix, got %d %d %d", d1, d2, d3))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"determinism prefix failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_invalid_traceid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewTraceIDRatioSampler(0.5)
        invalids := []string{"", "short", "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "0102030405060708090a0b0c0d0e0f0g"}
        for _, tid := range invalids {
            p := observability.SamplingParameters{TraceID:tid, SpanName:"test"}
            // should not panic, and should return Drop for invalid (or at least not panic)
            func(){
                defer func(){
                    if r:=recover(); r!=nil { panic(fmt.Sprintf("panic on invalid traceID %s: %v", tid, r)) }
                }()
                d := sampler.ShouldSample(p)
                // for invalid, we expect Drop
                if d!=observability.DecisionDrop && d!=observability.DecisionRecordAndSample {
                    panic("unexpected decision")
                }
                // For our stricter test, invalid should be Drop
                if tid=="" || len(tid)<16 {
                    if d!=observability.DecisionDrop { panic(fmt.Sprintf("invalid tid %s should Drop, got %d", tid, d)) }
                }
            }()
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"invalid traceid failed: {proc.stdout} {proc.stderr}"


def test_sampler_parent_based():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        rootOn := observability.NewAlwaysOnSampler()
        rootOff := observability.NewAlwaysOffSampler()
        pbOn := observability.NewParentBasedSampler(rootOn)
        pbOff := observability.NewParentBasedSampler(rootOff)

        pNoParent := observability.SamplingParameters{TraceID:"0102030405060708090a0b0c0d0e0f10", HasParent:false}
        if pbOn.ShouldSample(pNoParent)!=observability.DecisionRecordAndSample { panic("pbOn no-parent should sample") }
        if pbOff.ShouldSample(pNoParent)!=observability.DecisionDrop { panic("pbOff no-parent should drop") }

        parentSampled := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        pSampledParent := observability.SamplingParameters{TraceID:parentSampled.TraceID, HasParent:true, ParentContext:parentSampled}
        if pbOff.ShouldSample(pSampledParent)!=observability.DecisionRecordAndSample { panic("sampled parent should sample even if root off") }

        parentNotSampled := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false}
        pNotSampledParent := observability.SamplingParameters{TraceID:parentNotSampled.TraceID, HasParent:true, ParentContext:parentNotSampled}
        if pbOn.ShouldSample(pNotSampledParent)!=observability.DecisionDrop { panic("not sampled parent should drop") }

        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent based failed: {proc.stdout} {proc.stderr}"


def test_sampler_parent_nested():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        root := observability.NewTraceIDRatioSampler(0.0) // always drop unless parent sampled
        pb := observability.NewParentBasedSampler(root)
        // chain: grandparent sampled true, parent sampled true, child should sample
        gpSampled := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0000000000000001", Sampled:true}
        // parent context sampled true
        parentSampled := observability.SpanContext{TraceID:gpSampled.TraceID, SpanID:"0000000000000002", Sampled:true}
        pChild := observability.SamplingParameters{TraceID:parentSampled.TraceID, HasParent:true, ParentContext:parentSampled}
        if pb.ShouldSample(pChild)!=observability.DecisionRecordAndSample { panic("nested sampled should sample") }

        // grandparent not sampled -> parent not sampled -> child not sampled
        gpNotSampled := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0000000000000001", Sampled:false}
        parentNotSampled := observability.SpanContext{TraceID:gpNotSampled.TraceID, SpanID:"0000000000000002", Sampled:false}
        pChild2 := observability.SamplingParameters{TraceID:parentNotSampled.TraceID, HasParent:true, ParentContext:parentNotSampled}
        if pb.ShouldSample(pChild2)!=observability.DecisionDrop { panic("nested not sampled should drop") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent nested failed: {proc.stdout} {proc.stderr}"


def test_sampler_parent_description_contains_root():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        root := observability.NewTraceIDRatioSampler(0.5)
        pb := observability.NewParentBasedSampler(root)
        desc := pb.Description()
        rootDesc := root.Description()
        if !strings.Contains(desc, rootDesc) && !strings.Contains(strings.ToLower(desc), "parent") {
            panic(fmt.Sprintf("ParentBased description should mention root or parent, got %s root %s", desc, rootDesc))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent desc failed: {proc.stdout} {proc.stderr}"


def test_tracer_with_sampler_integration():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        sampler := observability.NewAlwaysOffSampler()
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc), observability.WithSampler(sampler))
        _, span := tracer.Start(context.Background(), "should-drop")
        if span.IsRecording() { panic("should not be recording") }
        span.End()
        if len(exp.GetSpans())!=0 { panic(fmt.Sprintf("expected 0 got %d", len(exp.GetSpans()))) }

        exp2 := observability.NewInMemoryExporter()
        proc2 := observability.NewSimpleSpanProcessor(exp2)
        tracer2 := observability.NewTracer("svc", observability.WithSpanProcessor(proc2), observability.WithSampler(observability.NewAlwaysOnSampler()))
        _, s2 := tracer2.Start(context.Background(), "should-sample")
        s2.End()
        if len(exp2.GetSpans())!=1 { panic("expected 1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"tracer sampler integration failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_parent_propagation_via_tracer():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        root := observability.NewTraceIDRatioSampler(0.5)
        pb := observability.NewParentBasedSampler(root)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc), observability.WithSampler(pb))

        parentSC := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithSpanContext(context.Background(), parentSC)
        _, child := tracer.Start(ctx, "child")
        if !child.IsRecording() { panic("child of sampled parent should be recording") }
        child.End()

        parentSC2 := observability.SpanContext{TraceID:"0202030405060708090a0b0c0d0e0f10", SpanID:"0202030405060708", Sampled:false}
        ctx3 := observability.ContextWithSpanContext(context.Background(), parentSC2)
        _, child2 := tracer.Start(ctx3, "child2")
        if child2.IsRecording() { panic("child of not-sampled parent should NOT be recording") }
        child2.End()

        if len(exp.GetSpans())!=1 { panic(fmt.Sprintf("expected 1 got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent propagation via tracer failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_inject_extract_preserves_parent_id():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentSpanID:"0a0b0c0d0e0f0a0b", Sampled:true}
        ctx := observability.ContextWithSpanContext(context.Background(), sc)
        carrier := map[string]string{}
        observability.Inject(ctx, carrier)
        if carrier["parent-id"]!="0a0b0c0d0e0f0a0b" { panic(fmt.Sprintf("parent-id not preserved, got %s", carrier["parent-id"])) }
        ctx2 := observability.Extract(carrier)
        sc2, _ := observability.SpanContextFromContext(ctx2)
        if sc2.ParentSpanID != sc.ParentSpanID { panic("parent-id mismatch after extract") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"inject extract parent-id failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_processor_basic():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithBatchSize(5), observability.WithQueueSize(100), observability.WithBatchTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<10;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d", i))
            span.End()
        }
        proc.ForceFlush(context.Background())
        spans := exp.GetSpans()
        if len(spans)!=10 { panic(fmt.Sprintf("expected 10 got %d", len(spans))) }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch basic failed: {proc.stdout} {proc.stderr}"


def test_batch_timeout_trigger():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithBatchSize(100), observability.WithQueueSize(1000), observability.WithBatchTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<10;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        // Don't call ForceFlush, wait for batch timeout
        time.Sleep(400*time.Millisecond)
        count := len(exp.GetSpans())
        fmt.Printf("exported after timeout %d\\n", count)
        if count < 10 { panic(fmt.Sprintf("batch timeout should have exported 10, got %d", count)) }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch timeout trigger failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_batch_size_limit():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    type countingExporter struct {
        mu sync.Mutex
        batches [][]observability.ReadableSpan
        maxBatch int
    }
    func (c *countingExporter) ExportSpans(ctx context.Context, spans []observability.ReadableSpan) error {
        c.mu.Lock()
        defer c.mu.Unlock()
        c.batches = append(c.batches, spans)
        if len(spans) > c.maxBatch { c.maxBatch = len(spans) }
        return nil
    }
    func main(){
        exporter := &countingExporter{}
        proc := observability.NewBatchSpanProcessor(exporter, observability.WithBatchSize(5), observability.WithQueueSize(1000))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<20;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        fmt.Printf("maxBatch %d batches %d\\n", exporter.maxBatch, len(exporter.batches))
        if exporter.maxBatch > 5 { panic(fmt.Sprintf("batch size limit violated: max %d > 5", exporter.maxBatch)) }
        total := 0
        for _, b := range exporter.batches { total+=len(b) }
        if total!=20 { panic(fmt.Sprintf("total expected 20 got %d", total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch size limit failed: {proc.stdout} {proc.stderr}"


def test_batch_processor_drop_and_backpressure():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(10), observability.WithBatchSize(20), observability.WithBatchTimeout(5*time.Second))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<100;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            span.End()
        }
        time.Sleep(200*time.Millisecond)
        proc.ForceFlush(context.Background())
        exported := len(exp.GetSpans())
        fmt.Printf("exported %d out of 100\\n", exported)
        start := time.Now()
        for i:=0;i<50;i++{
            _, span := tracer.Start(context.Background(), "fast")
            span.End()
        }
        elapsed := time.Since(start)
        fmt.Printf("fast enqueue 50 took %v\\n", elapsed)
        if elapsed > 500*time.Millisecond {
            panic(fmt.Sprintf("enqueue blocking too long %v", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch drop backpressure failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_droppedcount_and_queuelen():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(5), observability.WithBatchSize(100))
        // Access DroppedCount and QueueLen via type assertion if method exists
        // Use interface that includes those methods
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<20;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        // Try to get dropped count via type that has method
        if b, ok := proc.(interface{ DroppedCount() int }); ok {
            dc := b.DroppedCount()
            fmt.Printf("dropped %d\\n", dc)
            if dc < 0 { panic("negative dropped") }
        } else {
            panic("BatchSpanProcessor should have DroppedCount method")
        }
        if b, ok := proc.(interface{ QueueLen() int }); ok {
            ql := b.QueueLen()
            fmt.Printf("queueLen %d\\n", ql)
            if ql <0 || ql>5 { panic(fmt.Sprintf("queueLen out of expected 0-5 got %d", ql)) }
        } else {
            panic("BatchSpanProcessor should have QueueLen method")
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"droppedcount queuelen failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_shutdown_drops_new():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(100))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        _, s := tracer.Start(context.Background(), "before-shutdown")
        s.End()
        proc.Shutdown(context.Background())
        countAfterShutdown := len(exp.GetSpans())
        // try to add after shutdown, should not panic and should be dropped
        _, s2 := tracer.Start(context.Background(), "after-shutdown")
        s2.End()
        // give time
        // After shutdown, new spans should be dropped, so count should stay same
        // ForceFlush after shutdown should still work or return?
        // Our impl: OnEnd checks stopped flag and drops
        if len(exp.GetSpans()) != countAfterShutdown {
            panic(fmt.Sprintf("spans after shutdown should be dropped, before %d after %d", countAfterShutdown, len(exp.GetSpans())))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"shutdown drops new failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_forceflush_timeout():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    type slowExporter struct {
        sleep time.Duration
    }
    func (s *slowExporter) ExportSpans(ctx context.Context, spans []observability.ReadableSpan) error {
        time.Sleep(s.sleep)
        return nil
    }
    func main(){
        slow := &slowExporter{sleep: 500*time.Millisecond}
        proc := observability.NewBatchSpanProcessor(slow, observability.WithQueueSize(100), observability.WithBatchSize(10), observability.WithExportTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<5;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        // ForceFlush with short timeout should respect context
        ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
        defer cancel()
        start := time.Now()
        err := proc.ForceFlush(ctx)
        elapsed := time.Since(start)
        fmt.Printf("ForceFlush elapsed %v err %v\\n", elapsed, err)
        // Should return within ~300ms even though exporter slow
        if elapsed > 800*time.Millisecond {
            panic(fmt.Sprintf("ForceFlush took too long %v", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"forceflush timeout failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_export_timeout_respects():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    type slowExporter struct {
        sleep time.Duration
    }
    func (s *slowExporter) ExportSpans(ctx context.Context, spans []observability.ReadableSpan) error {
        // respects ctx?
        select {
        case <-time.After(s.sleep):
            return nil
        case <-ctx.Done():
            return ctx.Err()
        }
    }
    func main(){
        slow := &slowExporter{sleep: 500*time.Millisecond}
        proc := observability.NewBatchSpanProcessor(slow, observability.WithQueueSize(100), observability.WithBatchSize(5), observability.WithExportTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        _, s := tracer.Start(context.Background(), "slow")
        s.End()
        start := time.Now()
        proc.ForceFlush(context.Background())
        elapsed := time.Since(start)
        fmt.Printf("ForceFlush with slow exporter respecting timeout elapsed %v\\n", elapsed)
        // With export timeout 100ms, ForceFlush should finish within ~300ms, not 500ms*? Actually our exportWithTimeout does goroutine select, so should timeout quickly
        if elapsed > 600*time.Millisecond {
            panic(fmt.Sprintf("export timeout not respected, elapsed %v", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"export timeout respects failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_processor_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(256), observability.WithBatchTimeout(50*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        var wg sync.WaitGroup
        n:=100
        per:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<per;j++{
                    _, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d-%d", idx, j))
                    span.End()
                }
            }(i)
        }
        wg.Wait()
        proc.ForceFlush(context.Background())
        time.Sleep(100*time.Millisecond)
        proc.Shutdown(context.Background())
        spans := exp.GetSpans()
        expected := n*per
        if len(spans)!=expected {
            panic(fmt.Sprintf("expected %d got %d", expected, len(spans)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch concurrent failed: {proc.stdout} {proc.stderr}"


def test_batch_processor_shutdown_flush():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(1000), observability.WithBatchSize(100))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<250;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s%d", i))
            s.End()
        }
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!=250 { panic(fmt.Sprintf("shutdown flush expected 250 got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"shutdown flush failed: {proc.stdout} {proc.stderr}"


def test_batch_with_sampler():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        batch := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(512))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(batch), observability.WithSampler(observability.NewTraceIDRatioSampler(0.1)))
        n:=5000
        for i:=0;i<n;i++{
            _, s := tracer.Start(context.Background(), "op")
            s.End()
        }
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        exported := len(exp.GetSpans())
        fmt.Printf("n=%d exported=%d\\n", n, exported)
        if exported < 250 || exported > 750 {
            panic(fmt.Sprintf("expected ~500 got %d", exported))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch with sampler failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_concurrent_different_tracers():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(128))
        var wg sync.WaitGroup
        n:=10
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                tracer := observability.NewTracer(fmt.Sprintf("svc-%d", idx), observability.WithSpanProcessor(proc))
                for j:=0;j<100;j++{
                    _, s := tracer.Start(context.Background(), "op")
                    s.End()
                }
            }(i)
        }
        wg.Wait()
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!=n*100 { panic(fmt.Sprintf("expected %d got %d", n*100, len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch concurrent different tracers failed: {proc.stdout} {proc.stderr}"
    )


# metrics cardinality


def test_metrics_cardinality_limit_drop():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(2))
        c1 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"id":"1"}))
        c2 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"id":"2"}))
        c3 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"id":"3"}))
        c1.Inc()
        c2.Inc()
        c3.Inc()
        fams := prov.Collect()
        var count int
        for _, fam := range fams {
            if fam.Name=="my_counter" { count = len(fam.Metrics) }
        }
        if count>2 { panic(fmt.Sprintf("expected <=2 metrics got %d", count)) }
        dropped := prov.DroppedSeriesCount()
        if dropped <1 { panic(fmt.Sprintf("expected dropped >=1 got %d", dropped)) }
        c1b := prov.Counter("my_counter", observability.WithLabels(map[string]string{"id":"1"}))
        c1b.Inc()
        if prov.DroppedSeriesCount()!=dropped { panic("reuse should not increase dropped") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"cardinality drop failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_per_name():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(2))
        // metric a: 2 distinct ok, 3rd dropped
        // metric b: also 2 distinct ok, independent
        for i:=0;i<3;i++{
            c := prov.Counter("metric_a", observability.WithLabels(map[string]string{"id": fmt.Sprintf("%d", i)}))
            c.Inc()
        }
        for i:=0;i<3;i++{
            c := prov.Counter("metric_b", observability.WithLabels(map[string]string{"id": fmt.Sprintf("%d", i)}))
            c.Inc()
        }
        fams := prov.Collect()
        var countA, countB int
        for _, fam := range fams {
            if fam.Name=="metric_a" { countA=len(fam.Metrics) }
            if fam.Name=="metric_b" { countB=len(fam.Metrics) }
        }
        if countA!=2 || countB!=2 { panic(fmt.Sprintf("per-name limit broken: a=%d b=%d", countA, countB)) }
        if prov.DroppedSeriesCount()!=2 { panic(fmt.Sprintf("expected dropped 2 got %d", prov.DroppedSeriesCount())) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cardinality per name failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_cardinality_aggregate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(2), observability.WithCardinalityOverflowHandling("aggregate"))
        c1 := prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"1"}))
        c2 := prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"2"}))
        c3 := prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"3"}))
        c4 := prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"4"}))
        c1.Inc()
        c2.Inc()
        c3.Inc()
        c3.Inc()
        c4.Add(5)
        fams := prov.Collect()
        var count int
        var overflowVal float64
        for _, fam := range fams {
            if fam.Name=="agg_counter" {
                count = len(fam.Metrics)
                for _, m := range fam.Metrics {
                    if m.Labels["__overflow__"]=="true" {
                        overflowVal = m.Value
                    }
                }
            }
        }
        fmt.Printf("count %d overflowVal %f\\n", count, overflowVal)
        if count!=3 { panic(fmt.Sprintf("expected 3 metrics (2 normal + 1 overflow) got %d", count)) }
        if overflowVal != 7 { panic(fmt.Sprintf("overflow should aggregate 2+5=7 got %f", overflowVal)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"aggregate failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_mixed_types():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(1))
        c1 := prov.Counter("mixed", observability.WithLabels(map[string]string{"id":"1"}))
        c2 := prov.Counter("mixed", observability.WithLabels(map[string]string{"id":"2"}))
        // type conflict case: gauge with same name should be noop
        _ = prov.Gauge("mixed", observability.WithLabels(map[string]string{"id":"1"}))
        c1.Inc()
        c2.Inc()
        // gauge different name
        g2 := prov.Gauge("mixed_gauge", observability.WithLabels(map[string]string{"id":"1"}))
        g3 := prov.Gauge("mixed_gauge", observability.WithLabels(map[string]string{"id":"2"}))
        g2.Set(1)
        g3.Set(2)
        fams := prov.Collect()
        var counterCount, gaugeCount int
        for _, fam := range fams {
            if fam.Name=="mixed" { counterCount=len(fam.Metrics) }
            if fam.Name=="mixed_gauge" { gaugeCount=len(fam.Metrics) }
        }
        if counterCount!=1 { panic(fmt.Sprintf("counter mixed expected 1 got %d", counterCount)) }
        if gaugeCount!=1 { panic(fmt.Sprintf("gauge mixed expected 1 got %d", gaugeCount)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"mixed types failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_unlimited():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        for i:=0;i<200;i++{
            c := prov.Counter("counter", observability.WithLabels(map[string]string{"i": fmt.Sprintf("%d", i)}))
            c.Inc()
        }
        fams := prov.Collect()
        var cnt int
        for _, fam := range fams {
            if fam.Name=="counter" { cnt=len(fam.Metrics) }
        }
        if cnt!=200 { panic(fmt.Sprintf("expected 200 got %d", cnt)) }
        if prov.DroppedSeriesCount()!=0 { panic("dropped should be 0 unlimited") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cardinality unlimited failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_cardinality_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(10000))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<100;j++{
                    label := fmt.Sprintf("%d-%d", idx, j)
                    c := prov.Counter("conc", observability.WithLabels(map[string]string{"id":label}))
                    c.Inc()
                }
            }(i)
        }
        wg.Wait()
        fams := prov.Collect()
        var cnt int
        for _, fam := range fams {
            if fam.Name=="conc" { cnt=len(fam.Metrics) }
        }
        if cnt!=100*100 { panic(fmt.Sprintf("expected 10000 got %d", cnt)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cardinality concurrent failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_collect_immutability_under_concurrency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("immut")
        var wg sync.WaitGroup
        wg.Add(2)
        go func(){
            defer wg.Done()
            for i:=0;i<1000;i++{
                c.Inc()
            }
        }()
        go func(){
            defer wg.Done()
            for i:=0;i<100;i++{
                fams := prov.Collect()
                for _, fam := range fams {
                    for _, m := range fam.Metrics {
                        _ = m.Value
                        _ = m.Labels
                    }
                }
            }
        }()
        wg.Wait()
        fams := prov.Collect()
        var total float64
        for _, fam := range fams {
            if fam.Name=="immut" {
                for _, m := range fam.Metrics { total+=m.Value }
            }
        }
        if total!=1000 { panic(fmt.Sprintf("expected 1000 got %f", total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"collect immutability failed: {proc.stdout} {proc.stderr}"
    )


def test_span_resource_limits():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        _, span := tracer.Start(context.Background(), "limits")
        long := strings.Repeat("a", 2000)
        span.AddAttribute("long", long)
        for i:=0;i<200;i++{
            span.AddEvent(fmt.Sprintf("ev-%d", i))
        }
        span.End()
        spans := exp.GetSpans()
        s := spans[0]
        if v, ok := s.Attributes["long"]; ok {
            if str, ok := v.(string); ok {
                if len(str) != 1024 { panic(fmt.Sprintf("expected truncated to exactly 1024 got %d", len(str))) }
            }
        }
        if len(s.Events) != 128 {
            panic(fmt.Sprintf("event limit expected exactly 128 got %d", len(s.Events)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"resource limits failed: {proc.stdout} {proc.stderr}"


def test_span_attribute_initial_limit_withattributes():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        var attrs []observability.Attribute
        for i:=0;i<200;i++{
            attrs = append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value: i})
        }
        _, span := tracer.Start(context.Background(), "initial", observability.WithAttributes(attrs...))
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes) != 128 { panic(fmt.Sprintf("initial attrs should be capped at 128 got %d", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"initial attr limit failed: {proc.stdout} {proc.stderr}"
    )


def test_span_attribute_truncate_exact_1024():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        long := strings.Repeat("x", 2000)
        _, span := tracer.Start(context.Background(), "truncate", observability.WithAttributes(observability.Attribute{Key:"long", Value:long}))
        span.End()
        s := exp.GetSpans()[0]
        v := s.Attributes["long"].(string)
        if len(v) != 1024 { panic(fmt.Sprintf("expected 1024 got %d", len(v))) }
        if v != long[:1024] { panic("truncation mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"truncate exact failed: {proc.stdout} {proc.stderr}"


def test_backward_compat_tracing():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("ride", observability.WithSpanProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "op", observability.WithAttributes(observability.Attribute{Key:"k", Value:"v"}))
        span.AddAttribute("k2", 1)
        span.AddEvent("e")
        span.SetStatus(observability.StatusOK, "")
        _ = ctx
        span.End()
        if len(exp.GetSpans())!=1 { panic("backward compat fail") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"backward compat tracing failed: {proc.stdout} {proc.stderr}"
    )


def test_backward_compat_metrics_logger():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("backward")
        c.Inc()
        if len(prov.Collect())==0 { panic("metrics backward fail") }
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "msg")
        if buf.Len()==0 { panic("logger backward fail") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"backward compat metrics/logger failed: {proc.stdout} {proc.stderr}"
    )


def test_high_throughput_tracing_simulation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        batch := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(50000), observability.WithBatchSize(1024))
        tracer := observability.NewTracer("ride-service", observability.WithSpanProcessor(batch), observability.WithSampler(observability.NewTraceIDRatioSampler(0.2)))
        var wg sync.WaitGroup
        rides:=1000
        wg.Add(rides)
        for i:=0;i<rides;i++{
            go func(idx int){
                defer wg.Done()
                ctx, span := tracer.Start(context.Background(), "RequestRide")
                span.AddAttribute("rider_id", fmt.Sprintf("rider-%d", idx))
                _, child := tracer.Start(ctx, "MatchDriver")
                child.End()
                _, child2 := tracer.Start(ctx, "CompleteTrip")
                child2.End()
                span.End()
            }(i)
        }
        wg.Wait()
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        exported := len(exp.GetSpans())
        fmt.Printf("rides %d exported %d\\n", rides, exported)
        if exported < 300 || exported > 900 {
            panic(fmt.Sprintf("throughput simulation out of range: %d", exported))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"high throughput simulation failed: {proc.stdout} {proc.stderr}"
    )


def test_high_throughput_10k():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        batch := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(100000), observability.WithBatchSize(1024), observability.WithBatchTimeout(50*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(batch), observability.WithSampler(observability.NewAlwaysOnSampler()))
        var wg sync.WaitGroup
        n:=100
        per:=100
        wg.Add(n)
        start := time.Now()
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<per;j++{
                    _, s := tracer.Start(context.Background(), fmt.Sprintf("op-%d-%d", idx, j))
                    s.End()
                }
            }(i)
        }
        wg.Wait()
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        elapsed := time.Since(start)
        fmt.Printf("10k spans in %v exported %d\\n", elapsed, len(exp.GetSpans()))
        if len(exp.GetSpans())!=n*per { panic(fmt.Sprintf("expected %d got %d", n*per, len(exp.GetSpans()))) }
        if elapsed > 5*time.Second { panic(fmt.Sprintf("10k throughput too slow: %v", elapsed)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"10k throughput failed: {proc.stdout} {proc.stderr}"


def test_tracing_service_name_in_batch():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        batch := observability.NewBatchSpanProcessor(exp, observability.WithBatchSize(2))
        tracer := observability.NewTracer("my-service", observability.WithSpanProcessor(batch))
        _, s := tracer.Start(context.Background(), "op")
        s.End()
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        spans := exp.GetSpans()
        if spans[0].ServiceName != "my-service" { panic("service name not preserved in batch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"service name batch failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_simple_processor_shutdown_multiple():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        // multiple shutdowns should not panic
        proc.Shutdown(context.Background())
        proc.Shutdown(context.Background())
        proc.ForceFlush(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"simple processor multiple shutdown failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_processor_shutdown_multiple():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp)
        proc.Shutdown(context.Background())
        proc.Shutdown(context.Background())
        proc.ForceFlush(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch multiple shutdown failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_race_with_sampler():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        batch := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(50000), observability.WithBatchSize(256))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(batch), observability.WithSampler(observability.NewParentBasedSampler(observability.NewTraceIDRatioSampler(0.5))))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                ctx, s := tracer.Start(context.Background(), "root")
                for j:=0;j<10;j++{
                    _, child := tracer.Start(ctx, fmt.Sprintf("child-%d", j))
                    child.End()
                }
                s.End()
            }(i)
        }
        wg.Wait()
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        fmt.Printf("exported %d\\n", len(exp.GetSpans()))
        fmt.Println("OK")
    }
    """)
    proc = go_run_with_race(code)
    assert proc.returncode == 0, (
        f"race with sampler failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_ratio_small_large():
    code=textwrap.dedent("""
    package main
    import (
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randID() string { b:=make([]byte,16); rand.Read(b); return hex.EncodeToString(b) }
    func main(){
        small := observability.NewTraceIDRatioSampler(0.01)
        large := observability.NewTraceIDRatioSampler(0.99)
        n:=20000
        smallCount:=0
        largeCount:=0
        for i:=0;i<n;i++{
            tid := randID()
            p := observability.SamplingParameters{TraceID:tid, SpanName:"t"}
            if small.ShouldSample(p)==observability.DecisionRecordAndSample { smallCount++ }
            if large.ShouldSample(p)==observability.DecisionRecordAndSample { largeCount++ }
        }
        sr := float64(smallCount)/float64(n)
        lr := float64(largeCount)/float64(n)
        fmt.Printf("small %f large %f\\n", sr, lr)
        if sr < 0.005 || sr > 0.02 { panic(fmt.Sprintf("small ratio 0.01 out of range %f", sr)) }
        if lr < 0.97 || lr > 1.0 { panic(fmt.Sprintf("large ratio 0.99 out of range %f", lr)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"small large ratio failed: {proc.stdout} {proc.stderr}"

def test_batch_processor_queue_size_edge():
    code=textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(1), observability.WithBatchSize(1))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<5;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        // Should not deadlock, exported should be at least 1 and at most 5
        exported := len(exp.GetSpans())
        if exported <1 || exported>5 { panic(fmt.Sprintf("edge queue size exported %d", exported)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"queue size edge failed: {proc.stdout} {proc.stderr}"

def test_batch_processor_default_options():
    code=textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithBatchSize(0), observability.WithQueueSize(0), observability.WithBatchTimeout(0), observability.WithExportTimeout(0))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        _, s := tracer.Start(context.Background(), "op")
        s.End()
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!=1 { panic("default options should still work") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"default options failed: {proc.stdout} {proc.stderr}"

def test_batch_processor_error_handling():
    code=textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    type errExporter struct{
        count int
    }
    func (e *errExporter) ExportSpans(ctx context.Context, spans []observability.ReadableSpan) error {
        e.count += len(spans)
        return fmt.Errorf("export error")
    }
    func main(){
        exp := &errExporter{}
        proc := observability.NewBatchSpanProcessor(exp, observability.WithBatchSize(5), observability.WithQueueSize(100))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        for i:=0;i<10;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        // Should not panic even though exporter returns error, and count should be 10
        if exp.count != 10 { panic(fmt.Sprintf("expected count 10 got %d", exp.count)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"error handling failed: {proc.stdout} {proc.stderr}"

def test_metrics_histogram_buckets_dedup_sorted():
    code=textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        // duplicate buckets
        h := prov.Histogram("dup_buckets", observability.WithBuckets([]float64{5,5,1,10,1}))
        h.Observe(2)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="dup_buckets" {
                // implementation may keep duplicates or dedup, but should be sorted and at least 3 unique?
                // We check sorted
                prev := -1.0
                for _, b := range fam.Metrics[0].Buckets {
                    if b.UpperBound < prev { panic("buckets not sorted") }
                    prev = b.UpperBound
                }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"dup buckets failed: {proc.stdout} {proc.stderr}"

def test_metrics_counter_add_nan_inf():
    code=textwrap.dedent("""
    package main
    import (
        "math"
        "ride-observability/observability"
        "fmt"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("nan_counter")
        c.Add(math.NaN())
        c.Add(math.Inf(1))
        c.Add(math.Inf(-1))
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="nan_counter" {
                if fam.Metrics[0].Value != 1 { panic(fmt.Sprintf("NaN/Inf should be ignored, expected 1 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"nan inf counter failed: {proc.stdout} {proc.stderr}"

def test_logger_with_nil_context():
    code=textwrap.dedent("""
    package main
    import (
        "bytes"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(nil, "nil ctx should not panic")
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"logger nil ctx failed: {proc.stdout} {proc.stderr}"

def test_tracing_span_context_traceflags_false():
    code=textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        // Use AlwaysOff sampler -> not sampled, TraceFlags 0
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc), observability.WithSampler(observability.NewAlwaysOffSampler()))
        _, span := tracer.Start(context.Background(), "not-sampled")
        sc := span.SpanContext()
        if sc.TraceFlags != 0 { panic(fmt.Sprintf("TraceFlags should be 0 when not sampled, got %d", sc.TraceFlags)) }
        if sc.Sampled { panic("sampled should be false") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"traceflags false failed: {proc.stdout} {proc.stderr}"

def test_metrics_gauge_add_large():
    code=textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("large_gauge")
        g.Add(1e9)
        g.Add(1e9)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="large_gauge" {
                if fam.Metrics[0].Value != 2e9 { panic(fmt.Sprintf("expected 2e9 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"gauge large failed: {proc.stdout} {proc.stderr}"

def test_batch_processor_forceflush_concurrent():
    code=textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewInMemoryExporter()
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(10000), observability.WithBatchSize(100))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        var wg sync.WaitGroup
        wg.Add(2)
        go func(){
            defer wg.Done()
            for i:=0;i<100;i++{
                _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
                s.End()
            }
        }()
        go func(){
            defer wg.Done()
            for i:=0;i<10;i++{
                proc.ForceFlush(context.Background())
            }
        }()
        wg.Wait()
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!=100 { panic(fmt.Sprintf("expected 100 got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode==0, f"forceflush concurrent failed: {proc.stdout} {proc.stderr}"
