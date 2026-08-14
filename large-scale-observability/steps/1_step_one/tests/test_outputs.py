"""
Step1 verifier — single-header x-ride-trace, concurrency, Collect deep copys
"""

import os, subprocess, tempfile, re, textwrap, shutil
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
    tmp = tempfile.mkdtemp(prefix="obs_test_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "."], cwd=tmp, timeout=20)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_run_race_program(go_code: str, timeout=30):
    tmp = tempfile.mkdtemp(prefix="obs_test_")
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


def test_files_exist():
    assert os.path.isdir(os.path.join(APP_DIR, "observability"))
    for fname in ["tracing.go", "metrics.go", "logger.go"]:
        assert os.path.isfile(os.path.join(APP_DIR, "observability", fname)), (
            f"missing {fname}"
        )


def test_go_mod_no_external():
    with open(os.path.join(APP_DIR, "go.mod")) as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            dep = m.group(2).split("/")[0]
            assert "." not in dep, f"external dependency {m.group(2)} not allowed"


def test_stdlib_only():
    import_re = re.compile(r'"([^"]+)"')
    for root, _, files in os.walk(APP_DIR):
        if "/.git" in root or "testharness" in root:
            continue
        for file in files:
            if not file.endswith(".go"):
                continue
            path = os.path.join(root, file)
            with open(path) as fh:
                txt = fh.read()
            for block in re.findall(r"import\s*\((.*?)\)", txt, flags=re.S):
                for imp in import_re.findall(block):
                    first = imp.split("/")[0]
                    assert "." not in first or first == "ride-observability", (
                        f"non stdlib import {imp} in {path}"
                    )
            for imp in re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', txt):
                first = imp.split("/")[0]
                if first == "ride-observability":
                    continue
                assert "." not in first, f"non stdlib import {imp} in {path}"


def test_go_build_and_vet():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


def test_tracing_id_format():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "regexp"
        "context"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "test-span")
        _ = ctx
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span got %d", len(spans))) }
        sc := spans[0].SpanContext
        if len(sc.TraceID)!=32 { panic("traceID len !=32") }
        if len(sc.SpanID)!=16 { panic("spanID len !=16") }
        hex32 := regexp.MustCompile("^[0-9a-fA-F]{32}$")
        hex16 := regexp.MustCompile("^[0-9a-fA-F]{16}$")
        if !hex32.MatchString(sc.TraceID) { panic("traceID not hex32: "+sc.TraceID) }
        if !hex16.MatchString(sc.SpanID) { panic("spanID not hex16: "+sc.SpanID) }
        if !sc.Sampled { panic("sampled should be true in step1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"tracing id format failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_id_uniqueness():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        seenTrace := map[string]bool{}
        seenSpan := map[string]bool{}
        n:=5000
        for i:=0;i<n;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            sc := span.Context()
            if seenTrace[sc.TraceID] { panic("duplicate traceID "+sc.TraceID) }
            if seenSpan[sc.SpanID] { panic("duplicate spanID "+sc.SpanID) }
            seenTrace[sc.TraceID]=true
            seenSpan[sc.SpanID]=true
            span.End()
        }
        fmt.Printf("unique trace %d span %d OK\\n", len(seenTrace), len(seenSpan))
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"id uniqueness failed: {proc.stdout} {proc.stderr}"


def test_tracing_no_parent_new_trace():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s1 := tracer.Start(context.Background(), "a")
        _, s2 := tracer.Start(context.Background(), "b")
        if s1.Context().TraceID == s2.Context().TraceID {
            panic("background starts should generate different traceIDs")
        }
        s1.End()
        s2.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"no parent new trace failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_child_inherits():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        ctx1, s1 := tracer.Start(context.Background(), "parent")
        ctx2, s2 := tracer.Start(ctx1, "child")
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        if len(spans)!=2 { panic(fmt.Sprintf("expected 2 spans got %d", len(spans))) }
        var parent, child *observability.FinishedSpan
        for i:= range spans {
            if spans[i].Name=="parent" { parent = &spans[i] }
            if spans[i].Name=="child" { child = &spans[i] }
        }
        if parent==nil || child==nil { panic("parent/child not found") }
        if parent.SpanContext.TraceID != child.SpanContext.TraceID { panic(fmt.Sprintf("traceID mismatch parent %s child %s", parent.SpanContext.TraceID, child.SpanContext.TraceID)) }
        if parent.SpanContext.SpanID == child.SpanContext.SpanID { panic("spanID should differ") }
        if child.ParentID != parent.SpanContext.SpanID { panic(fmt.Sprintf("child parentID %s != parent spanID %s", child.ParentID, parent.SpanContext.SpanID)) }
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("no TraceContext in ctx2") }
        if sc.TraceID != child.SpanContext.TraceID { panic("ctx2 traceID mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"child inherits failed: {proc.stdout} {proc.stderr}"


def test_tracing_withparent_overrides():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx1, s1 := tracer.Start(context.Background(), "parent1")
        ctx2, s2 := tracer.Start(context.Background(), "parent2")
        explicit := s2.Context()
        _, child := tracer.Start(ctx1, "child", observability.WithParent(explicit))
        if child.Context().TraceID != explicit.TraceID {
            panic(fmt.Sprintf("WithParent should override context parent, expected %s got %s", explicit.TraceID, child.Context().TraceID))
        }
        if child.Context().ParentID != explicit.SpanID {
            panic("WithParent parentID mismatch")
        }
        child.End()
        s1.End()
        s2.End()
        _ = ctx2
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withparent override failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_span_kind():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s := tracer.Start(context.Background(), "kind-test", observability.WithSpanKind(observability.KindServer))
        s.End()
        spans := exp.GetSpans()
        if spans[0].Kind != observability.KindServer { panic(fmt.Sprintf("expected server kind got %d", spans[0].Kind)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"span kind failed: {proc.stdout} {proc.stderr}"


def test_tracing_attributes_events_status():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "op", observability.WithAttributes(observability.Attribute{Key:"k1", Value:"v1"}))
        span.AddAttribute("k2", 42)
        span.AddAttribute("k3", true)
        span.AddEvent("event1", observability.Attribute{Key:"e1", Value:"x"})
        span.SetStatus(observability.StatusError, "oops")
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        s := spans[0]
        if len(s.Attributes) < 3 { panic(fmt.Sprintf("expected >=3 attrs got %d", len(s.Attributes))) }
        if s.Attributes["k1"]!="v1" { panic("k1 missing") }
        if len(s.Events)!=1 { panic(fmt.Sprintf("expected 1 event got %d", len(s.Events))) }
        if s.StatusCode != observability.StatusError { panic("status code not error") }
        if s.StatusMessage != "oops" { panic("status msg mismatch") }
        if s.ServiceName != "ride-service" { panic("service name missing "+s.ServiceName) }
        if s.StartTime.IsZero() || s.EndTime.IsZero() { panic("start/end zero") }
        if s.EndTime.Before(s.StartTime) { panic("end before start") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attrs/events/status failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_add_after_end_noop():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "after-end")
        span.End()
        span.AddAttribute("should-not", "appear")
        span.AddEvent("should-not")
        span.SetStatus(observability.StatusError, "nope")
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        if _, ok := spans[0].Attributes["should-not"]; ok { panic("attribute after End should be noop") }
        if len(spans[0].Events)!=0 { panic("event after End should be noop") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"add after end failed: {proc.stdout} {proc.stderr}"


def test_tracing_event_attributes():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "ev-attr")
        span.AddEvent("ride_matched", observability.Attribute{Key:"driver", Value:"d123"}, observability.Attribute{Key:"eta", Value:5})
        span.End()
        spans := exp.GetSpans()
        ev := spans[0].Events[0]
        if ev.Name!="ride_matched" { panic("event name mismatch") }
        if len(ev.Attributes)!=2 { panic("event attrs count mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event attrs failed: {proc.stdout} {proc.stderr}"


def test_tracing_end_idempotent():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "idempotent")
        span.End()
        span.End()
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("idempotent End should produce 1 span, got %d", len(spans))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"idempotent failed: {proc.stdout} {proc.stderr}"


def test_tracing_marshal_unmarshal_single_header():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok || v=="" { panic("x-ride-trace missing, MarshalTrace must write single header") }
        // ensure single-header only
        if _, has := carrier["trace-id"]; has {
            panic("MarshalTrace must write only x-ride-trace, not trace-id")
        }
        if _, has := carrier["span-id"]; has {
            panic("MarshalTrace should NOT write span-id, use x-ride-trace")
        }
        ctx2 := observability.UnmarshalTrace(carrier)
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("Unmarshal missing trace") }
        origSc, _ := observability.TraceFromContext(ctx)
        if sc.TraceID != origSc.TraceID { panic("traceID mismatch") }
        if sc.SpanID != origSc.SpanID { panic("spanID mismatch") }
        if sc.Sampled != origSc.Sampled { panic("sampled flag mismatch") }
        bad := map[string]string{"x-ride-trace":"bad"}
        ctxBad := observability.UnmarshalTrace(bad)
        _, ok = observability.TraceFromContext(ctxBad)
        if ok { panic("bad carrier should yield no trace") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal/unmarshal single-header failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_inject_extract_alias_single_header():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.Inject(ctx, carrier)
        // Inject alias must also use single header
        if _, ok := carrier["x-ride-trace"]; !ok {
            panic("Inject alias must write x-ride-trace")
        }
        if _, has := carrier["trace-id"]; has {
            panic("Inject must write only x-ride-trace")
        }
        ctx2 := observability.Extract(carrier)
        sc, ok := observability.SpanContextFromContext(ctx2)
        if !ok { panic("Extract alias missing span") }
        origSc, _ := observability.SpanContextFromContext(ctx)
        if sc.TraceID != origSc.TraceID { panic("traceID mismatch via alias") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"inject/extract alias single-header failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_marshal_preserves_sampled():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        // sampled should be 1
        val := carrier["x-ride-trace"]
        fmt.Println(val)
        // parse sampled part
        // last part after last colon is sampled
        // check ends with :1
        if len(val) < 2 || val[len(val)-1] != '1' {
            // root sampled true per step1
            panic("sampled should be 1 in x-ride-trace, got "+val)
        }
        span.End()
        // manually create not sampled context
        carrier2 := map[string]string{"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:0102030405060708::0"}
        ctx2 := observability.UnmarshalTrace(carrier2)
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have sc") }
        if sc.Sampled { panic("sampled should be false when 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal sampled flag failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                ctx, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d", idx))
                span.AddAttribute("idx", idx)
                span.AddEvent("ev")
                _ = ctx
                span.End()
            }(i)
        }
        wg.Wait()
        spans := exp.GetSpans()
        if len(spans)!=n { panic(fmt.Sprintf("expected %d spans got %d", n, len(spans))) }
        seen := map[string]bool{}
        for _, s := range spans {
            if seen[s.SpanContext.SpanID] { panic("duplicate spanID "+s.SpanContext.SpanID) }
            seen[s.SpanContext.SpanID]=true
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrent tracing failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_concurrent_addattr():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "concurrent-attr")
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                span.AddAttribute(fmt.Sprintf("k%d", idx), idx)
                span.AddEvent(fmt.Sprintf("ev-%d", idx))
            }(i)
        }
        wg.Wait()
        span.End()
        spans := exp.GetSpans()
        if len(spans[0].Attributes) > 128 { panic("attrs >128") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrent addattr failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_attribute_limit():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "limit")
        for i:=0;i<200;i++{
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        span.End()
        spans := exp.GetSpans()
        if len(spans[0].Attributes) > 128 {
            panic(fmt.Sprintf("attrs >128 not enforced: %d", len(spans[0].Attributes)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr limit failed: {proc.stdout} {proc.stderr}"


def test_tracing_attribute_initial_limit():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var attrs []observability.Attribute
        for i:=0;i<200;i++{
            attrs = append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value: i})
        }
        _, span := tracer.Start(context.Background(), "initial-limit", observability.WithAttributes(attrs...))
        span.End()
        spans := exp.GetSpans()
        if len(spans[0].Attributes) > 128 {
            panic(fmt.Sprintf("initial attrs >128: %d", len(spans[0].Attributes)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"initial attr limit failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_starttime_bounds():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        before := time.Now()
        _, span := tracer.Start(context.Background(), "time-bounds")
        time.Sleep(5*time.Millisecond)
        span.End()
        after := time.Now()
        s := exp.GetSpans()[0]
        if s.StartTime.Before(before) || s.StartTime.After(after) { panic("start time out of bounds") }
        if s.EndTime.Before(s.StartTime) { panic("end before start") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"starttime bounds failed: {proc.stdout} {proc.stderr}"


def test_tracing_context_direct():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        got, ok := observability.TraceFromContext(ctx)
        if !ok { panic("not ok") }
        if got.TraceID != sc.TraceID { panic("tid mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context direct failed: {proc.stdout} {proc.stderr}"


def test_metrics_counter_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("ride_requests_total", observability.WithLabels(map[string]string{"status":"requested"}))
        c.Inc()
        c.Add(2)
        c.Add(-1)
        fams := prov.Collect()
        var found bool
        for _, fam := range fams {
            if fam.Name=="ride_requests_total" {
                found=true
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("expected 1 metric got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 3 { panic(fmt.Sprintf("expected value 3 got %f", fam.Metrics[0].Value)) }
            }
        }
        if !found { panic("family not found") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter basic failed: {proc.stdout} {proc.stderr}"


def test_metrics_counter_reuse():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"a":"1"}))
        c2 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"a":"1"}))
        c1.Inc()
        c2.Inc()
        fams := prov.Collect()
        var val float64
        for _, fam := range fams {
            if fam.Name=="my_counter" {
                for _, m := range fam.Metrics {
                    if m.Labels["a"]=="1" { val+=m.Value }
                }
            }
        }
        if val!=2 { panic(fmt.Sprintf("expected 2 got %f", val)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter reuse failed: {proc.stdout} {proc.stderr}"


def test_metrics_distinct_labels():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("req", observability.WithLabels(map[string]string{"route":"a"}))
        c2 := prov.Counter("req", observability.WithLabels(map[string]string{"route":"b"}))
        c1.Inc()
        c2.Add(5)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="req" {
                if len(fam.Metrics)!=2 { panic(fmt.Sprintf("expected 2 distinct series got %d", len(fam.Metrics))) }
                fmt.Println("OK")
                return
            }
        }
        panic("family req not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"distinct labels failed: {proc.stdout} {proc.stderr}"


def test_metrics_concurrency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("conc_counter")
        var wg sync.WaitGroup
        n:=100
        per:=1000
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(){
                defer wg.Done()
                for j:=0;j<per;j++{ c.Inc() }
            }()
        }
        wg.Wait()
        fams := prov.Collect()
        var total float64
        for _, fam := range fams {
            if fam.Name=="conc_counter" {
                for _, m := range fam.Metrics { total+=m.Value }
            }
        }
        expected := float64(n*per)
        if total!=expected { panic(fmt.Sprintf("expected %f got %f", expected, total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"metrics concurrency failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_gauge_histogram():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("active_rides")
        g.Set(10)
        g.Inc()
        g.Dec()
        g.Add(5)
        h := prov.Histogram("ride_duration_seconds", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(0.5)
        h.Observe(2)
        h.Observe(7)
        h.Observe(20)
        fams := prov.Collect()
        var gaugeVal float64
        var histFound bool
        for _, fam := range fams {
            if fam.Name=="active_rides" {
                for _, m := range fam.Metrics { gaugeVal=m.Value }
            }
            if fam.Name=="ride_duration_seconds" {
                histFound=true
                if len(fam.Metrics)!=1 { panic("expected 1 hist metric") }
                m := fam.Metrics[0]
                if m.Count!=4 { panic(fmt.Sprintf("hist count expected 4 got %d", m.Count)) }
                if m.Sum < 29.4 || m.Sum > 29.6 { panic(fmt.Sprintf("hist sum expected ~29.5 got %f", m.Sum)) }
            }
        }
        if gaugeVal!=15 { panic(fmt.Sprintf("gauge expected 15 got %f", gaugeVal)) }
        if !histFound { panic("hist not found") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge/hist failed: {proc.stdout} {proc.stderr}"


def test_metrics_histogram_default_buckets():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("default_bucket_hist")
        h.Observe(0.01)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="default_bucket_hist" {
                if len(fam.Metrics[0].Buckets)!=11 { panic(fmt.Sprintf("default buckets expected 11 got %d", len(fam.Metrics[0].Buckets))) }
                if fam.Metrics[0].Buckets[0].UpperBound != 0.005 { panic("first default bucket should be 0.005") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"default buckets failed: {proc.stdout} {proc.stderr}"


def test_metrics_histogram_cumulative_strict():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("strict_hist", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(0.5)
        h.Observe(2)
        h.Observe(7)
        h.Observe(20)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="strict_hist" {
                m := fam.Metrics[0]
                if m.Buckets[0].Count != 1 { panic(fmt.Sprintf("bucket 1 expected 1 got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count != 2 { panic(fmt.Sprintf("bucket 5 expected 2 got %d", m.Buckets[1].Count)) }
                if m.Buckets[2].Count != 3 { panic(fmt.Sprintf("bucket 10 expected 3 got %d", m.Buckets[2].Count)) }
                if m.Count != 4 { panic("count 4") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cumulative strict failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_invalid_name():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("invalid-name", observability.WithLabels(map[string]string{"ok":"1"}))
        c.Inc()
        c2 := prov.Counter("123bad", observability.WithLabels(map[string]string{"ok":"1"}))
        c2.Inc()
        c3 := prov.Counter("good_name", observability.WithLabels(map[string]string{"bad-key":"1"}))
        c3.Inc()
        c4 := prov.Counter("good_name2", observability.WithLabels(map[string]string{"":"empty"}))
        c4.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="invalid-name" || fam.Name=="123bad" { panic("invalid metric name should not be in Collect") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"invalid name test failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_collect_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("copy_test", observability.WithLabels(map[string]string{"env":"prod"}))
        c.Inc()
        fams1 := prov.Collect()
        if len(fams1)==0 || len(fams1[0].Metrics)==0 {
            panic("Collect returned empty for copy_test")
        }
        if fams1[0].Metrics[0].Labels == nil {
            panic("Collect returned nil Labels map; expected non-nil")
        }
        fams1[0].Metrics[0].Value = 9999
        fams1[0].Metrics[0].Labels["env"]="hacked"
        fams1[0].Metrics[0].Labels["injected"]="yes"
        fams2 := prov.Collect()
        var val float64
        var env string
        var hasInjected bool
        for _, fam := range fams2 {
            if fam.Name=="copy_test" {
                if len(fam.Metrics)==0 { panic("copy_test empty on second collect") }
                val = fam.Metrics[0].Value
                if fam.Metrics[0].Labels == nil { panic("second Collect nil Labels") }
                env = fam.Metrics[0].Labels["env"]
                _, hasInjected = fam.Metrics[0].Labels["injected"]
            }
        }
        if hasInjected { panic("Collect should return deep copy, injected leaked") }
        if env != "prod" { panic(fmt.Sprintf("env expected prod got %s", env)) }
        if val != 1 { panic(fmt.Sprintf("expected 1 got %f", val)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"collect copy failed: {proc.stdout} {proc.stderr}"


def test_metrics_label_truncate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        long := strings.Repeat("a", 500)
        c := prov.Counter("truncate_test", observability.WithLabels(map[string]string{"id": long}))
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="truncate_test" {
                v := fam.Metrics[0].Labels["id"]
                if len(v) > 256 { panic(fmt.Sprintf("label value should be truncated to 256, got %d", len(v))) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"label truncate failed: {proc.stdout} {proc.stderr}"


def test_metrics_gauge_negative():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("neg_gauge")
        g.Set(0)
        g.Dec()
        g.Add(-5)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="neg_gauge" {
                if fam.Metrics[0].Value != -6 { panic(fmt.Sprintf("expected -6 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge negative failed: {proc.stdout} {proc.stderr}"


def test_metrics_provider_isolation():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p1 := observability.NewMetricsProvider()
        p2 := observability.NewMetricsProvider()
        c1 := p1.Counter("isolated")
        c1.Inc()
        fams2 := p2.Collect()
        for _, fam := range fams2 {
            if fam.Name=="isolated" { panic("provider isolation broken") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"isolation failed: {proc.stdout} {proc.stderr}"


def test_logger_json_and_trace():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("ride-service", observability.WithOutput(buf))
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "test-log")
        logger.Info(ctx, "ride requested", observability.Field{Key:"ride_id", Value:"r123"})
        span.End()
        line := buf.String()
        var obj map[string]interface{}
        if err:= json.Unmarshal([]byte(line), &obj); err!= nil { panic("not valid json: "+err.Error()) }
        if obj["service"]!="ride-service" { panic("service missing") }
        if obj["message"]!="ride requested" { panic("message missing") }
        if obj["trace_id"]==nil { panic("trace_id missing") }
        if obj["span_id"]==nil { panic("span_id missing") }
        if obj["ride_id"]!="r123" { panic("custom field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger json trace failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_no_trace():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "no trace")
        var obj map[string]interface{}
        json.Unmarshal(buf.Bytes(), &obj)
        if _, ok := obj["trace_id"]; ok { panic("trace_id should not be present without span") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger no trace failed: {proc.stdout} {proc.stderr}"


def test_logger_with_immutable():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        child := base.With(observability.Field{Key:"env", Value:"prod"})
        base.Info(context.Background(), "base msg")
        child.Info(context.Background(), "child msg")
        lines := bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\\n"))
        if len(lines)!=2 { panic(fmt.Sprintf("expected 2 lines got %d", len(lines))) }
        var m1,m2 map[string]interface{}
        json.Unmarshal(lines[0], &m1)
        json.Unmarshal(lines[1], &m2)
        if _, ok := m1["env"]; ok { panic("base logger should not have env") }
        if m2["env"]!="prod" { panic("child should have env=prod") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger With immutable failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_multiple_with_chain():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        l1 := base.With(observability.Field{Key:"a", Value:"1"})
        l2 := l1.With(observability.Field{Key:"b", Value:"2"})
        l2.Info(context.Background(), "msg")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["a"]!="1" || obj["b"]!="2" { panic("chained With failed") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"multiple with chain failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_field_overwrite():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        base = base.With(observability.Field{Key:"env", Value:"dev"})
        child := base.With(observability.Field{Key:"env", Value:"prod"})
        child.Info(context.Background(), "msg")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["env"]!="prod" { panic(fmt.Sprintf("expected prod got %v", obj["env"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"field overwrite failed: {proc.stdout} {proc.stderr}"


def test_logger_level_filter():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "strings"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("error"))
        logger.Info(context.Background(), "info filtered")
        logger.Debug(context.Background(), "debug filtered")
        logger.Warn(context.Background(), "warn filtered")
        logger.Error(context.Background(), "error pass")
        out := strings.TrimSpace(buf.String())
        if out=="" { panic("expected error log") }
        lines := strings.Split(out, "\\n")
        var nonEmpty []string
        for _, l := range lines { if strings.TrimSpace(l)!="" { nonEmpty=append(nonEmpty,l) } }
        if len(nonEmpty)!=1 { panic(fmt.Sprintf("expected 1 line got %d", len(nonEmpty))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger level filter failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_timestamp_format():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "ts test")
        var obj map[string]interface{}
        json.Unmarshal(buf.Bytes(), &obj)
        tsStr, ok := obj["timestamp"].(string)
        if !ok { panic("timestamp missing or not string") }
        if _, err := time.Parse(time.RFC3339Nano, tsStr); err != nil {
            panic("timestamp not RFC3339Nano: "+err.Error())
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"timestamp format failed: {proc.stdout} {proc.stderr}"


def test_logger_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                logger.Info(context.Background(), fmt.Sprintf("msg-%d", idx))
            }(i)
        }
        wg.Wait()
        out := buf.String()
        lines := 0
        for _, c := range out { if c=='\\n' { lines++ } }
        if lines!=n { panic(fmt.Sprintf("expected %d lines got %d", n, lines)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"logger concurrent failed: {proc.stdout} {proc.stderr}"
    )


def test_exporter_clear_and_count():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s1 := tracer.Start(context.Background(), "a")
        s1.End()
        if exp.GetCount()!=1 { panic("GetCount expected 1") }
        exp.Clear()
        if exp.GetCount()!=0 { panic("Clear should make 0") }
        if len(exp.GetSpans())!=0 { panic("GetSpans after Clear should be 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter clear/count failed: {proc.stdout} {proc.stderr}"
    )


def test_tracer_isolation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp1 := observability.NewMemoryExporter()
        exp2 := observability.NewMemoryExporter()
        p1 := observability.NewSimpleProcessor(exp1)
        p2 := observability.NewSimpleProcessor(exp2)
        t1 := observability.NewTracer("service-a", observability.WithProcessor(p1))
        t2 := observability.NewTracer("service-b", observability.WithProcessor(p2))
        _, s1 := t1.Start(context.Background(), "op1")
        s1.End()
        if len(exp2.GetSpans())!=0 { panic("tracer isolation broken") }
        if exp1.GetSpans()[0].ServiceName!="service-a" { panic("service name mismatch") }
        _, s2 := t2.Start(context.Background(), "op2")
        s2.End()
        if len(exp2.GetSpans())!=1 { panic("exp2 should have 1") }
        if exp2.GetSpans()[0].ServiceName!="service-b" { panic("service b name mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"tracer isolation failed: {proc.stdout} {proc.stderr}"


def test_tracing_attribute_types():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "types")
        span.AddAttribute("int", 42)
        span.AddAttribute("float", 3.14)
        span.AddAttribute("bool", true)
        span.AddAttribute("string", "hello")
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["int"]!=42 { panic("int attr mismatch") }
        if s.Attributes["bool"]!=true { panic("bool mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr types failed: {proc.stdout} {proc.stderr}"


def test_tracing_event_limit():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "event-limit")
        for i:=0;i<200;i++{
            span.AddEvent(fmt.Sprintf("ev-%d", i))
        }
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Events) > 128 { panic(fmt.Sprintf("event limit >128 got %d", len(s.Events))) }
        if len(s.Events) < 128 { panic(fmt.Sprintf("expected 128 after 200 adds, got %d", len(s.Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event limit failed: {proc.stdout} {proc.stderr}"


def test_tracing_with_race():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=50
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                ctx, span := tracer.Start(context.Background(), fmt.Sprintf("race-%d", idx))
                for j:=0;j<20;j++{
                    span.AddAttribute(fmt.Sprintf("k%d", j), j)
                }
                span.End()
                _ = ctx
            }(i)
        }
        wg.Wait()
        if len(exp.GetSpans())!=n { panic(fmt.Sprintf("expected %d got %d", n, len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"race test failed: {proc.stdout} {proc.stderr}"


def test_tracing_custom_id_generator():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    type fixedGen struct{}
    func (f *fixedGen) NewTraceID() string { return "0102030405060708090a0b0c0d0e0f10" }
    func (f *fixedGen) NewSpanID() string { return "0102030405060708" }
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        gen := &fixedGen{}
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(gen))
        _, span := tracer.Start(context.Background(), "fixed")
        sc := span.Context()
        if sc.TraceID != "0102030405060708090a0b0c0d0e0f10" { panic("custom traceID not used") }
        if sc.SpanID != "0102030405060708" { panic("custom spanID not used") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"custom id gen failed: {proc.stdout} {proc.stderr}"


def test_tracing_service_name_override():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("original", observability.WithProcessor(proc), observability.WithServiceName("override"))
        _, span := tracer.Start(context.Background(), "op")
        span.End()
        s := exp.GetSpans()[0]
        if s.ServiceName != "override" { panic(fmt.Sprintf("expected override got %s", s.ServiceName)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"service name override failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_description():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("desc_counter", observability.WithDescription("number of requests"))
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="desc_counter" {
                if fam.Help != "number of requests" { panic(fmt.Sprintf("help expected got '%s'", fam.Help)) }
                fmt.Println("OK")
                return
            }
        }
        panic("desc_counter not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"description failed: {proc.stdout} {proc.stderr}"


def test_metrics_histogram_unsorted_buckets():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("unsorted", observability.WithBuckets([]float64{10,1,5}))
        h.Observe(2)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="unsorted" {
                buckets := fam.Metrics[0].Buckets
                if buckets[0].UpperBound != 1 || buckets[1].UpperBound != 5 || buckets[2].UpperBound != 10 {
                    panic(fmt.Sprintf("buckets not sorted: %v", buckets))
                }
                if buckets[0].Count != 0 || buckets[1].Count != 1 {
                    panic(fmt.Sprintf("counts wrong: %v", buckets))
                }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unsorted buckets failed: {proc.stdout} {proc.stderr}"


def test_logger_level_case_insensitive():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("ERROR"))
        logger.Info(context.Background(), "filtered")
        logger.Error(context.Background(), "pass")
        _ = fmt.Sprintf("x")
        out := strings.TrimSpace(buf.String())
        if out=="" { panic("error should pass") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"level case insensitive failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_traceflags():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "flags")
        sc := span.Context()
        if sc.Flags != 1 { panic(fmt.Sprintf("Flags should be 1 got %d", sc.Flags)) }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"traceflags failed: {proc.stdout} {proc.stderr}"


def test_tracing_tracecontext_field_names():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"aabbccddeeff0011", Sampled:true, Flags:1}
        if tc.TraceID=="" { panic("TraceID field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"tracecontext field test failed: {proc.stdout} {proc.stderr}"
    )


def test_exporter_getspans_deep_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "deepcopy", observability.WithAttributes(observability.Attribute{Key:"k", Value:"v"}))
        span.End()
        spans1 := exp.GetSpans()
        if len(spans1)!=1 { panic("expected 1") }
        if spans1[0].Attributes == nil { panic("Attributes nil, expected non-nil map") }
        spans1[0].Attributes["k"] = "hacked"
        spans1[0].Attributes["injected"] = "yes"
        spans1[0].Name = "hacked-name"
        spans2 := exp.GetSpans()
        if spans2[0].Attributes["k"] != "v" { panic(fmt.Sprintf("GetSpans should return deep copy of Attributes, expected v got %s", spans2[0].Attributes["k"])) }
        if _, ok := spans2[0].Attributes["injected"]; ok { panic("GetSpans deep copy failed - injected leaked") }
        if spans2[0].Name != "deepcopy" { panic("GetSpans deep copy failed - Name mutation leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter GetSpans deep copy failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_context_immutability():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), tc)
        // mutate original after storing
        tc.TraceID = "ffffffffffffffffffffffffffffffff"
        tc.SpanID = "ffffffffffffffff"
        got, ok := observability.TraceFromContext(ctx)
        if !ok { panic("should have trace") }
        if got.TraceID == "ffffffffffffffffffffffffffffffff" { panic("ContextWithTrace must store copy, not reference - mutation leaked") }
        if got.TraceID != "0102030405060708090a0b0c0d0e0f10" { panic("traceID mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"context immutability failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_attribute_duplicate_last_wins():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "dup", observability.WithAttributes(observability.Attribute{Key:"k", Value:"first"}, observability.Attribute{Key:"k", Value:"second"}))
        span.AddAttribute("k", "third")
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["k"] != "third" { panic(fmt.Sprintf("duplicate attr last wins expected third got %v", s.Attributes["k"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"duplicate attr last wins failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_event_attr_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "evcopy")
        attrs := []observability.Attribute{{Key:"a", Value:"1"}, {Key:"b", Value:"2"}}
        span.AddEvent("ev", attrs...)
        // mutate original slice after AddEvent
        attrs[0].Key = "hacked"
        attrs[0].Value = "hacked"
        span.End()
        ev := exp.GetSpans()[0].Events[0]
        if len(ev.Attributes)!=2 { panic("event attrs count mismatch") }
        if ev.Attributes[0].Key == "hacked" { panic("AddEvent must copy attributes slice, mutation leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event attr copy failed: {proc.stdout} {proc.stderr}"


def test_tracing_marshal_empty_parent_format():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok { panic("missing x-ride-trace") }
        parts := strings.Split(v, ":")
        if len(parts)!=4 { panic(fmt.Sprintf("x-ride-trace must have 4 colon-separated parts, got %d: %s", len(parts), v)) }
        if parts[2] != "" { panic(fmt.Sprintf("root ParentID should be empty, got %s in %s", parts[2], v)) }
        if parts[3] != "1" { panic("sampled flag should be 1") }
        ctx2 := observability.UnmarshalTrace(carrier)
        sc2, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace after unmarshal") }
        if sc2.ParentID != "" { panic("ParentID should stay empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal empty parent format failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_histogram_boundary_inclusive():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("boundary_hist", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(1)
        h.Observe(5)
        h.Observe(10)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="boundary_hist" {
                m := fam.Metrics[0]
                if len(m.Buckets)!=3 { panic("expected 3 buckets") }
                if m.Buckets[0].Count != 1 { panic(fmt.Sprintf("bucket 1 inclusive expected 1 got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count != 2 { panic(fmt.Sprintf("bucket 5 cumulative inclusive expected 2 got %d", m.Buckets[1].Count)) }
                if m.Buckets[2].Count != 3 { panic(fmt.Sprintf("bucket 10 cumulative inclusive expected 3 got %d", m.Buckets[2].Count)) }
                if m.Count != 3 { panic("count 3") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"histogram boundary inclusive failed: {proc.stdout} {proc.stderr}"
    )


def test_exporter_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<50;j++{
                    _, s := tracer.Start(context.Background(), fmt.Sprintf("c-%d-%d", idx, j))
                    s.End()
                }
                // concurrent GetSpans
                _ = exp.GetSpans()
            }(i)
        }
        wg.Wait()
        if len(exp.GetSpans()) != n*50 { panic(fmt.Sprintf("expected %d got %d", n*50, len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"race test failed: {proc.stdout} {proc.stderr}"


def test_metrics_collect_race():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("race_collect", observability.WithLabels(map[string]string{"a":"1"}))
        var wg sync.WaitGroup
        wg.Add(2)
        go func(){
            defer wg.Done()
            for i:=0;i<10000;i++{ c.Inc() }
        }()
        go func(){
            defer wg.Done()
            for i:=0;i<1000;i++{ prov.Collect() }
        }()
        wg.Wait()
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"race test failed: {proc.stdout} {proc.stderr}"


def test_tracing_parent_id_rules():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, root := tracer.Start(context.Background(), "root")
        rootCtx, _ := tracer.Start(context.Background(), "root2")
        _, child := tracer.Start(rootCtx, "child")
        root.End()
        child.End()
        spans := exp.GetSpans()
        var rootSpan, childSpan *observability.FinishedSpan
        for i:= range spans {
            if spans[i].Name=="root" { rootSpan=&spans[i] }
            if spans[i].Name=="child" { childSpan=&spans[i] }
        }
        if rootSpan==nil { panic("root not found") }
        if rootSpan.ParentID != "" { panic(fmt.Sprintf("root ParentID should be empty, got %s", rootSpan.ParentID)) }
        if childSpan==nil { panic("child not found") }
        if childSpan.ParentID == "" { panic("child ParentID should not be empty") }
        if childSpan.ParentID != childSpan.SpanContext.ParentID { panic("ParentID field and SpanContext.ParentID must match") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent id rules failed: {proc.stdout} {proc.stderr}"


def test_tracing_isrecording_after_end():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "rec")
        if !span.IsRecording() { panic("should be recording before End") }
        span.End()
        if span.IsRecording() { panic("should NOT be recording after End") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"isrecording after end failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_string_truncate_exact_boundary():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        exact1024 := strings.Repeat("b", 1024)
        longer := strings.Repeat("c", 1025)
        _, s1 := tracer.Start(context.Background(), "exact", observability.WithAttributes(observability.Attribute{Key:"k1", Value:exact1024}))
        s1.End()
        _, s2 := tracer.Start(context.Background(), "longer", observability.WithAttributes(observability.Attribute{Key:"k2", Value:longer}))
        s2.End()
        spans := exp.GetSpans()
        var v1, v2 string
        for _, sp := range spans {
            if sp.Name=="exact" { v1 = sp.Attributes["k1"].(string) }
            if sp.Name=="longer" { v2 = sp.Attributes["k2"].(string) }
        }
        if len(v1)!=1024 { panic(fmt.Sprintf("exact 1024 should stay 1024, got %d", len(v1))) }
        if len(v2)!=1024 { panic(fmt.Sprintf("1025 should truncate to 1024, got %d", len(v2))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"string truncate boundary failed: {proc.stdout} {proc.stderr}"
    )


def test_exporter_clear_then_reuse():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s1 := tracer.Start(context.Background(), "a")
        s1.End()
        if exp.GetCount()!=1 { panic("count 1") }
        exp.Clear()
        if exp.GetCount()!=0 { panic("clear should 0") }
        if len(exp.GetSpans())!=0 { panic("clear spans not empty") }
        _, s2 := tracer.Start(context.Background(), "b")
        s2.End()
        _, s3 := tracer.Start(context.Background(), "c")
        s3.End()
        if exp.GetCount()!=2 { panic(fmt.Sprintf("expected 2 after reuse got %d", exp.GetCount())) }
        spans := exp.GetSpans()
        if spans[0].Name!="b" || spans[1].Name!="c" { panic("reuse order wrong") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter clear then reuse failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_parent_chain_three_levels():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx1, s1 := tracer.Start(context.Background(), "grandparent")
        ctx2, s2 := tracer.Start(ctx1, "parent")
        ctx3, s3 := tracer.Start(ctx2, "child")
        s3.End()
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        if len(spans)!=3 { panic(fmt.Sprintf("expected 3 got %d", len(spans))) }
        var gp, par, child *observability.FinishedSpan
        for i:= range spans {
            switch spans[i].Name {
            case "grandparent": gp=&spans[i]
            case "parent": par=&spans[i]
            case "child": child=&spans[i]
            }
        }
        if gp==nil || par==nil || child==nil { panic("missing spans") }
        if gp.SpanContext.TraceID != par.SpanContext.TraceID || par.SpanContext.TraceID != child.SpanContext.TraceID { panic("traceID chain broken") }
        if par.ParentID != gp.SpanContext.SpanID { panic("parent ParentID != gp SpanID") }
        if child.ParentID != par.SpanContext.SpanID { panic("child ParentID != parent SpanID") }
        if child.SpanContext.ParentID != par.SpanContext.SpanID { panic("child SpanContext.ParentID mismatch") }
        _ = ctx3
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent chain three levels failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_attribute_map_non_nil():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "attr-map", observability.WithAttributes(observability.Attribute{Key:"k", Value:"v"}))
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes==nil { panic("Attributes map must be non-nil when attributes present") }
        if len(s.Attributes)!=1 { panic("expected 1 attr") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attribute map non-nil failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_event_timestamp_recent():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        before := time.Now()
        _, span := tracer.Start(context.Background(), "evt-time")
        time.Sleep(5*time.Millisecond)
        span.AddEvent("ev1")
        time.Sleep(5*time.Millisecond)
        span.AddEvent("ev2")
        span.End()
        after := time.Now()
        evs := exp.GetSpans()[0].Events
        if len(evs)!=2 { panic("expected 2 events") }
        if evs[0].Timestamp.Before(before) || evs[0].Timestamp.After(after) { panic("event timestamp out of bounds") }
        if evs[1].Timestamp.Before(evs[0].Timestamp) { panic("event timestamps not monotonic") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"event timestamp recent failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_histogram_boundary_and_sum():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("bound_sum", observability.WithBuckets([]float64{5,10}))
        h.Observe(5)
        h.Observe(10)
        h.Observe(3)
        h.Observe(12)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="bound_sum" {
                m := fam.Metrics[0]
                if m.Count!=4 { panic(fmt.Sprintf("count expected 4 got %d", m.Count)) }
                if m.Sum < 29.9 || m.Sum > 30.1 { panic(fmt.Sprintf("sum expected 30 got %f", m.Sum)) }
                if m.Buckets[0].Count!=2 { panic(fmt.Sprintf("bucket 5 should have 2 (3 and 5), got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count!=3 { panic(fmt.Sprintf("bucket 10 should have 3 (3,5,10), got %d", m.Buckets[1].Count)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"histogram boundary and sum failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_json_fields():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "msg", observability.Field{Key:"int", Value:42}, observability.Field{Key:"bool", Value:true})
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["level"]!="info" { panic("level missing") }
        if obj["service"]!="svc" { panic("service missing") }
        if obj["message"]!="msg" { panic("message missing") }
        if obj["timestamp"]==nil { panic("timestamp missing") }
        if obj["int"]!=float64(42) { panic(fmt.Sprintf("int field expected 42 got %v", obj["int"])) }
        if obj["bool"]!=true { panic("bool field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger json fields failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_service_name_empty_fallback():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "empty-svc")
        span.End()
        s := exp.GetSpans()[0]
        _ = s.ServiceName
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"service name empty fallback failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_id_generator_nil_fallback():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(nil))
        _, span := tracer.Start(context.Background(), "nilgen")
        sc := span.Context()
        if len(sc.TraceID)!=32 || len(sc.SpanID)!=16 { panic("nil IDGenerator should fallback to default") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"id generator nil fallback failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_level_default_info():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Debug(context.Background(), "debug should be filtered by default")
        logger.Info(context.Background(), "info should pass")
        out := buf.String()
        if strings.Contains(out, "debug should be filtered") { panic("default level should be info, debug filtered") }
        if !strings.Contains(out, "info should pass") { panic("info should pass at default") }
        println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger default level failed: {proc.stdout} {proc.stderr}"
    )


def test_exporter_getspans_slice_mutation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s := tracer.Start(context.Background(), "slice-mut")
        s.End()
        spans1 := exp.GetSpans()
        // append to returned slice should not affect internal
        spans1 = append(spans1, observability.FinishedSpan{Name:"injected"})
        spans2 := exp.GetSpans()
        if len(spans2)!=1 { panic(fmt.Sprintf("append to returned GetSpans slice should not affect internal, expected 1 got %d", len(spans2))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter slice mutation failed: {proc.stdout} {proc.stderr}"
    )


def test_logger_with_empty_fields():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        child := base.With()
        child.Info(context.Background(), "empty-with")
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["message"]!="empty-with" { panic("msg missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger with empty fields failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_gauge_set_and_add():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("gauge_set_add")
        g.Set(5)
        g.Add(3)
        g.Add(-2)
        g.Inc()
        g.Dec()
        g.Dec()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="gauge_set_add" {
                // 5+3-2+1-1-1 =5
                if fam.Metrics[0].Value != 5 { panic(fmt.Sprintf("expected gauge 5 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"gauge set and add failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_label_order_irrelevant_for_reuse():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("order_test", observability.WithLabels(map[string]string{"a":"1","b":"2"}))
        c2 := prov.Counter("order_test", observability.WithLabels(map[string]string{"b":"2","a":"1"}))
        c1.Inc()
        c2.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="order_test" {
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("label order should be irrelevant for reuse, expected 1 series got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 2 { panic(fmt.Sprintf("expected value 2 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"label order irrelevant failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_label_truncate_for_gauge_and_histogram():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        long := strings.Repeat("x", 500)
        g := prov.Gauge("gauge_trunc", observability.WithLabels(map[string]string{"id": long}))
        g.Set(1)
        h := prov.Histogram("hist_trunc", observability.WithLabels(map[string]string{"id": long}))
        h.Observe(1)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="gauge_trunc" {
                v := fam.Metrics[0].Labels["id"]
                if len(v) > 256 { panic(fmt.Sprintf("gauge label value should truncate to 256, got %d", len(v))) }
            }
            if fam.Name=="hist_trunc" {
                v := fam.Metrics[0].Labels["id"]
                if len(v) > 256 { panic(fmt.Sprintf("hist label value should truncate to 256, got %d", len(v))) }
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"label truncate gauge hist failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_add_attribute_after_limit_still_128():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var attrs []observability.Attribute
        for i:=0;i<128;i++{ attrs = append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value:i}) }
        _, span := tracer.Start(context.Background(), "limit-add", observability.WithAttributes(attrs...))
        // at limit now
        span.AddAttribute("extra", "should-be-ignored")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes) != 128 { panic(fmt.Sprintf("after 128 initial + 1 extra, should still be 128, got %d", len(s.Attributes))) }
        if _, ok := s.Attributes["extra"]; ok { panic("extra attribute beyond 128 should be ignored") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"add attribute after limit failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_attribute_value_truncate_add():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "truncate-add")
        long := strings.Repeat("z", 2000)
        span.AddAttribute("longkey", long)
        span.End()
        s := exp.GetSpans()[0]
        v := s.Attributes["longkey"].(string)
        if len(v)!=1024 { panic(fmt.Sprintf("AddAttribute long should truncate to 1024, got %d", len(v))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attribute value truncate add failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_context_overwrites():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc1 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        sc2 := observability.TraceContext{TraceID:"aabbccddeeff0011aabbccddeeff0011", SpanID:"aabbccddeeff0011", Sampled:false}
        ctx := observability.ContextWithTrace(context.Background(), sc1)
        ctx2 := observability.ContextWithTrace(ctx, sc2)
        got, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace") }
        if got.TraceID != sc2.TraceID { panic("ContextWithTrace should overwrite previous") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"context overwrites failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_event_copy_isolation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "ev-isolation")
        span.AddEvent("ev1")
        span.End()
        spans := exp.GetSpans()
        // mutate events slice in returned span
        spans[0].Events = append(spans[0].Events, observability.SpanEvent{Name:"injected"})
        spans2 := exp.GetSpans()
        if len(spans2[0].Events)!=1 { panic(fmt.Sprintf("mutating returned Events slice should not affect internal, expected 1 got %d", len(spans2[0].Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"event copy isolation failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_withattributes_duplicate_across_calls():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "dup-across",
            observability.WithAttributes(observability.Attribute{Key:"k", Value:"v1"}),
            observability.WithAttributes(observability.Attribute{Key:"k", Value:"v2"}),
        )
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["k"] != "v2" { panic(fmt.Sprintf("duplicate across WithAttributes calls last wins expected v2 got %v", s.Attributes["k"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withattributes duplicate across calls failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_marshal_nil_carrier():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("MarshalTrace nil carrier should not panic, got %v", r)) }
        }()
        observability.MarshalTrace(context.Background(), nil)
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        observability.Inject(ctx, nil)
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal nil carrier failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_unmarshal_invalid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        cases := []map[string]string{
            nil,
            {},
            {"x-ride-trace":""},
            {"x-ride-trace":"bad"},
            {"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:0102030405060708"},
            {"x-ride-trace":"zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz:0102030405060708::1"},
            {"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:zzzzzzzzzzzzzzzz::1"},
            {"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:0102030405060708:zzzzzzzzzzzzzzzz:1"},
        }
        for i, c := range cases {
            ctx := observability.UnmarshalTrace(c)
            if _, ok := observability.TraceFromContext(ctx); ok {
                panic(fmt.Sprintf("case %d should yield no trace for invalid carrier %v", i, c))
            }
            ctx2 := observability.Extract(c)
            if _, ok := observability.SpanContextFromContext(ctx2); ok {
                panic(fmt.Sprintf("Extract case %d should yield no trace", i))
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"unmarshal invalid failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_withattributes_nil():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s1 := tracer.Start(context.Background(), "nilattrs", observability.WithAttributes())
        s1.End()
        _, s2 := tracer.Start(context.Background(), "nilattrs2")
        s2.End()
        if len(exp.GetSpans())!=2 { panic("should have 2") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withattributes nil failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_counter_type_conflict():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("conflict_metric")
        c.Inc()
        g := prov.Gauge("conflict_metric")
        g.Set(100)
        h := prov.Histogram("conflict_metric")
        h.Observe(5)
        fams := prov.Collect()
        var families int
        var typ string
        for _, fam := range fams {
            if fam.Name=="conflict_metric" {
                families++
                typ = fam.Type
            }
        }
        if families!=1 { panic(fmt.Sprintf("conflict metric same name should be 1 family, got %d", families)) }
        if typ!="counter" { panic(fmt.Sprintf("first type counter should win, got %s", typ)) }
        // value should be 1, not affected by gauge/histogram no-op
        for _, fam := range fams {
            if fam.Name=="conflict_metric" {
                if fam.Metrics[0].Value != 1 { panic(fmt.Sprintf("counter value should be 1, got %f", fam.Metrics[0].Value)) }
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"counter type conflict failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_flags_zero_when_not_sampled():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"", Sampled:false}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok { panic("should marshal even when not sampled? Flags 0 but still valid") }
        if v[len(v)-1] != '0' { panic(fmt.Sprintf("sampled false should have :0, got %s", v)) }
        sc2 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false}
        if sc2.Flags != 0 { panic(fmt.Sprintf("Flags should be 0 when Sampled false, but struct may have Flag 0 - check Context() returns with Flags 0")) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"flags zero when not sampled failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_concurrent_end_exactly_once():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "concurrent-end-once")
        var wg sync.WaitGroup
        for i:=0;i<50;i++{
            wg.Add(1)
            go func(){
                defer wg.Done()
                span.End()
            }()
        }
        wg.Wait()
        spans := exp.GetSpans()
        if len(spans)!=1 {
            panic(fmt.Sprintf("Concurrent End() must export exactly once, got %d", len(spans)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrent End exactly once failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_export_snapshots_span():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "snapshot-test")
        span.AddAttribute("k1", "v1")
        var wg sync.WaitGroup
        wg.Add(1)
        go func(){
            defer wg.Done()
            for i:=0;i<100;i++{
                span.AddAttribute(fmt.Sprintf("k%d", i+2), i)
            }
        }()
        span.End()
        wg.Wait()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span got %d", len(spans))) }
        // The exported span must be a snapshot - mutating after End must not affect it
        // AddAttribute after End is defined as no-op, so subsequent GetSpans must still return original snapshot
        // Also check that the exporter received a stable copy (no race)
        s := spans[0]
        if s.Attributes == nil { panic("Attributes nil") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"export snapshots span failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_racing_add_after_end_noop_atomic():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "race-add-after-end")
        var wg sync.WaitGroup
        // 20 goroutines racing AddAttribute/AddEvent with End
        for i:=0;i<20;i++{
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                span.AddAttribute(fmt.Sprintf("race-k%d", idx), idx)
                span.AddEvent(fmt.Sprintf("race-ev-%d", idx))
            }(i)
        }
        wg.Add(1)
        go func(){
            defer wg.Done()
            span.End()
        }()
        wg.Wait()
        // After all, End should have been called and exporter should have exactly 1 span, no panic, no race
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span after racing End and Add, got %d", len(spans))) }
        // After End, further Add should be no-op and not affect exported count
        span.AddAttribute("after", "should-not-appear")
        if len(exp.GetSpans()) != 1 { panic("Add after End should be no-op") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"racing add after end atomic failed: {proc.stdout} {proc.stderr}"
    )

def test_metrics_withlabels_defensive_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        labels := map[string]string{"env":"prod"}
        c := prov.Counter("defcopy_test", observability.WithLabels(labels))
        // mutate original map after creation
        labels["env"]="hacked"
        labels["new"]="injected"
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="defcopy_test" {
                if len(fam.Metrics)!=1 { panic("expected 1") }
                v := fam.Metrics[0].Labels["env"]
                if v!="prod" { panic(fmt.Sprintf("WithLabels must copy map, expected prod got %s", v)) }
                if _, ok := fam.Metrics[0].Labels["new"]; ok { panic("injected label leaked via defensive copy failure") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withlabels defensive copy failed: {proc.stdout} {proc.stderr}"


def test_metrics_withbuckets_defensive_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        buckets := []float64{1,5,10}
        h := prov.Histogram("bucket_defcopy", observability.WithBuckets(buckets))
        // mutate original slice
        buckets[0]=100
        buckets = append(buckets, 20)
        h.Observe(2)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="bucket_defcopy" {
                bs := fam.Metrics[0].Buckets
                if len(bs)!=3 { panic(fmt.Sprintf("expected 3 buckets got %d", len(bs))) }
                if bs[0].UpperBound != 1 { panic(fmt.Sprintf("WithBuckets must copy slice, expected 1 got %f", bs[0].UpperBound)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withbuckets defensive copy failed: {proc.stdout} {proc.stderr}"


def test_metrics_label_truncation_reuse_after_truncate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        long1 := strings.Repeat("a", 500)
        long2 := strings.Repeat("a", 256) + strings.Repeat("b", 244) // same first 256 as long1
        // first 256 chars of both are all 'a's
        c1 := prov.Counter("trunc_reuse", observability.WithLabels(map[string]string{"id": long1}))
        c2 := prov.Counter("trunc_reuse", observability.WithLabels(map[string]string{"id": long2}))
        c1.Inc()
        c2.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="trunc_reuse" {
                // after truncation both should be same series, so only 1 metric
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("truncation should cause reuse, expected 1 series got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 2 { panic(fmt.Sprintf("expected value 2 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"label truncation reuse failed: {proc.stdout} {proc.stderr}"


def test_metrics_collect_buckets_deep_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("buckets_deepcopy", observability.WithBuckets([]float64{1,5}))
        h.Observe(2)
        fams1 := prov.Collect()
        if len(fams1)==0 { panic("empty") }
        // mutate returned buckets
        fams1[0].Metrics[0].Buckets[0].Count = 9999
        fams1[0].Metrics[0].Buckets[0].UpperBound = 9999
        fams2 := prov.Collect()
        for _, fam := range fams2 {
            if fam.Name=="buckets_deepcopy" {
                if fam.Metrics[0].Buckets[0].Count == 9999 { panic("Collect must return deep copy of Buckets") }
                if fam.Metrics[0].Buckets[0].UpperBound == 9999 { panic("Buckets UpperBound deep copy failed") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"collect buckets deep copy failed: {proc.stdout} {proc.stderr}"


def test_gauge_ignore_nan_inf():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("gauge_nan")
        g.Set(10)
        g.Set(math.NaN())
        g.Add(math.Inf(1))
        g.Add(math.NaN())
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="gauge_nan" {
                if fam.Metrics[0].Value != 10 { panic(fmt.Sprintf("Gauge Set/Add should ignore NaN/Inf, expected 10 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge ignore nan inf failed: {proc.stdout} {proc.stderr}"


def test_tracing_start_nil_context():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("Start with nil ctx should not panic, got %v", r)) }
        }()
        ctx, span := tracer.Start(nil, "nil-ctx")
        if ctx==nil { panic("Start(nil, ...) should return non-nil context") }
        if _, ok := observability.TraceFromContext(ctx); !ok { panic("ctx should have trace") }
        span.End()
        if len(exp.GetSpans())!=1 { panic("expected 1 span") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"start nil context failed: {proc.stdout} {proc.stderr}"


def test_tracing_contextwithtrace_nil():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true, Flags:1}
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("ContextWithTrace(nil, ...) should not panic: %v", r)) }
        }()
        ctx := observability.ContextWithTrace(nil, tc)
        if ctx==nil { panic("ContextWithTrace(nil) must return non-nil") }
        got, ok := observability.TraceFromContext(ctx)
        if !ok || got.TraceID!=tc.TraceID { panic("trace not stored") }
        // TraceFromContext nil
        _, ok = observability.TraceFromContext(nil)
        if ok { panic("TraceFromContext(nil) should return false") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"contextwithtrace nil failed: {proc.stdout} {proc.stderr}"


def test_tracing_context_returns_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "copy-ctx")
        sc := span.Context()
        originalID := sc.TraceID
        sc.TraceID = "ffffffffffffffffffffffffffffffff"
        sc2 := span.Context()
        if sc2.TraceID != originalID { panic("Span.Context() must return defensive copy") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context returns copy failed: {proc.stdout} {proc.stderr}"


def test_tracing_duplicate_key_not_count_toward_limit():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "dup-limit")
        // 200 adds of same key should count as 1 distinct, not exhaust limit
        for i:=0;i<200;i++{
            span.AddAttribute("samekey", i)
        }
        // now add 127 more distinct keys, total distinct should be 128
        for i:=0;i<127;i++{
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        // At this point we have 1 +127 =128 distinct, at limit
        // Adding new distinct should be ignored
        span.AddAttribute("extra", "ignored")
        // Overwriting existing samekey should still work even after limit
        span.AddAttribute("samekey", "final")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)!=128 { panic(fmt.Sprintf("expected 128 attrs, got %d", len(s.Attributes))) }
        if s.Attributes["samekey"] != "final" { panic(fmt.Sprintf("duplicate key after limit should overwrite, expected final got %v", s.Attributes["samekey"])) }
        if _, ok := s.Attributes["extra"]; ok { panic("extra beyond limit should be ignored") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"duplicate key not count toward limit failed: {proc.stdout} {proc.stderr}"


def test_tracing_empty_attribute_key_ignored():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "empty-key", observability.WithAttributes(observability.Attribute{Key:"", Value:"should-ignore"}))
        span.AddAttribute("", "also-ignore")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)!=0 { panic(fmt.Sprintf("empty attribute keys should be ignored, got %d attrs", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"empty attribute key ignored failed: {proc.stdout} {proc.stderr}"


def test_tracing_flags_preserved_via_propagation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"aabbccddeeff0011", Sampled:true, Flags:1}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        ctx2 := observability.UnmarshalTrace(carrier)
        sc2, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace") }
        if sc2.Flags != 1 { panic(fmt.Sprintf("Flags should be 1 after unmarshal sampled true, got %d", sc2.Flags)) }
        if !sc2.Sampled { panic("Sampled should be true") }
        // sampled false
        scFalse := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false, Flags:0}
        ctxF := observability.ContextWithTrace(context.Background(), scFalse)
        carrierF := map[string]string{}
        observability.MarshalTrace(ctxF, carrierF)
        ctxF2 := observability.UnmarshalTrace(carrierF)
        scF2, _ := observability.TraceFromContext(ctxF2)
        if scF2.Flags != 0 { panic(fmt.Sprintf("Flags should be 0 when sampled false, got %d", scF2.Flags)) }
        if scF2.Sampled { panic("Sampled should be false") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"flags preserved via propagation failed: {proc.stdout} {proc.stderr}"


def test_exporter_event_attributes_deep_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "ev-deepcopy")
        span.AddEvent("ev", observability.Attribute{Key:"k", Value:"v"})
        span.End()
        spans1 := exp.GetSpans()
        // mutate nested event attributes
        if len(spans1[0].Events)==0 { panic("no events") }
        spans1[0].Events[0].Attributes[0].Key = "hacked"
        spans1[0].Events[0].Attributes[0].Value = "hacked"
        spans1[0].Events[0].Name = "hacked"
        spans2 := exp.GetSpans()
        ev := spans2[0].Events[0]
        if ev.Name=="hacked" { panic("Events slice deep copy failed") }
        if ev.Attributes[0].Key=="hacked" { panic("Event Attributes deep copy failed") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"exporter event attributes deep copy failed: {proc.stdout} {proc.stderr}"


def test_logger_with_immutability_and_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        // concurrent With chains
        for i:=0;i<20;i++{
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                child := base.With(observability.Field{Key: fmt.Sprintf("k%d", idx), Value: idx})
                child.Info(context.Background(), fmt.Sprintf("msg-%d", idx))
            }(i)
        }
        wg.Wait()
        // also test that base not mutated
        _ = base.With(observability.Field{Key:"env", Value:"prod"})
        // base should not have env
        // we check via logging base after With
        buf.Reset()
        base.Info(context.Background(), "base-check")
        out := buf.String()
        if len(out)==0 { panic("no output") }
        if contains(out, "env") {
            panic("base logger mutated by With")
        }
        fmt.Println("OK")
    }
    func contains(s, substr string) bool {
        return len(s)>=len(substr) && (func() bool {
            for i:=0;i<=len(s)-len(substr);i++{
                if s[i:i+len(substr)]==substr { return true }
            }
            return false
        })()
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, f"logger with immutability concurrent failed: {proc.stdout} {proc.stderr}"


def test_histogram_dedup_buckets():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("dedup_buckets", observability.WithBuckets([]float64{5,1,5,10,1}))
        h.Observe(3)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="dedup_buckets" {
                buckets := fam.Metrics[0].Buckets
                // should be deduped and sorted: [1,5,10] => 3 buckets
                if len(buckets)!=3 { panic(fmt.Sprintf("expected 3 deduped buckets got %d", len(buckets))) }
                if buckets[0].UpperBound!=1 || buckets[1].UpperBound!=5 || buckets[2].UpperBound!=10 {
                    panic(fmt.Sprintf("dedup sorted buckets wrong: %v", buckets))
                }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram dedup buckets failed: {proc.stdout} {proc.stderr}"


def test_tracing_parent_id_consistency():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx1, s1 := tracer.Start(context.Background(), "root")
        _, s2 := tracer.Start(ctx1, "child")
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        var root, child *observability.FinishedSpan
        for i:= range spans {
            if spans[i].Name=="root" { root=&spans[i] }
            if spans[i].Name=="child" { child=&spans[i] }
        }
        if root==nil || child==nil { panic("missing") }
        if root.ParentID != "" { panic("root ParentID should be empty") }
        if root.SpanContext.ParentID != "" { panic("root SpanContext.ParentID should be empty") }
        if root.ParentID != root.SpanContext.ParentID { panic("root ParentID and SpanContext.ParentID mismatch") }
        if child.ParentID == "" { panic("child ParentID empty") }
        if child.ParentID != child.SpanContext.ParentID { panic(fmt.Sprintf("child ParentID %s != SpanContext.ParentID %s", child.ParentID, child.SpanContext.ParentID)) }
        if child.ParentID != root.SpanContext.SpanID { panic("child ParentID should equal parent SpanID") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent id consistency failed: {proc.stdout} {proc.stderr}"


def test_logger_output_nil_fallback():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("WithOutput(nil) should not panic: %v", r)) }
        }()
        logger := observability.NewLogger("svc", observability.WithOutput(nil))
        logger.Info(context.Background(), "nil-output-test")
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger output nil fallback failed: {proc.stdout} {proc.stderr}"

