"""
Black-box tests for Flink-like stream aggregation engine.

Builds Go program and drives via stdin/stdout.

Semantics under test:
  * streams with watermarks, tumbling and sliding windows per key
  * aggregations SUM COUNT MIN MAX AVG
  * late handling LATE, watermark monotonicity
  * query NULL vs ERROR vs result
  * delete cascade, list sorted
  * durable mode STREAM_STATE_DIR/stream.log: CRC-framed log, torn-tail truncation, atomic compaction
"""

import os
import struct
import subprocess
import zlib
from pathlib import Path
import random

import pytest

APP = "/app"
BIN = "/tmp/agent_agg"

GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}


def _find_main_pkg():
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                try:
                    if "func main(" in open(os.path.join(root, f)).read():
                        rel = os.path.relpath(root, APP)
                        return "." if rel == "." else "./" + rel
                except OSError:
                    pass
    return None


@pytest.fixture(scope="session", autouse=True)
def built():
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(
            ["go", "mod", "init", "aggregator"],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
            text=True,
        )

    def _build(pkg):
        return subprocess.run(
            ["go", "build", "-o", BIN, pkg],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
            text=True,
            timeout=240,
        )

    r = _build(".")
    if r.returncode != 0:
        pkg = _find_main_pkg()
        if pkg and pkg != ".":
            r = _build(pkg)
    assert r.returncode == 0, (
        f"`go build` failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert os.path.exists(BIN), "build produced no binary"
    yield


def _chmod_no_access():
    try:
        os.chmod(__file__, 0o000)
    except Exception:
        pass


def _chmod_restore():
    try:
        os.chmod(__file__, 0o644)
    except Exception:
        pass


def run(stdin, timeout=20, state_dir=None):
    env = {k: v for k, v in os.environ.items() if k != "STREAM_STATE_DIR"}
    if state_dir is not None:
        env["STREAM_STATE_DIR"] = state_dir
    _chmod_no_access()
    try:
        return subprocess.run(
            [BIN], input=stdin, capture_output=True, text=True, timeout=timeout, env=env
        )
    finally:
        _chmod_restore()


def lines(out):
    return [l for l in out.strip().split("\n") if l != ""]


def record(payload: str) -> bytes:
    b = payload.encode()
    return struct.pack("<II", len(b), zlib.crc32(b) & 0xFFFFFFFF) + b


# --------------------------------------------------------------------------
# Basic functionality
# --------------------------------------------------------------------------


def test_basic_tumbling_sum():
    stdin = """CREATE_STREAM orders 0
DEFINE_TUMBLING_WINDOW w1 orders 10 SUM 1
INGEST orders alice 5 2 2
INGEST orders alice 7 8 3
INGEST orders bob 3 12 4
ADVANCE_WATERMARK orders 10 5
QUERY w1 alice 0 6
QUERY w1 bob 0 7
QUERY w1 bob 10 8
LIST_STREAMS 9
LIST_WINDOWS 10
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    assert lines(r.stdout) == [
        "OK",
        "OK",
        "OK",
        "12",
        "NULL",
        "NULL",
        "orders",
        "w1",
    ]


def test_tumbling_count_min_max_avg():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW wcount s 10 COUNT 1
DEFINE_TUMBLING_WINDOW wsum s 10 SUM 2
DEFINE_TUMBLING_WINDOW wmin s 10 MIN 3
DEFINE_TUMBLING_WINDOW wmax s 10 MAX 4
DEFINE_TUMBLING_WINDOW wavg s 10 AVG 5
INGEST s k 5 1 6
INGEST s k 15 2 7
INGEST s k -3 8 8
ADVANCE_WATERMARK s 10 9
QUERY wcount k 0 10
QUERY wsum k 0 11
QUERY wmin k 0 12
QUERY wmax k 0 13
QUERY wavg k 0 14
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    assert lines(r.stdout) == ["OK", "OK", "OK", "3", "17", "-3", "15", "5"]


def test_sliding_window_count():
    stdin = """CREATE_STREAM s 0
DEFINE_SLIDING_WINDOW win s 10 5 COUNT 1
INGEST s k 1 2 2
INGEST s k 1 7 3
INGEST s k 1 12 4
ADVANCE_WATERMARK s 15 5
QUERY win k 0 6
QUERY win k 5 7
QUERY win k 10 8
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    assert lines(r.stdout) == ["OK", "OK", "OK", "2", "2", "NULL"]


def test_sliding_window_sum():
    stdin = """CREATE_STREAM s 0
DEFINE_SLIDING_WINDOW w s 10 5 SUM 1
INGEST s a 10 0 2
INGEST s a 20 6 3
INGEST s a 30 9 4
ADVANCE_WATERMARK s 10 5
ADVANCE_WATERMARK s 15 6
QUERY w a 0 7
QUERY w a 5 8
"""
    r = run(stdin)
    assert r.returncode == 0
    # Event0: time0 in [0,10) => sum10
    # Event6: time6 in [0,10) and [5,15) => [0,10) sum=10+20=30? Actually second event 20 at time6, plus third event 30 at 9 also in [0,10) => sum 10+20+30=60 for [0,10)
    # [5,15) contains events at 6 and 9 => 20+30=50
    # Watermark 15 closes both
    assert lines(r.stdout) == ["OK", "OK", "OK", "60", "50"]


def test_late_handling():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST s k 10 5 2
ADVANCE_WATERMARK s 10 3
INGEST s k 20 5 4
INGEST s k 30 15 5
ADVANCE_WATERMARK s 20 6
QUERY w k 0 7
QUERY w k 10 8
"""
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["OK", "LATE", "OK", "10", "30"]


def test_watermark_monotonic_error():
    stdin = """CREATE_STREAM s 0
ADVANCE_WATERMARK s 10 1
ADVANCE_WATERMARK s 5 2
ADVANCE_WATERMARK s 10 3
"""
    r = run(stdin)
    assert r.returncode == 0
    # first advance OK (no output), second decreasing -> ERROR, third same as current 10 -> no-op no ERROR? Our spec says same watermark no-op not ERROR.
    # So outputs: ERROR for second
    assert lines(r.stdout) == ["ERROR"]


def test_query_not_closed_returns_null():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST s k 5 2 2
QUERY w k 0 3
ADVANCE_WATERMARK s 5 4
QUERY w k 0 5
ADVANCE_WATERMARK s 10 6
QUERY w k 0 7
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "NULL", "NULL", "5"]


def test_query_no_data_returns_null():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
ADVANCE_WATERMARK s 20 1
QUERY w k 0 2
QUERY w k 10 3
"""
    r = run(stdin)
    assert lines(r.stdout) == ["NULL", "NULL"]


def test_alignment_error():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
DEFINE_SLIDING_WINDOW w2 s 10 5 SUM 2
ADVANCE_WATERMARK s 20 3
QUERY w k 5 4
QUERY w2 k 3 5
QUERY w k 0 6
"""
    r = run(stdin)
    # w requires start %10==0, 5 -> ERROR, w2 requires %5==0, 3 -> ERROR, last NULL
    assert lines(r.stdout) == ["ERROR", "ERROR", "NULL"]


def test_delete_stream_cascade():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST s k 5 1 2
DELETE_STREAM s 3
LIST_STREAMS 4
LIST_WINDOWS 5
QUERY w k 0 6
CREATE_STREAM s 7
DEFINE_TUMBLING_WINDOW w s 10 SUM 8
LIST_STREAMS 9
LIST_WINDOWS 10
"""
    r = run(stdin)
    assert r.returncode == 0
    out = lines(r.stdout)
    # INGEST OK, then after delete LIST_STREAMS NONE, LIST_WINDOWS NONE, QUERY ERROR (window gone), then after recreate lists
    assert out == ["OK", "NONE", "NONE", "ERROR", "s", "w"]


def test_delete_window():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w1 s 10 SUM 1
DEFINE_TUMBLING_WINDOW w2 s 10 SUM 2
INGEST s k 5 1 3
ADVANCE_WATERMARK s 10 4
QUERY w1 k 0 5
DELETE_WINDOW w1 6
LIST_WINDOWS 7
QUERY w1 k 0 8
QUERY w2 k 0 9
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "5", "w2", "ERROR", "5"]


def test_list_sorted():
    stdin = """CREATE_STREAM z 0
CREATE_STREAM a 1
CREATE_STREAM m 2
DEFINE_TUMBLING_WINDOW wz z 10 SUM 3
DEFINE_TUMBLING_WINDOW wa a 10 SUM 4
LIST_STREAMS 5
LIST_WINDOWS 6
LIST_WINDOWS a 7
LIST_WINDOWS m 8
"""
    r = run(stdin)
    assert lines(r.stdout) == ["a,m,z", "wa,wz", "wa", "NONE"]


def test_list_windows_filtered_error():
    stdin = """CREATE_STREAM s 0
LIST_WINDOWS missing 1
"""
    r = run(stdin)
    assert lines(r.stdout) == ["ERROR"]


def test_ingest_error_missing_stream():
    stdin = """INGEST missing k 5 10 0
CREATE_STREAM s 1
INGEST s k 5 10 2
"""
    r = run(stdin)
    assert lines(r.stdout) == ["ERROR", "OK"]


def test_define_error_missing_stream():
    stdin = """DEFINE_TUMBLING_WINDOW w missing 10 SUM 0
DEFINE_SLIDING_WINDOW w2 missing 10 5 SUM 1
CREATE_STREAM s 2
DEFINE_TUMBLING_WINDOW w s 10 SUM 3
DEFINE_TUMBLING_WINDOW w s 10 SUM 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["ERROR", "ERROR", "ERROR"]


def test_advance_watermark_error_missing():
    stdin = """ADVANCE_WATERMARK missing 10 0
CREATE_STREAM s 1
ADVANCE_WATERMARK s 10 2
"""
    r = run(stdin)
    assert lines(r.stdout) == ["ERROR"]


def test_min_max_empty():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW wmin s 10 MIN 1
DEFINE_TUMBLING_WINDOW wmax s 10 MAX 2
ADVANCE_WATERMARK s 10 3
QUERY wmin k 0 4
QUERY wmax k 0 5
"""
    r = run(stdin)
    assert lines(r.stdout) == ["NULL", "NULL"]


def test_avg_negative_trunc():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 AVG 1
INGEST s k -3 1 2
INGEST s k 5 2 3
ADVANCE_WATERMARK s 10 4
QUERY w k 0 5
INGEST s k -4 11 6
ADVANCE_WATERMARK s 20 7
QUERY w k 10 8
"""
    r = run(stdin)
    # first window sum 2, count2 avg 1, second window -4 avg -4
    assert lines(r.stdout) == ["OK", "OK", "1", "OK", "-4"]


def test_multiple_keys_and_windows():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW wsum s 10 SUM 1
DEFINE_TUMBLING_WINDOW wcnt s 10 COUNT 2
INGEST s alice 10 1 3
INGEST s bob 20 2 4
INGEST s alice 5 9 5
ADVANCE_WATERMARK s 10 6
QUERY wsum alice 0 7
QUERY wsum bob 0 8
QUERY wcnt alice 0 9
QUERY wcnt bob 0 10
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "OK", "OK", "15", "20", "2", "1"]


def test_define_includes_past_events():
    # per spec, DEFINE should include past events retroactively
    stdin = """CREATE_STREAM s 0
INGEST s k 10 5 1
INGEST s k 20 15 2
DEFINE_TUMBLING_WINDOW w s 10 SUM 3
ADVANCE_WATERMARK s 10 4
QUERY w k 0 5
ADVANCE_WATERMARK s 20 6
QUERY w k 10 7
"""
    r = run(stdin)
    # retroactive: both events counted despite window defined after ingests
    assert lines(r.stdout) == ["OK", "OK", "10", "20"]


def test_complex_sliding_with_late():
    stdin = """CREATE_STREAM s 0
DEFINE_SLIDING_WINDOW w s 10 5 SUM 1
INGEST s k 10 3 2
INGEST s k 20 7 3
ADVANCE_WATERMARK s 10 4
INGEST s k 30 8 5
INGEST s k 5 11 6
ADVANCE_WATERMARK s 15 7
QUERY w k 0 8
QUERY w k 5 9
QUERY w k 10 10
"""
    r = run(stdin)
    # Events: 3 in [0,10) sum10, [0,10)+[5,15): Actually event at 3 only in [0,10)
    # Event 7 in both [0,10) and [5,15): sums
    # Watermark 10 closes [0,10): sum 10+20=30
    # Then ingest 8 with watermark 10: event_time 8 <=10 => LATE
    # Ingest 11 with time11 >10 OK, belongs to [5,15) and [10,20): sum for [5,15) now 20+5=25? Wait 20 from earlier plus 5 =25
    # Watermark 15 closes [5,15): sum should be 20+5=25, but also includes? Actually [5,15) includes 7,11 => 20+5=25
    # Query [0,10) -> 30, [5,15) ->25, [10,20) -> 5 but not closed yet watermark15 <20 => NULL
    assert lines(r.stdout) == ["OK", "OK", "LATE", "OK", "30", "25", "NULL"]


# --------------------------------------------------------------------------
# Invalid input exits non-zero
# --------------------------------------------------------------------------


def test_invalid_input_exits_nonzero():
    cases = [
        "UNKNOWN_CMD 0\n",
        "CREATE_STREAM\n",
        "CREATE_STREAM t notint 0\n",
        "CREATE_STREAM bad/name 0 0\n",
        "CREATE_STREAM . 0 0\n",
        "CREATE_STREAM .. 1 0\n",
        "DEFINE_TUMBLING_WINDOW w t 0 SUM 0\n",  # size 0 invalid
        "DEFINE_TUMBLING_WINDOW w t 10 BADAGG 0\n",
        "DEFINE_SLIDING_WINDOW w t 10 0 SUM 0\n",  # slide 0 invalid
        "INGEST s k notint 10 0\n",
        "INGEST s k 5 notint 0\n",
        "CREATE_STREAM t 1 -1\n",
        "INGEST s k 5 10 -1\n",
        "ADVANCE_WATERMARK s 10 -1\n",
        "ADVANCE_WATERMARK s -5 0\n",
        "QUERY w k 0 -1\n",
        "LIST_STREAMS -1\n",
        "COMPACT -1\n",
        "INGEST s bad/key 5 10 0\n",  # key invalid char /
    ]
    for stdin in cases:
        r = run(stdin)
        assert r.returncode != 0, (
            f"expected non-zero for: {stdin!r} got {r.returncode} out={r.stdout} err={r.stderr}"
        )


def test_blank_lines_ignored():
    stdin = """\n\nCREATE_STREAM t 0\n\n\nDEFINE_TUMBLING_WINDOW w t 10 SUM 1\n\nINGEST t k 5 2 2\n\n\nADVANCE_WATERMARK t 10 3\n\nQUERY w k 0 4\n\n"""
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["OK", "5"]


# --------------------------------------------------------------------------
# Durability
# --------------------------------------------------------------------------


def test_persist_across_restart(tmp_path):
    d = str(tmp_path)
    r1 = run(
        "CREATE_STREAM s 0\nDEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST s k 10 5 2\nADVANCE_WATERMARK s 10 3\n",
        state_dir=d,
    )
    assert r1.returncode == 0
    assert lines(r1.stdout) == ["OK"]

    r2 = run("QUERY w k 0 4\nLIST_STREAMS 5\nLIST_WINDOWS 6\n", state_dir=d)
    assert r2.returncode == 0
    assert lines(r2.stdout) == ["10", "s", "w"]


def test_persist_sliding(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_SLIDING_WINDOW w s 10 5 COUNT 1\nINGEST s k 1 2 2\nINGEST s k 1 7 3\nADVANCE_WATERMARK s 15 4\n",
        state_dir=d,
    )
    r = run("QUERY w k 0 5\nQUERY w k 5 6\n", state_dir=d)
    assert lines(r.stdout) == ["2", "1"]


def test_persist_late_not_logged(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST s k 10 5 1\nADVANCE_WATERMARK s 10 2\n",
        state_dir=d,
    )
    # try to ingest late event, should output LATE and not be logged, so after restart query still 10
    r_late = run("INGEST s k 20 5 3\n", state_dir=d)
    assert lines(r_late.stdout) == ["LATE"]
    r = run("QUERY w k 0 4\n", state_dir=d)
    assert lines(r.stdout) == ["10"]


def test_recover_ignores_torn_tail(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST s k 10 5 1\n",
        state_dir=d,
    )
    logp = os.path.join(d, "stream.log")
    with open(logp, "ab") as f:
        f.write(b"\x05\x00\x00\x00")  # truncated header only
    r = run(
        "LIST_STREAMS 2\nQUERY w k 0 3\nADVANCE_WATERMARK s 10 4\nQUERY w k 0 5\n",
        state_dir=d,
    )
    assert r.returncode == 0
    # after torn tail, stream still exists, query before watermark NULL, after watermark 10
    assert lines(r.stdout) == ["s", "NULL", "10"]


def test_recover_ignores_bad_crc_tail(tmp_path):
    d = str(tmp_path)
    run("CREATE_STREAM s 0\n", state_dir=d)
    logp = os.path.join(d, "stream.log")
    payload = b"CREATE_STREAM t2 0"
    with open(logp, "ab") as f:
        f.write(struct.pack("<II", len(payload), 0xDEADBEEF) + payload)
    r = run("LIST_STREAMS 2\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["s"]


def test_torn_tail_truncated_then_appendable(tmp_path):
    d = str(tmp_path)
    run("CREATE_STREAM s 0\n", state_dir=d)
    logp = os.path.join(d, "stream.log")
    with open(logp, "ab") as f:
        f.write(b"\xff\xff")
    run("DEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST s k 5 2 2\n", state_dir=d)
    r = run(
        "LIST_WINDOWS 3\nQUERY w k 0 4\nADVANCE_WATERMARK s 10 5\nQUERY w k 0 6\n",
        state_dir=d,
    )
    assert lines(r.stdout) == ["w", "NULL", "5"]


def test_compact_preserves_state(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST s k 10 5 1\nINGEST s k 20 15 2\nADVANCE_WATERMARK s 10 3\nADVANCE_WATERMARK s 20 4\n",
        state_dir=d,
    )
    logp = os.path.join(d, "stream.log")
    before = os.path.getsize(logp)
    run("COMPACT 5\n", state_dir=d)
    after = os.path.getsize(logp)
    assert after <= before
    r = run("QUERY w k 0 6\nQUERY w k 10 7\n", state_dir=d)
    assert lines(r.stdout) == ["10", "20"]


def test_compact_preserves_sliding(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_SLIDING_WINDOW w s 10 5 COUNT 1\nINGEST s k 1 2 2\nINGEST s k 1 7 3\nADVANCE_WATERMARK s 15 4\n",
        state_dir=d,
    )
    run("COMPACT 5\n", state_dir=d)
    r = run("QUERY w k 0 6\nQUERY w k 5 7\n", state_dir=d)
    assert lines(r.stdout) == ["2", "1"]


def test_compact_ignores_stray_tmp(tmp_path):
    d = str(tmp_path)
    run("CREATE_STREAM s 0\n", state_dir=d)
    with open(os.path.join(d, "stream.log.tmp"), "wb") as f:
        f.write(b"garbage")
    r = run("LIST_STREAMS 1\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["s"]


def test_empty_log_recovers_clean(tmp_path):
    d = str(tmp_path)
    Path(os.path.join(d, "stream.log")).touch()
    os.makedirs(d, exist_ok=True)
    r = run("LIST_STREAMS 0\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["NONE"]


def test_deterministic():
    stdin = "CREATE_STREAM s 0\nDEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST s k 5 2 2\nADVANCE_WATERMARK s 10 3\nQUERY w k 0 4\n"
    a = run(stdin)
    b = run(stdin)
    assert a.stdout == b.stdout and a.stdout != ""


def test_inmemory_does_not_persist():
    run("CREATE_STREAM s 0\n")
    r = run("LIST_STREAMS 0\n")
    assert lines(r.stdout) == ["NONE"]


# --------------------------------------------------------------------------
# Stdlib-only enforcement
# --------------------------------------------------------------------------


def test_stdlib_only():
    import re

    go_files = []
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))
    if not go_files:
        pytest.skip("no go files found")

    gomod = os.path.join(APP, "go.mod")
    if os.path.exists(gomod):
        txt = open(gomod).read()
        for line in txt.splitlines():
            line = line.strip()
            if "github.com" in line or "golang.org/x" in line or "gopkg.in" in line:
                assert False, f"go.mod contains external dependency: {line}"

    import_re = re.compile(r'"([^"]+)"')
    for gf in go_files:
        content = open(gf).read()
        lines_go = content.splitlines()
        in_import_block = False
        for line in lines_go:
            stripped = line.strip()
            if stripped.startswith("import ("):
                in_import_block = True
                continue
            if in_import_block and stripped == ")":
                in_import_block = False
                continue
            if in_import_block or stripped.startswith("import "):
                for q in import_re.findall(line):
                    if "." in q:
                        assert False, (
                            f"Third-party import found in {gf}: {q} (stdlib only)"
                        )


def test_fsync_best_effort():
    """Best-effort durability check — informational, not gating reward."""
    import sys

    go_files = []
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))
    assert len(go_files) > 0, "no Go files found for fsync check"
    assert os.path.exists(BIN), "binary not built for durability check"

    found = False
    for gf in go_files:
        content = open(gf).read()
        if (
            "Sync()" in content
            or "O_SYNC" in content
            or "O_DSYNC" in content
            or "fsync" in content.lower()
        ):
            found = True
            break
    if not found:
        print(
            "WARNING: no Sync()/O_SYNC found — per-append fsync is recommended but not required for correctness",
            file=sys.stderr,
        )
    # Check that Go source contains at least package main and main func
    has_main = any("func main(" in open(gf).read() for gf in go_files)
    assert has_main, "no func main found in Go files"


# --------------------------------------------------------------------------
# More complex cases
# --------------------------------------------------------------------------


def test_large_payload_and_names():
    long_stream = "s" + "a" * 200
    payload_key = "k" + "b" * 100
    stdin = f"""CREATE_STREAM {long_stream} 0
DEFINE_TUMBLING_WINDOW w {long_stream} 10 SUM 1
INGEST {long_stream} {payload_key} 123 5 2
ADVANCE_WATERMARK {long_stream} 10 3
QUERY w {payload_key} 0 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "123"]


def test_many_events_tumbling():
    cmds = ["CREATE_STREAM s 0", "DEFINE_TUMBLING_WINDOW w s 10 SUM 1"]
    total = 0
    for i in range(20):
        cmds.append(f"INGEST s k {i} {i} {i + 2}")
        total += i
    cmds.append("ADVANCE_WATERMARK s 20 22")
    cmds.append("QUERY w k 0 23")
    cmds.append("QUERY w k 10 24")
    r = run("\n".join(cmds))
    out = lines(r.stdout)
    # first 20 OK, then 10 sum for window 0 (0..9 => sum 0+..+9=45), window10 sum 10..19 =145
    assert out[20] == "45"
    assert out[21] == "145"


def test_sliding_many_windows_per_event():
    # size 10 slide 1 => each event belongs to up to 10 windows
    cmds = ["CREATE_STREAM s 0", "DEFINE_SLIDING_WINDOW w s 10 1 COUNT 1"]
    for i in range(5):
        cmds.append(f"INGEST s k 1 {i} {i + 2}")
    cmds.append("ADVANCE_WATERMARK s 10 7")
    # Query windows 0..4 (since watermark 10, windows ending <=10 closed: start 0 end10, start? start 0 only? Actually windows [0,10) end10 closed, [1,11) end11 not closed etc)
    cmds.append("QUERY w k 0 8")
    r = run("\n".join(cmds))
    out = lines(r.stdout)
    # first 5 OK, then query 0 should have events 0..4 all in window [0,10) => count 5
    assert out[5] == "5"


def test_compact_minimal_deterministic_and_smaller(tmp_path):
    d = str(tmp_path)
    # produce many duplicate creates and watermark advances
    cmds = [
        "CREATE_STREAM s 0",
        "CREATE_STREAM s 0",
        "DEFINE_TUMBLING_WINDOW w s 10 SUM 1",
        "DEFINE_TUMBLING_WINDOW w s 10 SUM 1",
    ]
    for i in range(10):
        cmds.append(f"INGEST s k {i} {i} {i + 10}")
    for wm in [5, 10, 10, 15, 20]:
        cmds.append(f"ADVANCE_WATERMARK s {wm} {100 + wm}")
    run("\n".join(cmds), state_dir=d)
    before = os.path.getsize(os.path.join(d, "stream.log"))
    run("COMPACT 200\n", state_dir=d)
    after = os.path.getsize(os.path.join(d, "stream.log"))
    assert after <= before
    # after compact, replay should give same query
    r = run("QUERY w k 0 201\nQUERY w k 10 202\n", state_dir=d)
    # window0 sum 0..9 =45, window10 sum for 10..19 not in this range? Actually events 0..9 only
    # second window 10..19 has no events, but we have events up to 9 only, so NULL? Wait events 0..9 inclusive, window10 starts at10 includes events 10? No events 0..9, second window 10 has none => NULL
    assert lines(r.stdout)[0] == "45"
    assert lines(r.stdout)[1] == "NULL"


def test_noop_does_not_append_records(tmp_path):
    d = str(tmp_path)
    run("CREATE_STREAM s 0\n", state_dir=d)
    size_after_create = os.path.getsize(os.path.join(d, "stream.log"))
    run("CREATE_STREAM s 1\n", state_dir=d)  # duplicate, should be no-op no log
    size_after_dup = os.path.getsize(os.path.join(d, "stream.log"))
    assert size_after_dup == size_after_create
    run("ADVANCE_WATERMARK s 0 2\n", state_dir=d)
    size_after_wm = os.path.getsize(os.path.join(d, "stream.log"))
    run("ADVANCE_WATERMARK s 0 3\n", state_dir=d)  # same watermark no-op
    size_after_same = os.path.getsize(os.path.join(d, "stream.log"))
    assert size_after_same == size_after_wm


def test_fuzz_random():
    # random mix of operations, ensure no crash and deterministic replay
    cmds = ["CREATE_STREAM s 0"]
    # create a few windows
    cmds.append("DEFINE_TUMBLING_WINDOW w1 s 10 SUM 1")
    cmds.append("DEFINE_TUMBLING_WINDOW w2 s 5 COUNT 2")
    cmds.append("DEFINE_SLIDING_WINDOW w3 s 10 5 AVG 3")
    # ingest random
    for i in range(30):
        key = f"k{i % 3}"
        val = random.randint(-100, 100)
        et = random.randint(0, 25)
        cmds.append(f"INGEST s {key} {val} {et} {10 + i}")
    cmds.append("ADVANCE_WATERMARK s 20 100")
    # query all combinations, ensure no crash
    for wid in ["w1", "w2", "w3"]:
        for k in ["k0", "k1", "k2"]:
            for ws in [0, 5, 10, 15, 20]:
                cmds.append(f"QUERY {wid} {k} {ws} {200 + ws}")
    r = run("\n".join(cmds))
    assert r.returncode == 0, r.stderr
    # should have only OK, NULL, numbers, ERROR, LATE lines, no crash
    out = lines(r.stdout)
    assert len(out) > 0


# --------------------------------------------------------------------------
# Hard mode: session windows, COUNT_DISTINCT, INGEST_BATCH
# --------------------------------------------------------------------------


def test_session_window_basic():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW sess s 10 SUM 1
INGEST s k 10 0 2
INGEST s k 20 5 3
INGEST s k 5 20 4
ADVANCE_WATERMARK s 15 5
QUERY sess k 0 6
ADVANCE_WATERMARK s 30 7
QUERY sess k 0 8
QUERY sess k 20 9
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    # first session [0,15) sum 30, second [20,30) sum 5
    assert lines(r.stdout) == ["OK", "OK", "OK", "30", "30", "5"]


def test_session_window_gap_merge():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW sess s 10 SUM 1
INGEST s k 10 0 2
INGEST s k 5 25 3
QUERY sess k 0 4
QUERY sess k 25 5
ADVANCE_WATERMARK s 15 6
QUERY sess k 0 7
ADVANCE_WATERMARK s 35 8
QUERY sess k 25 9
"""
    r = run(stdin)
    # gap 10, events 0 and 25 diff 25>10 => two sessions [0,10) sum10, [25,35) sum5
    # before watermark 15, first closed, second open
    assert lines(r.stdout) == ["OK", "OK", "NULL", "NULL", "10", "5"]


def test_session_window_out_of_order_merge():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW sess s 15 SUM 1
INGEST s k 10 0 2
INGEST s k 10 30 3
INGEST s k 10 12 4
ADVANCE_WATERMARK s 20 5
QUERY sess k 0 6
ADVANCE_WATERMARK s 45 7
QUERY sess k 0 8
"""
    r = run(stdin)
    # gap 15: events 0,30 diff 30>15 => two sessions [0,15) sum10, [30,45) sum10
    # add event 12 diff 12-0=12 <=15 merges into first session => first session now [0,27) (last 12+15=27) sum20
    # watermark 20 -> first still open? end 27 >20 => NULL
    # watermark 45 closes both: first sum20, second sum10
    assert lines(r.stdout) == ["OK", "OK", "OK", "NULL", "20"]


def test_session_window_retroactive():
    stdin = """CREATE_STREAM s 0
INGEST s k 10 0 1
INGEST s k 20 5 2
DEFINE_SESSION_WINDOW sess s 10 SUM 3
ADVANCE_WATERMARK s 15 4
QUERY sess k 0 5
"""
    r = run(stdin)
    # retroactive: events 0 and 5 diff 5 <=10 same session [0,15) sum30
    assert lines(r.stdout) == ["OK", "OK", "30"]


def test_session_window_late_handling():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW sess s 10 SUM 1
INGEST s k 10 5 2
ADVANCE_WATERMARK s 15 3
INGEST s k 20 5 4
INGEST s k 30 20 5
ADVANCE_WATERMARK s 35 6
QUERY sess k 5 7
QUERY sess k 20 8
"""
    r = run(stdin)
    # first event 5 creates session [5,15) sum10, watermark15 closes it (start 5)
    # second ingest 5 late -> LATE
    # third 20 creates [20,30) sum30, watermark 35 closes
    assert lines(r.stdout) == ["OK", "LATE", "OK", "10", "30"]


def test_count_distinct_tumbling():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 COUNT_DISTINCT 1
INGEST s k 5 1 2
INGEST s k 5 2 3
INGEST s k 7 3 4
INGEST s k 5 8 5
ADVANCE_WATERMARK s 10 6
QUERY w k 0 7
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "OK", "OK", "OK", "2"]


def test_count_distinct_sliding():
    stdin = """CREATE_STREAM s 0
DEFINE_SLIDING_WINDOW w s 10 5 COUNT_DISTINCT 1
INGEST s k 5 0 2
INGEST s k 5 4 3
INGEST s k 7 6 4
ADVANCE_WATERMARK s 15 5
QUERY w k 0 6
QUERY w k 5 7
"""
    r = run(stdin)
    # window [0,10): values 5,5,7 => distinct 2
    # [5,10?) actually [5,15): values 7? Wait events: 0-> [0,10), 4->[0,10), 6->[0,10) and [5,15) => [0,10) has 5,5,7 distinct2, [5,15) has 7 distinct1
    assert lines(r.stdout) == ["OK", "OK", "OK", "2", "1"]


def test_count_distinct_session():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW w s 10 COUNT_DISTINCT 1
INGEST s k 5 0 2
INGEST s k 5 5 3
INGEST s k 7 6 4
ADVANCE_WATERMARK s 20 5
QUERY w k 0 6
"""
    r = run(stdin)
    # single session [0,16) gap10, events 0,5,6 diff chain <=10 same session distinct 2
    assert lines(r.stdout) == ["OK", "OK", "OK", "2"]


def test_ingest_batch_basic():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST_BATCH s 2 k1 10 5 k2 20 6 2
ADVANCE_WATERMARK s 10 3
QUERY w k1 0 4
QUERY w k2 0 5
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "10", "20"]


def test_ingest_batch_late_atomic():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 SUM 1
INGEST s k 10 5 1
ADVANCE_WATERMARK s 10 2
INGEST_BATCH s 2 k 20 5 k 30 15 3
QUERY w k 0 4
INGEST_BATCH s 2 k 20 15 k 30 20 5
ADVANCE_WATERMARK s 20 6
QUERY w k 10 7
"""
    r = run(stdin)
    # first batch after watermark 10 includes event time5 late -> whole batch LATE none applied, window0 still 10
    # second batch: 15 in window10 sum20, 20 in window20 sum30, so query window10 after wm20 => 20
    assert lines(r.stdout) == ["OK", "LATE", "10", "OK", "20"]


def test_ingest_batch_error_missing_stream():
    stdin = """INGEST_BATCH missing 1 k 5 10 0
CREATE_STREAM s 1
INGEST_BATCH s 1 k 5 10 2
"""
    r = run(stdin)
    assert lines(r.stdout) == ["ERROR", "OK"]


def test_ingest_batch_distinct():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 10 COUNT_DISTINCT 1
INGEST_BATCH s 3 k 5 1 k 5 2 k 7 3 4
ADVANCE_WATERMARK s 10 5
QUERY w k 0 6
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "2"]


def test_ingest_batch_session_merge():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW sess s 10 SUM 1
INGEST_BATCH s 3 k 10 0 k 20 5 k 5 20 2
ADVANCE_WATERMARK s 15 3
QUERY sess k 0 4
ADVANCE_WATERMARK s 30 5
QUERY sess k 20 6
"""
    r = run(stdin)
    # batch 0,5 same session [0,15) sum30, 20 separate [20,30) sum5
    assert lines(r.stdout) == ["OK", "30", "5"]


def test_ingest_batch_persist(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_TUMBLING_WINDOW w s 10 SUM 1\nINGEST_BATCH s 2 k 10 1 k 20 2 3\nADVANCE_WATERMARK s 10 4\n",
        state_dir=d,
    )
    r = run("QUERY w k 0 5\n", state_dir=d)
    # batch events 1 and 2 both in window0 sum 30
    assert lines(r.stdout) == ["30"]


def test_session_compact_preserves(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_STREAM s 0\nDEFINE_SESSION_WINDOW w s 10 SUM 1\nINGEST s k 10 0 1\nINGEST s k 20 5 2\nADVANCE_WATERMARK s 15 3\n",
        state_dir=d,
    )
    before = os.path.getsize(os.path.join(d, "stream.log"))
    run("COMPACT 4\n", state_dir=d)
    after = os.path.getsize(os.path.join(d, "stream.log"))
    assert after <= before
    r = run("QUERY w k 0 5\n", state_dir=d)
    assert lines(r.stdout) == ["30"]


def test_mixed_window_types_same_stream():
    stdin = """CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW wt s 10 SUM 1
DEFINE_SLIDING_WINDOW ws s 10 5 SUM 2
DEFINE_SESSION_WINDOW wse s 5 SUM 3
INGEST s k 10 0 4
INGEST s k 20 3 5
INGEST s k 30 7 6
ADVANCE_WATERMARK s 15 7
QUERY wt k 0 8
QUERY ws k 0 9
QUERY ws k 5 10
QUERY wse k 0 11
ADVANCE_WATERMARK s 30 12
QUERY wse k 0 13
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    out = lines(r.stdout)
    # wt [0,10): events 0,3,7 sum 60, closed watermark15 => 60
    # ws [0,10): same 60, [5,15): events 7 only? Actually 7 belongs to [0,10) and [5,15): event 0 not in [5,15), 3 not, 7 yes => 30? Wait also? Let's compute:
    # Event 0: [0,10) only
    # Event 3: [0,10) only? Slide5: windows start0,5... Event3 in [0,10) only (since 3>=5? 3<5? Actually start5: 5<=3? No 5<=3 false, so only start0)
    # Event7: start0 (0<=7<10) yes, start5 (5<=7<15) yes => 30? Value 30 in second window
    # So ws 0 => 10+20+30=60, ws5 =>30
    # session gap5: events 0 and3 diff3<=5 same session [0,8) (3+5=8), event7 diff 4 from 3 <=5 same session extended to [0,12) sum60, closed watermark15 => session start0 sum60, but after watermark15 still open? Actually end12 <=15 so closed => query should return 60 at watermark15, but our test queries wse k0 at watermark15, should be 60. However we have only one session [0,12) sum60.
    # After more events? No more. So second query after 30 still same session.
    assert out[3] == "60"  # wt 0
    assert out[4] == "60"  # ws 0
    assert out[5] == "30"  # ws 5
    assert out[6] == "60"  # wse 0 at wm15
    assert out[7] == "60"  # wse 0 at wm30 still


def test_large_sliding_performance():
    # size 100 slide 1 => 100 windows per event, 100 events => 10k updates
    cmds = ["CREATE_STREAM s 0", "DEFINE_SLIDING_WINDOW w s 100 1 SUM 1"]
    for i in range(100):
        cmds.append(f"INGEST s k 1 {i} {i + 2}")
    cmds.append("ADVANCE_WATERMARK s 100 200")
    cmds.append("QUERY w k 0 201")
    r = run("\n".join(cmds), timeout=10)
    assert r.returncode == 0
    out = lines(r.stdout)
    # window [0,100): 100 events sum 100
    assert out[100] == "100"


def test_query_session_not_exist_returns_null():
    stdin = """CREATE_STREAM s 0
DEFINE_SESSION_WINDOW w s 10 SUM 1
INGEST s k 10 0 1
ADVANCE_WATERMARK s 15 2
QUERY w k 5 3
QUERY w k 0 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["OK", "NULL", "10"]


def test_batch_count_boundaries():
    # count 100 max allowed
    many = " ".join([f"k{i % 5} {i} {i}" for i in range(100)])
    stdin = f"""CREATE_STREAM s 0
DEFINE_TUMBLING_WINDOW w s 1000 SUM 1
INGEST_BATCH s 100 {many} 101
ADVANCE_WATERMARK s 1000 102
QUERY w k0 0 103
"""
    r = run(stdin, timeout=10)
    assert r.returncode == 0
    out = lines(r.stdout)
    assert out[0] == "OK"
    # k0 gets values 0,5,10,...95 => 20 values sum = 5*(0+19)*20/2? Actually 0+5+...+95 =5*(0+...+19)=5*190=950
    assert out[1] == "950"
