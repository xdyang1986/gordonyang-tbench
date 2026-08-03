"""
Step2 verifier: large-scale hardening — sampling, batch processor, cardinality limiting, resource limits
"""

import os, subprocess, tempfile, textwrap, shutil, re, json
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


def go_run_program(go_code: str):
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
        proc = run(["go", "run", "."], cwd=tmp, timeout=30)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- reuse step1 basic checks (backward compat) ----


def test_files_exist_step2():
    assert os.path.isdir(os.path.join(APP_DIR, "observability"))
    for fname in ["tracing.go", "metrics.go", "logger.go"]:
        assert os.path.isfile(os.path.join(APP_DIR, "observability", fname)), (
            f"missing {fname}"
        )
    # sampling and batch may be in separate files or same; just check package builds
    assert os.path.isfile(os.path.join(APP_DIR, "go.mod"))


def test_go_build_vet_step2():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


# ---- sampler tests ----


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
        if on.Description()=="" { panic("on description empty") }
        if off.Description()=="" { panic("off description empty") }
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
        if zero.Description()=="" || one.Description()=="" || half.Description()=="" { panic("empty desc") }
        p := observability.SamplingParameters{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"s"}
        if zero.ShouldSample(p)!=observability.DecisionDrop { panic("ratio 0 should drop") }
        if one.ShouldSample(p)!=observability.DecisionRecordAndSample { panic("ratio 1 should sample") }
        // invalid fraction clamping? test negative -> drop, >1 -> sample
        neg := observability.NewTraceIDRatioSampler(-0.1)
        over := observability.NewTraceIDRatioSampler(1.5)
        if neg.ShouldSample(p)!=observability.DecisionDrop { panic("negative ratio should drop") }
        if over.ShouldSample(p)!=observability.DecisionRecordAndSample { panic(">1 ratio should sample") }
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
        // allow 0.05 to 0.15 for 0.1 ratio
        if ratio < 0.05 || ratio > 0.15 {
            panic(fmt.Sprintf("ratio 0.1 expected ~0.1, got %f (sampled %d)", ratio, sampled))
        }
        // test determinism: same traceID should give same decision
        tid := "0102030405060708090a0b0c0d0e0f10"
        p1 := observability.SamplingParameters{TraceID:tid, SpanName:"a"}
        d1 := sampler.ShouldSample(p1)
        d2 := sampler.ShouldSample(p1)
        if d1!=d2 { panic("sampler not deterministic") }
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
        fmt.Printf("ratio 0.5 sampled %f\\n", ratio)
        if ratio < 0.4 || ratio > 0.6 { panic(fmt.Sprintf("expected ~0.5 got %f", ratio)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ratio half failed: {proc.stdout} {proc.stderr}"


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

        // no parent -> delegate to root
        pNoParent := observability.SamplingParameters{TraceID:"0102030405060708090a0b0c0d0e0f10", HasParent:false}
        if pbOn.ShouldSample(pNoParent)!=observability.DecisionRecordAndSample { panic("parentbased no-parent should delegate to rootOn") }
        if pbOff.ShouldSample(pNoParent)!=observability.DecisionDrop { panic("parentbased no-parent should delegate to rootOff") }

        // has parent sampled -> sample regardless of root
        parentSampled := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        pSampledParent := observability.SamplingParameters{TraceID:parentSampled.TraceID, HasParent:true, ParentContext:parentSampled}
        if pbOff.ShouldSample(pSampledParent)!=observability.DecisionRecordAndSample { panic("parentbased with sampled parent should sample even if root off") }

        // has parent not sampled -> drop regardless
        parentNotSampled := observability.SpanContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false}
        pNotSampledParent := observability.SamplingParameters{TraceID:parentNotSampled.TraceID, HasParent:true, ParentContext:parentNotSampled}
        if pbOn.ShouldSample(pNotSampledParent)!=observability.DecisionDrop { panic("parentbased with not sampled parent should drop even if root on") }

        if pbOn.Description()=="" { panic("parentbased description empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent based failed: {proc.stdout} {proc.stderr}"


def test_tracer_with_sampler_integration():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        // AlwaysOff should export none
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        // if tracer supports WithSampler, use it; SimpleProcessor should still not get non-recording spans
        sampler := observability.NewAlwaysOffSampler()
        var tracer observability.Tracer
        // try NewTracer with WithSampler; if not supported, this will not compile -> test fails explicitly
        tracer = observability.NewTracer("svc", observability.WithSpanProcessor(proc), observability.WithSampler(sampler))
        ctx, span := tracer.Start(context.Background(), "should-drop")
        if span.IsRecording() { panic("span should not be recording when sampler AlwaysOff") }
        span.End()
        if len(exp.GetSpans())!=0 { panic(fmt.Sprintf("expected 0 exported spans with AlwaysOff, got %d", len(exp.GetSpans()))) }

        // AlwaysOn should export
        exp2 := observability.NewInMemoryExporter()
        proc2 := observability.NewSimpleSpanProcessor(exp2)
        tracer2 := observability.NewTracer("svc", observability.WithSpanProcessor(proc2), observability.WithSampler(observability.NewAlwaysOnSampler()))
        _, s2 := tracer2.Start(context.Background(), "should-sample")
        s2.End()
        if len(exp2.GetSpans())!=1 { panic("expected 1 span") }
        _ = ctx
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
        // root sampler ratio 0.5 but parent based
        root := observability.NewTraceIDRatioSampler(0.5)
        pb := observability.NewParentBasedSampler(root)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc), observability.WithSampler(pb))

        // create parent manually sampled
        parentSC := observability.SpanContext{
            TraceID: "0102030405060708090a0b0c0d0e0f10",
            SpanID: "0102030405060708",
            Sampled: true,
        }
        ctx := observability.ContextWithSpanContext(context.Background(), parentSC)
        // child should inherit sampled true even if root would drop? Actually parent sampled => child sampled
        ctx2, child := tracer.Start(ctx, "child")
        if !child.IsRecording() { panic("child of sampled parent should be recording with ParentBased") }
        child.End()
        // now not sampled parent
        parentSC2 := observability.SpanContext{
            TraceID: "0202030405060708090a0b0c0d0e0f10",
            SpanID: "0202030405060708",
            Sampled: false,
        }
        ctx3 := observability.ContextWithSpanContext(context.Background(), parentSC2)
        _, child2 := tracer.Start(ctx3, "child2")
        if child2.IsRecording() { panic("child of not-sampled parent should NOT be recording") }
        child2.End()
        _ = ctx2
        fmt.Printf("spans exported %d expected 1 (only sampled parent chain)\\n", len(exp.GetSpans()))
        if len(exp.GetSpans())!=1 { panic(fmt.Sprintf("expected 1 got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent propagation via tracer failed: {proc.stdout} {proc.stderr}"
    )


# ---- batch processor ----


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
        // force flush
        if err := proc.ForceFlush(context.Background()); err!=nil { panic("force flush failed: "+err.Error()) }
        spans := exp.GetSpans()
        fmt.Printf("exported %d\\n", len(spans))
        if len(spans)!=10 { panic(fmt.Sprintf("expected 10 got %d", len(spans))) }
        // shutdown
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch basic failed: {proc.stdout} {proc.stderr}"


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
        // small queue, large batch size but no background export fast enough? Actually export is fast, but we need to test drop
        // To force drop, we will use queue size 10 and rapidly enqueue 100 without flush
        proc := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(10), observability.WithBatchSize(20), observability.WithBatchTimeout(5*time.Second))
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        // Enqueue fast, processor might export but queue limited
        for i:=0;i<100;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            span.End()
        }
        // Give time for some export attempts but we expect some dropped due to small queue
        time.Sleep(200*time.Millisecond)
        // Measure dropped via method if available, else via exported count < 100
        // Try to call DroppedCount if exists via type assertion (we can't directly)
        // We'll just check that Enqueue didn't block forever: if we reached here quickly, backpressure OK
        // ForceFlush remaining
        proc.ForceFlush(context.Background())
        exported := len(exp.GetSpans())
        fmt.Printf("exported %d out of 100\\n", exported)
        // Must not panic and exported <=100, but if drop implemented, exported <100 is ok. If not, exported==100 also ok as queue might have processed fast.
        // However we test backpressure timing: second part: ensure enqueue is non-blocking
        start := time.Now()
        for i:=0;i<50;i++{
            _, span := tracer.Start(context.Background(), "fast")
            span.End()
        }
        elapsed := time.Since(start)
        fmt.Printf("fast enqueue 50 took %v\\n", elapsed)
        if elapsed > 500*time.Millisecond {
            panic(fmt.Sprintf("enqueue blocking too long (%v), should drop when full", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch drop backpressure failed: {proc.stdout} {proc.stderr}"
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
        // force flush
        if err:= proc.ForceFlush(context.Background()); err!=nil { panic(err.Error()) }
        time.Sleep(100*time.Millisecond)
        // shutdown flush
        proc.Shutdown(context.Background())
        spans := exp.GetSpans()
        expected := n*per
        fmt.Printf("exported %d expected %d\\n", len(spans), expected)
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
        // shutdown should flush
        if err:= proc.Shutdown(context.Background()); err!=nil { panic(err.Error()) }
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
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randID() string {
        b:=make([]byte,16)
        rand.Read(b)
        return hex.EncodeToString(b)
    }
    func main(){
        exp := observability.NewInMemoryExporter()
        batch := observability.NewBatchSpanProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(512))
        // ratio 0.1 + batch
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
        // expect ~500 within tolerance 250-750
        if exported < 250 || exported > 750 {
            panic(fmt.Sprintf("expected ~500 got %d", exported))
        }
        _ = randID
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch with sampler failed: {proc.stdout} {proc.stderr}"
    )


# ---- metrics cardinality ----


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
        fmt.Printf("metrics count %d\\n", count)
        if count>2 { panic(fmt.Sprintf("expected <=2 metrics got %d", count)) }
        dropped := prov.DroppedSeriesCount()
        fmt.Printf("dropped %d\\n", dropped)
        if dropped <1 { panic(fmt.Sprintf("expected dropped >=1 got %d", dropped)) }
        // reuse existing should not increase dropped
        c1b := prov.Counter("my_counter", observability.WithLabels(map[string]string{"id":"1"}))
        c1b.Inc()
        if prov.DroppedSeriesCount()!=dropped { panic("reuse should not increase dropped") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"cardinality drop failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_unlimited():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider() // unlimited
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


# ---- resource limits ----


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
        // attribute value truncation test: string >1024
        long := strings.Repeat("a", 2000)
        span.AddAttribute("long", long)
        // event limit: add 200 events, should cap at 128
        for i:=0;i<200;i++{
            span.AddEvent(fmt.Sprintf("ev-%d", i))
        }
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        s := spans[0]
        // check truncation: long attr should be <=1024 if implementation truncates
        if v, ok := s.Attributes["long"]; ok {
            if str, ok := v.(string); ok {
                if len(str) > 1024 {
                    panic(fmt.Sprintf("expected truncated to <=1024 got %d", len(str)))
                }
            }
        }
        if len(s.Events) > 128 {
            panic(fmt.Sprintf("event limit expected <=128 got %d", len(s.Events)))
        }
        fmt.Printf("events %d attrs %d OK\\n", len(s.Events), len(s.Attributes))
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"resource limits failed: {proc.stdout} {proc.stderr}"


# ---- backward compat: ensure step1 still works ----


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
        // Use batch for high throughput
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
                // simulate matching
                _, child := tracer.Start(ctx, "MatchDriver")
                child.AddAttribute("driver_id", fmt.Sprintf("driver-%d", idx%100))
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
        fmt.Printf("rides %d exported spans %d (expected ~ rides*3*0.2 = 600)\\n", rides, exported)
        // 1000 rides * 3 spans = 3000, 20% = 600, allow 300-900
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
