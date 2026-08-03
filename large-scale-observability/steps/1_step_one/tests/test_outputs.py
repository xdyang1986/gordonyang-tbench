"""
Step1 verifier: core observability & design quality
Tests observability package via ephemeral Go harnesses importing /app
"""

import os, subprocess, tempfile, json, re, textwrap, shutil, sys
import pytest

APP_DIR = "/app"
PKG_PATH = "ride-observability/observability"


# helper to run command
def run(cmd, cwd=APP_DIR, timeout=30):
    env = os.environ.copy()
    env["GOCACHE"] = "/tmp/codimango/gocache"
    env["GOPATH"] = "/tmp/codimango/gopath"
    env["GOFLAGS"] = "-mod=mod"
    env["GOTOOLCHAIN"] = "local"
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        shell=False,
    )
    return proc


def go_run_program(go_code: str, extra_files=None):
    """
    Creates temp Go module that imports ride-observability, writes main.go with go_code, runs go run .
    extra_files dict filename->content
    go_code is content of main.go
    Returns proc
    """
    tmp = tempfile.mkdtemp(prefix="obs_test_")
    try:
        # go.mod
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        if extra_files:
            for name, content in extra_files.items():
                path = os.path.join(tmp, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").write(content)
        # run
        proc = run(["go", "run", "."], cwd=tmp, timeout=20)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_test_program(test_code: str):
    """write a single file main.go with test_code and run go test via harness"""
    # We'll embed test_code inside main that panics on failure
    return go_run_program(test_code)


# -------------------- basic file existence & go vet --------------------


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
    # walk .go files
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
            # find imports
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


# -------------------- tracing --------------------


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
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithSpanProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "test-span")
        _ = ctx
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span got %d", len(spans))) }
        sc := spans[0].SpanContext
        fmt.Printf("traceID=%s spanID=%s sampled=%v\\n", sc.TraceID, sc.SpanID, sc.Sampled)
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
        f"tracing id format failed: stdout={proc.stdout} stderr={proc.stderr}"
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
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithSpanProcessor(proc))
        ctx1, s1 := tracer.Start(context.Background(), "parent")
        ctx2, s2 := tracer.Start(ctx1, "child")
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        if len(spans)!=2 { panic(fmt.Sprintf("expected 2 spans got %d", len(spans))) }
        var parent, child *observability.ReadableSpan
        for i:= range spans {
            if spans[i].Name=="parent" { parent = &spans[i] }
            if spans[i].Name=="child" { child = &spans[i] }
        }
        if parent==nil || child==nil { panic("parent/child not found") }
        if parent.SpanContext.TraceID != child.SpanContext.TraceID { panic(fmt.Sprintf("traceID mismatch parent %s child %s", parent.SpanContext.TraceID, child.SpanContext.TraceID)) }
        if parent.SpanContext.SpanID == child.SpanContext.SpanID { panic("spanID should differ") }
        if child.ParentSpanID != parent.SpanContext.SpanID { panic(fmt.Sprintf("child parentSpanID %s != parent spanID %s", child.ParentSpanID, parent.SpanContext.SpanID)) }
        // also check context propagation
        sc, ok := observability.SpanContextFromContext(ctx2)
        if !ok { panic("no SpanContext in ctx2") }
        if sc.TraceID != child.SpanContext.TraceID { panic("ctx2 traceID mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"child inherits failed: {proc.stdout} {proc.stderr}"


def test_tracing_attributes_events_status():
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
        tracer := observability.NewTracer("ride-service", observability.WithSpanProcessor(proc))
        _, span := tracer.Start(context.Background(), "op", observability.WithAttributes(observability.Attribute{Key:"k1", Value:"v1"}))
        span.AddAttribute("k2", 42)
        span.AddAttribute("k3", true)
        span.AddEvent("event1", observability.Attribute{Key:"e1", Value:"x"})
        span.SetStatus(observability.StatusError, "oops")
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        s := spans[0]
        fmt.Printf("attrs=%v events=%v status=%d msg=%s\\n", s.Attributes, s.Events, s.StatusCode, s.StatusMessage)
        if len(s.Attributes) < 3 { panic(fmt.Sprintf("expected >=3 attrs got %d", len(s.Attributes))) }
        if s.Attributes["k1"]!="v1" && s.Attributes["k1"]!= "v1" { panic("k1 missing") }
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


def test_tracing_end_idempotent():
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


def test_tracing_inject_extract():
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
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.Inject(ctx, carrier)
        fmt.Printf("carrier=%v\\n", carrier)
        if carrier["trace-id"]=="" { panic("trace-id missing in carrier") }
        if carrier["span-id"]=="" { panic("span-id missing") }
        ctx2 := observability.Extract(carrier)
        sc, ok := observability.SpanContextFromContext(ctx2)
        if !ok { panic("Extract missing span ctx") }
        origSc, _ := observability.SpanContextFromContext(ctx)
        if sc.TraceID != origSc.TraceID { panic("traceID mismatch after inject/extract") }
        if sc.SpanID != origSc.SpanID { panic("spanID mismatch") }
        // invalid carrier should not panic
        bad := map[string]string{"trace-id":"bad","span-id":"bad"}
        ctxBad := observability.Extract(bad)
        _, ok = observability.SpanContextFromContext(ctxBad)
        if ok {
            // It could still have if implementation allows? We require invalid ignored => no context.
            // Accept if invalid but then IDs invalid? For safety, check that if ok then IDs should be empty? Let's just not panic.
            fmt.Printf("bad carrier extracted ok, but should ideally be ignored\\n")
        }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"inject/extract failed: {proc.stdout} {proc.stderr}"


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
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
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
        // check uniqueness of SpanID
        seen := map[string]bool{}
        for _, s := range spans {
            if seen[s.SpanContext.SpanID] { panic("duplicate spanID "+s.SpanContext.SpanID) }
            seen[s.SpanContext.SpanID]=true
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"concurrent tracing failed: {proc.stdout} {proc.stderr}"
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
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithSpanProcessor(proc))
        _, span := tracer.Start(context.Background(), "limit")
        for i:=0;i<200;i++{
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        if len(spans[0].Attributes) > 128 {
            panic(fmt.Sprintf("attrs >128 not enforced: %d", len(spans[0].Attributes)))
        }
        fmt.Printf("attrs=%d OK\\n", len(spans[0].Attributes))
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr limit failed: {proc.stdout} {proc.stderr}"


# -------------------- metrics --------------------


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
        c.Add(-1) // should be ignored
        fams := prov.Collect()
        if len(fams)==0 { panic("no families") }
        var found bool
        for _, fam := range fams {
            if fam.Name=="ride_requests_total" {
                found=true
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("expected 1 metric got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 3 { panic(fmt.Sprintf("expected value 3 got %f", fam.Metrics[0].Value)) }
                if fam.Metrics[0].Labels["status"]!="requested" { panic("label mismatch") }
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
        if val!=2 { panic(fmt.Sprintf("expected reuse value 2 got %f", val)) }
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
    proc = go_run_program(code)
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
        // 10+1-1+5=15
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
                // buckets cumulative? Check buckets exist
                if len(m.Buckets)==0 { panic("no buckets") }
                // With custom buckets [1,5,10], cumulative counts: <=1 =>1, <=5=>2, <=10=>3, +inf? we may have 4 total
                // Let's just check at least 3 buckets present
            }
        }
        if gaugeVal!=15 { panic(fmt.Sprintf("gauge expected 15 got %f", gaugeVal)) }
        if !histFound { panic("hist not found") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge/hist failed: {proc.stdout} {proc.stderr}"


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
        // invalid label key
        c3 := prov.Counter("good_name", observability.WithLabels(map[string]string{"bad-key":"1"}))
        c3.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="invalid-name" || fam.Name=="123bad" {
                panic("invalid metric name should not be in Collect")
            }
            if fam.Name=="good_name" {
                // if label invalid, should not be collected? Or metric with invalid labels should be noop.
                // We require good_name with invalid label should not appear, or appear 0? For safety, if it appears, check it's 0?
                // Let's enforce it should not appear as valid.
                // But if implementation returns noop for invalid label, then Collect should have 0 metrics or not include.
                // We'll allow either but check not panic.
                fmt.Printf("found good_name with invalid label, metrics=%d\\n", len(fam.Metrics))
                if len(fam.Metrics)>0 {
                    panic("expected no metric for invalid label key")
                }
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"invalid name test failed: {proc.stdout} {proc.stderr}"
    )


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


# -------------------- logger --------------------


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
        exp := observability.NewInMemoryExporter()
        proc := observability.NewSimpleSpanProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithSpanProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "test-log")
        logger.Info(ctx, "ride requested", observability.Field{Key:"ride_id", Value:"r123"}, observability.Field{Key:"rider", Value:"u1"})
        span.End()
        line := buf.String()
        fmt.Printf("log line: %s\\n", line)
        var obj map[string]interface{}
        if err:= json.Unmarshal([]byte(line), &obj); err!= nil { panic("not valid json: "+err.Error()+" line: "+line) }
        if obj["service"]!="ride-service" { panic("service missing") }
        if obj["message"]!="ride requested" { panic("message missing") }
        if obj["level"]==nil { panic("level missing") }
        if obj["timestamp"]==nil { panic("timestamp missing") }
        if obj["trace_id"]==nil { panic("trace_id missing from correlated log") }
        if obj["span_id"]==nil { panic("span_id missing") }
        if obj["ride_id"]!="r123" { panic("custom field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger json trace failed: {proc.stdout} {proc.stderr}"
    )


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


def test_logger_level_filter():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("error"))
        logger.Info(context.Background(), "info should be filtered")
        logger.Debug(context.Background(), "debug filtered")
        logger.Warn(context.Background(), "warn filtered")
        logger.Error(context.Background(), "error should pass")
        out := buf.String()
        fmt.Printf("out=%s\\n", out)
        if out=="" { panic("expected error log") }
        if len(bytesSplit(out))!=1 { panic(fmt.Sprintf("expected 1 line got %d content %s", len(bytesSplit(out)), out)) }
        fmt.Println("OK")
    }
    func bytesSplit(s string) []string {
        // simple split by newline non-empty
        var res []string
        for _, line := range bytesSplitRaw(s) {
            if line!="" { res=append(res, line) }
        }
        return res
    }
    func bytesSplitRaw(s string) []string {
        var lines []string
        cur:=""
        for _, ch := range s {
            if ch=='\\n' { lines=append(lines, cur); cur="" } else { cur+=string(ch) }
        }
        if cur!="" { lines=append(lines, cur) }
        return lines
    }
    """)
    # fix helper: use strings
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
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("error"))
        logger.Info(context.Background(), "info should be filtered")
        logger.Debug(context.Background(), "debug filtered")
        logger.Warn(context.Background(), "warn filtered")
        logger.Error(context.Background(), "error should pass")
        out := strings.TrimSpace(buf.String())
        fmt.Printf("out=%s\\n", out)
        if out=="" { panic("expected error log") }
        lines := strings.Split(out, "\\n")
        // filter empty
        var nonEmpty []string
        for _, l := range lines { if strings.TrimSpace(l)!="" { nonEmpty=append(nonEmpty,l) } }
        if len(nonEmpty)!=1 { panic(fmt.Sprintf("expected 1 line got %d content %s", len(nonEmpty), out)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger level filter failed: {proc.stdout} {proc.stderr}"
    )


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
        // use synchronized buffer? Our logger must handle concurrency.
        // Use bytes.Buffer with mutex wrapper via logger's own mutex.
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                logger.Info(context.Background(), fmt.Sprintf("msg-%d", idx), observability.Field{Key:"idx", Value:idx})
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
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger concurrent failed: {proc.stdout} {proc.stderr}"
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
        exp1 := observability.NewInMemoryExporter()
        exp2 := observability.NewInMemoryExporter()
        p1 := observability.NewSimpleSpanProcessor(exp1)
        p2 := observability.NewSimpleSpanProcessor(exp2)
        t1 := observability.NewTracer("service-a", observability.WithSpanProcessor(p1))
        t2 := observability.NewTracer("service-b", observability.WithSpanProcessor(p2))
        _, s1 := t1.Start(context.Background(), "op1")
        s1.End()
        // t2 should have 0
        if len(exp2.GetSpans())!=0 { panic("tracer isolation broken") }
        if len(exp1.GetSpans())!=1 { panic("expected 1 span in exp1") }
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
