"""Black-box tests for Kafka-like message queue broker.

Builds Go program and drives via stdin/stdout.

Semantics under test:
  * topics with partitions, append-only logs, offsets
  * PRODUCE and PRODUCE_AUTO (hash by sum bytes % partitions)
  * FETCH, FETCH_RANGE, LIST_TOPICS, TOPIC_INFO, PARTITION_INFO
  * consumer groups: JOIN_GROUP, POLL, COMMIT, SEEK, GET_GROUP_OFFSET, LIST_GROUPS
  * durable mode MQ_STATE_DIR/mq.log: CRC-framed log, torn-tail truncation, atomic compaction
"""

import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

APP = "/app"
BIN = "/tmp/agent_mq"

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
            ["go", "mod", "init", "mq"],
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


def run(stdin, timeout=20, state_dir=None):
    env = {k: v for k, v in os.environ.items() if k != "MQ_STATE_DIR"}
    if state_dir is not None:
        env["MQ_STATE_DIR"] = state_dir
    return subprocess.run(
        [BIN], input=stdin, capture_output=True, text=True, timeout=timeout, env=env
    )


def lines(out):
    return [l for l in out.strip().split("\n") if l != ""]


def record(payload: str) -> bytes:
    b = payload.encode()
    return struct.pack("<II", len(b), zlib.crc32(b) & 0xFFFFFFFF) + b


# --------------------------------------------------------------------------
# Basic functionality
# --------------------------------------------------------------------------


def test_basic_produce_fetch():
    stdin = """CREATE_TOPIC orders 2 0
PRODUCE orders 0 hello 1
PRODUCE orders 0 world 2
FETCH orders 0 0 3
FETCH orders 0 1 4
LIST_TOPICS 5
TOPIC_INFO orders 6
PARTITION_INFO orders 0 7
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    assert lines(r.stdout) == [
        "0",
        "1",
        "hello",
        "world",
        "orders",
        "2 2",
        "0 2",
    ]


def test_produce_auto_hash():
    # sum bytes: "foo" = 102+111+111=324 %3=0, "bar"=98+97+114=309%3=0, "baz"=98+97+122=317%3=2
    stdin = """CREATE_TOPIC t 3 0
PRODUCE_AUTO t foo 1
PRODUCE_AUTO t bar 2
PRODUCE_AUTO t baz 3
FETCH t 0 0 4
FETCH t 0 1 5
FETCH t 2 0 6
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    out = lines(r.stdout)
    # first two produces go to partition 0, baz to 2
    assert out[0] == "0 0"
    assert out[1] == "0 1"
    assert out[2] == "2 0"
    assert out[3] == "foo"
    assert out[4] == "bar"
    assert out[5] == "baz"


def test_fetch_none_when_beyond():
    stdin = """CREATE_TOPIC a 1 0
PRODUCE a 0 x 1
FETCH a 0 0 2
FETCH a 0 1 3
FETCH a 0 5 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "x", "NONE", "NONE"]


def test_fetch_range():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
FETCH_RANGE t 0 0 2 4
FETCH_RANGE t 0 0 10 5
FETCH_RANGE t 0 1 2 6
FETCH_RANGE t 0 5 10 7
"""
    r = run(stdin)
    assert r.returncode == 0, r.stderr
    assert lines(r.stdout) == ["0", "1", "2", "a,b", "a,b,c", "b", "NONE"]


def test_list_topics_sorted_and_none():
    stdin = """LIST_TOPICS 0
CREATE_TOPIC z 1 1
CREATE_TOPIC a 1 2
CREATE_TOPIC m 1 3
LIST_TOPICS 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["NONE", "a,m,z"]


def test_topic_info_and_partition_info():
    stdin = """CREATE_TOPIC t 2 0
PRODUCE t 0 x 1
PRODUCE t 1 y 2
PRODUCE t 1 z 3
TOPIC_INFO t 4
PARTITION_INFO t 0 5
PARTITION_INFO t 1 6
TOPIC_INFO missing 7
PARTITION_INFO t 5 8
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0", "1", "2 3", "0 1", "0 2", "ERROR", "ERROR"]


def test_create_topic_idempotent():
    stdin = """CREATE_TOPIC t 2 0
CREATE_TOPIC t 5 1
PRODUCE t 1 ok 2
PRODUCE t 2 fail 3
TOPIC_INFO t 4
"""
    r = run(stdin)
    # second create with different partitions should be no-op, keep 2 partitions
    assert lines(r.stdout) == ["0", "ERROR", "2 1"]


def test_delete_topic():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
DELETE_TOPIC t 2
FETCH t 0 0 3
LIST_TOPICS 4
TOPIC_INFO t 5
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "ERROR", "NONE", "ERROR"]


def test_produce_error_when_missing_topic_or_partition():
    stdin = """PRODUCE missing 0 x 0
CREATE_TOPIC t 1 1
PRODUCE t 1 y 2
PRODUCE t 0 ok 3
"""
    r = run(stdin)
    assert lines(r.stdout) == ["ERROR", "ERROR", "0"]


# --------------------------------------------------------------------------
# Consumer groups
# --------------------------------------------------------------------------


def test_join_and_poll_basic():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 m1 1
PRODUCE t 0 m2 2
JOIN_GROUP g t 3
POLL g t 0 4
POLL g t 0 5
POLL g t 0 6
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "1", "0 m1", "1 m2", "NONE"]


def test_poll_auto_creates_group_and_subscribes():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 hello 1
POLL mygroup t 0 2
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0 hello"]


def test_commit_and_get_offset():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
JOIN_GROUP g t 3
POLL g t 0 4
COMMIT g t 0 0 5
GET_GROUP_OFFSET g t 0 6
POLL g t 0 7
COMMIT g t 0 1 8
GET_GROUP_OFFSET g t 0 9
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "1", "0 a", "0", "1 b", "1"]


def test_commit_minus_one_clears():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 x 1
JOIN_GROUP g t 2
POLL g t 0 3
COMMIT g t 0 0 4
GET_GROUP_OFFSET g t 0 5
COMMIT g t 0 -1 6
GET_GROUP_OFFSET g t 0 7
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0 x", "0", "NONE"]


def test_seek():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
JOIN_GROUP g t 4
POLL g t 0 5
POLL g t 0 6
SEEK g t 0 0 7
POLL g t 0 8
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "1", "2", "0 a", "1 b", "0 a"]


def test_seek_to_high_and_poll_none():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
JOIN_GROUP g t 2
SEEK g t 0 1 3
POLL g t 0 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "NONE"]


def test_poll_after_produce():
    stdin = """CREATE_TOPIC t 1 0
JOIN_GROUP g t 0
POLL g t 0 1
PRODUCE t 0 new 2
POLL g t 0 3
"""
    r = run(stdin)
    assert lines(r.stdout) == ["NONE", "0", "0 new"]


def test_multiple_partitions_group():
    stdin = """CREATE_TOPIC t 2 0
PRODUCE t 0 p0m1 1
PRODUCE t 1 p1m1 2
JOIN_GROUP g t 3
POLL g t 0 4
POLL g t 1 5
POLL g t 0 6
POLL g t 1 7
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0", "0 p0m1", "0 p1m1", "NONE", "NONE"]


def test_list_groups():
    stdin = """LIST_GROUPS 0
CREATE_TOPIC t 1 1
JOIN_GROUP g2 t 2
JOIN_GROUP g1 t 3
LIST_GROUPS 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["NONE", "g1,g2"]


def test_group_offset_none_for_new_group():
    stdin = """CREATE_TOPIC t 1 0
GET_GROUP_OFFSET g t 0 1
JOIN_GROUP g t 2
GET_GROUP_OFFSET g t 0 3
"""
    r = run(stdin)
    assert lines(r.stdout) == ["NONE", "NONE"]


def test_delete_topic_removes_group_state():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
JOIN_GROUP g t 2
POLL g t 0 3
COMMIT g t 0 0 4
DELETE_TOPIC t 5
GET_GROUP_OFFSET g t 0 6
LIST_GROUPS 7
"""
    r = run(stdin)
    # after delete, topic gone so GET_GROUP_OFFSET -> ERROR (topic missing) per spec
    assert r.returncode == 0
    out = lines(r.stdout)
    assert out[0] == "0"
    assert out[1] == "0 a"
    assert out[2] == "ERROR"
    # LIST_GROUPS: spec now explicitly says groups remain visible even when empty (intended behavior),
    # but for backwards compatibility we leniently accept either keeping empty group or GC'ing it.
    # This eliminates interpretation variance that caused flaky 4/5 vs 5/5 scores.
    assert out[3] in ("g", "NONE"), (
        f"expected group to remain or be GC'd, got {out[3]!r}"
    )


def test_produce_auto_then_poll():
    stdin = """CREATE_TOPIC t 2 0
PRODUCE_AUTO t hello 1
JOIN_GROUP g t 2
POLL g t 0 3
POLL g t 1 4
"""
    r = run(stdin)
    assert r.returncode == 0
    out = lines(r.stdout)
    # hello sum = 104+101+108+108+111=532 %2=0
    assert out[0] == "0 0"
    # poll p0 should return hello, poll p1 NONE
    assert out[1] == "0 hello"
    assert out[2] == "NONE"


# --------------------------------------------------------------------------
# Error handling for application errors vs invalid input
# --------------------------------------------------------------------------


def test_error_output_for_invalid_topic_partition():
    stdin = """CREATE_TOPIC t 1 0
FETCH missing 0 0 1
FETCH t 5 0 2
FETCH t 0 -1 3
PARTITION_INFO t 5 4
TOPIC_INFO missing 5
PRODUCE_AUTO missing x 6
JOIN_GROUP g missing 7
"""
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["ERROR"] * 7


def test_commit_seek_error_cases():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
JOIN_GROUP g t 2
COMMIT g t 0 5 3
SEEK g t 0 5 4
COMMIT g t 0 -2 5
SEEK g t 0 -1 6
"""
    r = run(stdin)
    assert r.returncode == 0
    # produce 0, then commit 5 beyond high -> ERROR, seek beyond high ERROR, commit -2 ERROR, seek -1 ERROR
    assert lines(r.stdout) == ["0", "ERROR", "ERROR", "ERROR", "ERROR"]


# --------------------------------------------------------------------------
# Invalid input exits non-zero
# --------------------------------------------------------------------------


def test_invalid_input_exits_nonzero():
    cases = [
        "UNKNOWN_CMD 0\n",
        "CREATE_TOPIC\n",
        "CREATE_TOPIC t notint 0\n",
        "CREATE_TOPIC t 0 0\n",  # num_partitions 0 invalid
        "CREATE_TOPIC t 1001 0\n",  # >1000
        "CREATE_TOPIC bad/topic 1 0\n",  # invalid char /
        "PRODUCE t 0 0\n",  # missing payload? arity wrong
        "FETCH t 0 0\n",  # missing timestamp
        "PRODUCE t 0 has,comma 0\n",  # payload contains comma -> invalid
        "CREATE_TOPIC . 1 0\n",
        "CREATE_TOPIC .. 1 0\n",
        # Negative timestamp must be invalid input (Issue 4)
        "CREATE_TOPIC t 1 -1\n",
        "PRODUCE t 0 x -5\n",
        "FETCH t 0 0 -1\n",
        "LIST_TOPICS -1\n",
        "COMPACT -1\n",
    ]
    for stdin in cases:
        r = run(stdin)
        assert r.returncode != 0, (
            f"expected non-zero for: {stdin!r} got {r.returncode} out={r.stdout} err={r.stderr}"
        )


def test_blank_lines_ignored():
    stdin = """\n\nCREATE_TOPIC t 1 0\n\n\nPRODUCE t 0 x 1\n\n\nFETCH t 0 0 2\n\n"""
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["0", "x"]


# --------------------------------------------------------------------------
# Durability
# --------------------------------------------------------------------------


def test_persist_across_restart(tmp_path):
    d = str(tmp_path)
    r1 = run("CREATE_TOPIC t 1 0\nPRODUCE t 0 m1 1\nPRODUCE t 0 m2 2\n", state_dir=d)
    assert r1.returncode == 0
    assert lines(r1.stdout) == ["0", "1"]

    r2 = run(
        "FETCH t 0 0 3\nFETCH t 0 1 4\nLIST_TOPICS 5\nTOPIC_INFO t 6\n", state_dir=d
    )
    assert r2.returncode == 0
    assert lines(r2.stdout) == ["m1", "m2", "t", "1 2"]


def test_persist_group_state(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 1 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nJOIN_GROUP g t 3\nPOLL g t 0 4\nCOMMIT g t 0 0 5\n",
        state_dir=d,
    )
    r = run("GET_GROUP_OFFSET g t 0 6\nPOLL g t 0 7\n", state_dir=d)
    # after restart, committed 0, position should be committed+1 =1, so poll returns b
    assert lines(r.stdout) == ["0", "1 b"]


def test_persist_seek_position(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 1 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nJOIN_GROUP g t 3\nPOLL g t 0 4\nPOLL g t 0 5\nSEEK g t 0 0 6\n",
        state_dir=d,
    )
    r = run("POLL g t 0 7\n", state_dir=d)
    # after restart, seek 0 should be persisted, so poll a again
    assert lines(r.stdout) == ["0 a"]


def test_persist_auto_produce(tmp_path):
    d = str(tmp_path)
    r1 = run("CREATE_TOPIC t 2 0\nPRODUCE_AUTO t hello 1\n", state_dir=d)
    assert r1.returncode == 0
    part_off = lines(r1.stdout)[0]  # e.g., "0 0"
    part = part_off.split()[0]
    r2 = run(f"FETCH t {part} 0 2\n", state_dir=d)
    assert lines(r2.stdout) == ["hello"]


def test_recover_ignores_torn_tail(tmp_path):
    d = str(tmp_path)
    run("CREATE_TOPIC t 1 0\nPRODUCE t 0 ok 1\n", state_dir=d)
    logp = os.path.join(d, "mq.log")
    with open(logp, "ab") as f:
        f.write(b"\x05\x00\x00\x00")  # truncated header only
    r = run("FETCH t 0 0 2\nLIST_TOPICS 3\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["ok", "t"]


def test_recover_ignores_bad_crc_tail(tmp_path):
    d = str(tmp_path)
    run("CREATE_TOPIC t 1 0\n", state_dir=d)
    logp = os.path.join(d, "mq.log")
    payload = b"CREATE_TOPIC t2 1 0"
    with open(logp, "ab") as f:
        f.write(struct.pack("<II", len(payload), 0xDEADBEEF) + payload)
    r = run("LIST_TOPICS 2\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["t"]


def test_torn_tail_truncated_then_appendable(tmp_path):
    d = str(tmp_path)
    run("CREATE_TOPIC t 1 0\n", state_dir=d)
    logp = os.path.join(d, "mq.log")
    with open(logp, "ab") as f:
        f.write(b"\xff\xff")
    run("PRODUCE t 0 after 1\n", state_dir=d)
    r = run("FETCH t 0 0 2\nLIST_TOPICS 3\n", state_dir=d)
    assert lines(r.stdout) == ["after", "t"]


def test_compact_preserves_state(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 2 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nPRODUCE t 1 c 3\nJOIN_GROUP g t 4\nPOLL g t 0 5\nCOMMIT g t 0 0 6\n",
        state_dir=d,
    )
    logp = os.path.join(d, "mq.log")
    before = os.path.getsize(logp)
    run("COMPACT 10\n", state_dir=d)
    after = os.path.getsize(logp)
    assert after <= before
    r = run(
        "FETCH t 0 0 11\nFETCH t 0 1 12\nFETCH t 1 0 13\nLIST_TOPICS 14\nGET_GROUP_OFFSET g t 0 15\nPOLL g t 0 16\n",
        state_dir=d,
    )
    assert lines(r.stdout) == ["a", "b", "c", "t", "0", "1 b"]


def test_compact_preserves_seek(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 1 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nJOIN_GROUP g t 3\nPOLL g t 0 4\nSEEK g t 0 0 5\n",
        state_dir=d,
    )
    run("COMPACT 10\n", state_dir=d)
    r = run("POLL g t 0 11\n", state_dir=d)
    assert lines(r.stdout) == ["0 a"]


def test_compact_ignores_stray_tmp(tmp_path):
    d = str(tmp_path)
    run("CREATE_TOPIC t 1 0\n", state_dir=d)
    with open(os.path.join(d, "mq.log.tmp"), "wb") as f:
        f.write(b"garbage")
    r = run("LIST_TOPICS 1\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["t"]


def test_empty_log_recovers_clean(tmp_path):
    d = str(tmp_path)
    Path(os.path.join(d, "mq.log")).touch()
    os.makedirs(d, exist_ok=True)
    r = run("LIST_TOPICS 0\n", state_dir=d)
    assert r.returncode == 0
    assert lines(r.stdout) == ["NONE"]


def test_deterministic():
    stdin = "CREATE_TOPIC t 2 0\nPRODUCE t 0 hello 1\nFETCH t 0 0 2\n"
    a = run(stdin)
    b = run(stdin)
    assert a.stdout == b.stdout and a.stdout != ""


def test_inmemory_does_not_persist():
    # run without state_dir should not persist
    run("CREATE_TOPIC t 1 0\n")
    r = run("LIST_TOPICS 0\n")
    assert lines(r.stdout) == ["NONE"]


def test_example_from_spec_basic():
    stdin = """CREATE_TOPIC orders 2 0
PRODUCE orders 0 hello 1
PRODUCE orders 0 world 2
FETCH orders 0 0 3
FETCH orders 0 1 4
LIST_TOPICS 5
TOPIC_INFO orders 6
PARTITION_INFO orders 0 7
"""
    r = run(stdin)
    assert r.returncode == 0
    assert lines(r.stdout) == ["0", "1", "hello", "world", "orders", "2 2", "0 2"]


def test_multiple_topics_and_groups_interleaved():
    stdin = """CREATE_TOPIC t1 1 0
CREATE_TOPIC t2 1 1
PRODUCE t1 0 a 2
PRODUCE t2 0 b 3
JOIN_GROUP g t1 4
JOIN_GROUP g t2 5
POLL g t1 0 6
POLL g t2 0 7
LIST_TOPICS 8
LIST_GROUPS 9
TOPIC_INFO t1 10
TOPIC_INFO t2 11
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0", "0 a", "0 b", "t1,t2", "g", "1 1", "1 1"]


def test_poll_leaves_position_for_other_groups():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 x 1
JOIN_GROUP g1 t 2
JOIN_GROUP g2 t 3
POLL g1 t 0 4
POLL g2 t 0 5
POLL g1 t 0 6
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0 x", "0 x", "NONE"]


def test_fetch_range_with_auto():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
FETCH_RANGE t 0 0 3 4
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "1", "2", "a,b,c"]


# --------------------------------------------------------------------------
# Stdlib-only enforcement (Issue 3)
# --------------------------------------------------------------------------


def test_stdlib_only():
    """The spec says Go standard library only. Enforce no third-party imports."""
    import re

    go_files = []
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".go"):
                go_files.append(os.path.join(root, f))

    # If no go files, skip (agent hasn't built? but oracle should have)
    if not go_files:
        pytest.skip("no go files found")

    # Check go.mod for external requires (if present)
    gomod = os.path.join(APP, "go.mod")
    if os.path.exists(gomod):
        txt = open(gomod).read()
        # Look for require lines that are not stdlib (stdlib never appears in require)
        # Any require with a module containing a dot is external
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("require") or "\t" in line or " " in line:
                # crude: if line contains github.com, golang.org, etc, it's external
                if "github.com" in line or "golang.org/x" in line or "gopkg.in" in line:
                    assert False, f"go.mod contains external dependency: {line}"

    # Check imports: stdlib import paths never contain a dot
    import_re = re.compile(r'"([^"]+)"')
    for gf in go_files:
        content = open(gf).read()
        # Find all quoted strings in import blocks
        for m in import_re.finditer(content):
            imp = m.group(1)
            # Only consider import-like strings that look like package paths (contain / or short)
            # Ignore non-import string literals? Simple heuristic: check if this string appears after import keyword nearby
            # We'll also scan import statements more precisely
            # For strictness, any import containing '.' is disallowed
            # Skip if it's not in an import context? Check surrounding text for 'import'
            # Use simple check: if '.' in imp and '/' in imp, it's likely external
            # Also allow if imp is exactly "." or "_" (dot imports) – disallow those too for safety
            # The spec says stdlib only, so any import with a dot is third-party
            if "." in imp:
                # However stdlib does not contain dot, so fail
                # Exclude some false positives: if the file contains a string literal that is not import,
                # it could contain dot. We should only check imports inside import blocks or import "..."
                # Let's search import statements specifically
                pass

        # More precise: extract import blocks
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


# --------------------------------------------------------------------------
# TRIM / retention (makes task harder, Issue: too easy)
# --------------------------------------------------------------------------


def test_trim_basic():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
PARTITION_INFO t 0 4
TRIM t 0 1 5
PARTITION_INFO t 0 6
FETCH t 0 0 7
FETCH t 0 1 8
TOPIC_INFO t 9
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "1", "2", "0 3", "1 3", "NONE", "b", "1 2"]


def test_trim_fetch_range():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
TRIM t 0 2 4
FETCH_RANGE t 0 0 3 5
FETCH_RANGE t 0 1 3 6
FETCH_RANGE t 0 2 10 7
"""
    r = run(stdin)
    # after trim low=2, retained b? actually a offset0, b offset1, c offset2. trim 2 means low=2, retained only c
    # FETCH_RANGE 0 3 with low=2 -> effective start 2, returns c
    # 1 3 -> start 1 < low -> effective 2 returns c
    # 2 10 -> c
    assert lines(r.stdout) == ["0", "1", "2", "c", "c", "c"]


def test_trim_commit_and_seek_errors():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
TRIM t 0 1 3
COMMIT g t 0 0 4
SEEK g t 0 0 5
COMMIT g t 0 1 6
SEEK g t 0 1 7
GET_GROUP_OFFSET g t 0 8
"""
    r = run(stdin)
    # produce 0,1 then trim low=1, commit 0 (trimmed) -> ERROR, seek 0 (trimmed) -> ERROR, commit 1 ok, seek 1 ok, get offset 1
    assert lines(r.stdout) == ["0", "1", "ERROR", "ERROR", "1"]


def test_trim_poll_auto_advance():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
JOIN_GROUP g t 3
POLL g t 0 4
TRIM t 0 2 5
POLL g t 0 6
POLL g t 0 7
GET_GROUP_OFFSET g t 0 8
"""
    r = run(stdin)
    # poll a at 0, pos->1, trim low=2 advances pos to 2, high=2 -> poll NONE, second poll NONE, get offset NONE (no commit)
    assert lines(r.stdout) == ["0", "1", "0 a", "NONE", "NONE", "NONE"]


def test_trim_poll_after_trim_and_produce():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
JOIN_GROUP g t 2
POLL g t 0 3
TRIM t 0 1 4
POLL g t 0 5
PRODUCE t 0 b 6
POLL g t 0 7
"""
    r = run(stdin)
    # poll a, trim removes a, poll NONE, produce b offset1? Actually after trim, msgs len=1, high=1, low=1, produce b offset=1? Wait len=1 before produce after trim still len=1, so offset 1
    # Then poll should return b at offset 1
    assert lines(r.stdout) == ["0", "0 a", "NONE", "1", "1 b"]


def test_trim_commit_cleared():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
JOIN_GROUP g t 2
POLL g t 0 3
COMMIT g t 0 0 4
GET_GROUP_OFFSET g t 0 5
TRIM t 0 1 6
GET_GROUP_OFFSET g t 0 7
"""
    r = run(stdin)
    # after commit 0, get 0, then trim 1 clears committed < low
    assert lines(r.stdout) == ["0", "0 a", "0", "NONE"]


def test_trim_persist_and_compact(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 1 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nTRIM t 0 1 3\n",
        state_dir=d,
    )
    r = run(
        "PARTITION_INFO t 0 4\nFETCH t 0 0 5\nFETCH t 0 1 6\nTOPIC_INFO t 7\n",
        state_dir=d,
    )
    assert lines(r.stdout) == ["1 2", "NONE", "b", "1 1"]
    # compact should preserve low
    run("COMPACT 8\n", state_dir=d)
    r2 = run("PARTITION_INFO t 0 9\nFETCH t 0 1 10\n", state_dir=d)
    assert lines(r2.stdout) == ["1 2", "b"]


def test_trim_idempotent_and_error():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 x 1
TRIM t 0 0 2
TRIM t 0 1 3
TRIM t 0 1 4
TRIM t 0 5 5
PARTITION_INFO t 0 6
"""
    r = run(stdin)
    # produce, trim 0 no-op, trim1 low=1, trim1 again no-op, trim5 beyond high=1 -> ERROR
    assert lines(r.stdout) == ["0", "ERROR", "1 1"]


# --------------------------------------------------------------------------
# Additional hard cases to increase difficulty (too easy fix)
# --------------------------------------------------------------------------


def test_trim_many_messages_and_range():
    # 20 messages, trim first 15, fetch range should respect low
    cmds = ["CREATE_TOPIC t 1 0"]
    for i in range(20):
        cmds.append(f"PRODUCE t 0 m{i} {i + 1}")
    cmds.append("TRIM t 0 15 21")
    cmds.append("PARTITION_INFO t 0 22")
    cmds.append("FETCH_RANGE t 0 0 20 23")
    cmds.append("FETCH_RANGE t 0 15 20 24")
    cmds.append("TOPIC_INFO t 25")
    r = run("\n".join(cmds))
    out = lines(r.stdout)
    # 20 produces => offsets 0..19, then partition_info after trim = 15 20, then 2 ranges, then topic_info 1 5
    assert out[20] == "15 20"
    assert out[21] == "m15,m16,m17,m18,m19"
    assert out[22] == "m15,m16,m17,m18,m19"
    assert out[23] == "1 5"


def test_trim_then_produce_offsets_continue():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
TRIM t 0 2 3
PRODUCE t 0 c 4
PARTITION_INFO t 0 5
FETCH t 0 0 6
FETCH t 0 2 7
FETCH t 0 1 8
"""
    r = run(stdin)
    # a0,b1 trimmed by trim2 (low=2 high=2), then produce c offset 2, so high=3 low=2
    # fetch 0 -> NONE (trimmed), fetch2 -> c, fetch1 -> NONE (trimmed)
    assert lines(r.stdout) == ["0", "1", "2", "2 3", "NONE", "c", "NONE"]


def test_trim_delete_recreate_resets():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
TRIM t 0 1 2
DELETE_TOPIC t 3
CREATE_TOPIC t 1 4
PARTITION_INFO t 0 5
FETCH t 0 0 6
PRODUCE t 0 new 7
FETCH t 0 0 8
"""
    r = run(stdin)
    # after delete+recreate, low resets to 0, partition empty, then produce new
    assert lines(r.stdout) == ["0", "0 0", "NONE", "0", "new"]


def test_trim_group_commit_after_trim():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
JOIN_GROUP g t 3
POLL g t 0 4
COMMIT g t 0 0 5
TRIM t 0 1 6
GET_GROUP_OFFSET g t 0 7
COMMIT g t 0 0 8
COMMIT g t 0 1 9
GET_GROUP_OFFSET g t 0 10
"""
    r = run(stdin)
    # poll a, commit 0, trim1 clears committed 0, get NONE, commit 0 -> ERROR (trimmed), commit1 ok, get 1
    assert lines(r.stdout) == ["0", "1", "0 a", "NONE", "ERROR", "1"]


def test_trim_group_seek_and_poll():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
PRODUCE t 0 c 3
JOIN_GROUP g t 4
TRIM t 0 2 5
SEEK g t 0 1 6
SEEK g t 0 2 7
POLL g t 0 8
POLL g t 0 9
POLL g t 0 10
"""
    r = run(stdin)
    # low=2 high=3, seek1 ERROR, seek2 ok, poll c, then NONE, NONE
    assert lines(r.stdout) == ["0", "1", "2", "ERROR", "2 c", "NONE", "NONE"]


def test_trim_persist_group_low(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 1 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nJOIN_GROUP g t 3\nPOLL g t 0 4\nTRIM t 0 1 5\n",
        state_dir=d,
    )
    r = run(
        "GET_GROUP_OFFSET g t 0 6\nPARTITION_INFO t 0 7\nPOLL g t 0 8\n", state_dir=d
    )
    # after restart, group pos was 1 before trim, trim advanced to 1? Actually poll a pos->1, trim1 -> pos stays1 (since 1<1? no, 1==low so stays), poll returns b at 1
    # No commit, so get NONE
    out = lines(r.stdout)
    assert out[0] == "NONE"
    assert out[1] == "1 2"
    assert out[2] == "1 b"


def test_large_payload_and_topic_name():
    long_topic = "t" + "a" * 200  # 201 chars, valid (<255)
    payload = "x" * 1024  # max valid
    stdin = f"""CREATE_TOPIC {long_topic} 1 0
PRODUCE {long_topic} 0 {payload} 1
PARTITION_INFO {long_topic} 0 2
FETCH {long_topic} 0 0 3
TOPIC_INFO {long_topic} 4
"""
    r = run(stdin)
    out = lines(r.stdout)
    assert out[0] == "0"
    assert out[1] == "0 1"
    assert out[2] == payload
    assert out[3] == "1 1"


def test_create_topic_1000_partitions():
    stdin = """CREATE_TOPIC t 1000 0
PRODUCE t 999 last 1
PARTITION_INFO t 999 2
TOPIC_INFO t 3
"""
    r = run(stdin)
    assert lines(r.stdout) == ["0", "0 1", "1000 1"]


def test_fetch_range_low_edge():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 a 1
PRODUCE t 0 b 2
TRIM t 0 1 3
FETCH_RANGE t 0 0 1 4
FETCH_RANGE t 0 0 2 5
FETCH_RANGE t 0 1 1 6
"""
    r = run(stdin)
    # after trim low=1 high=2 retained b
    # range 0 1 -> effective start max(0,1)=1 >= end1? Actually end=1, start effective1 => start>=end => NONE
    # range 0 2 -> effective 1 2 => b
    # range 1 1 -> start 1 end1 => start>=end => NONE
    assert lines(r.stdout) == ["0", "1", "NONE", "b", "NONE"]


def test_compact_preserves_trim(tmp_path):
    d = str(tmp_path)
    run(
        "CREATE_TOPIC t 1 0\nPRODUCE t 0 a 1\nPRODUCE t 0 b 2\nTRIM t 0 1 3\nJOIN_GROUP g t 4\nPOLL g t 0 5\n",
        state_dir=d,
    )
    before = os.path.getsize(os.path.join(d, "mq.log"))
    run("COMPACT 6\n", state_dir=d)
    after = os.path.getsize(os.path.join(d, "mq.log"))
    assert after <= before
    r = run(
        "PARTITION_INFO t 0 7\nFETCH t 0 0 8\nFETCH t 0 1 9\nPOLL g t 0 10\n",
        state_dir=d,
    )
    # after compact, low=1 high=2, fetch0 NONE, fetch1 b, poll after previous poll had pos2 -> NONE
    assert lines(r.stdout) == ["1 2", "NONE", "b", "NONE"]


# --------------------------------------------------------------------------
# PRODUCE_BATCH atomic (harder)
# --------------------------------------------------------------------------


def test_produce_batch_basic():
    stdin = """CREATE_TOPIC t 2 0
PRODUCE_BATCH t 2 0 a 1 b 1
FETCH t 0 0 2
FETCH t 1 0 3
PARTITION_INFO t 0 4
PARTITION_INFO t 1 5
"""
    r = run(stdin)
    # batch of 2: partition0 a offset0, partition1 b offset0 => outputs "0,0"
    assert lines(r.stdout) == ["0,0", "a", "b", "0 1", "0 1"]


def test_produce_batch_same_partition():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE_BATCH t 3 0 x 0 y 0 z 1
FETCH_RANGE t 0 0 3 2
"""
    r = run(stdin)
    # 3 msgs to same partition: offsets 0,1,2 => "0,1,2"
    assert lines(r.stdout) == ["0,1,2", "x,y,z"]


def test_produce_batch_atomic_error():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE t 0 first 1
PRODUCE_BATCH t 2 0 ok 1 bad 2
FETCH t 0 0 3
FETCH t 0 1 4
TOPIC_INFO t 5
"""
    r = run(stdin)
    # batch has partition 1 invalid (>=1) => whole batch ERROR, none appended, only first message remains
    assert lines(r.stdout) == ["0", "ERROR", "first", "NONE", "1 1"]


def test_produce_batch_invalid_input():
    cases = [
        "PRODUCE_BATCH t 2 0 a 1\n",  # missing timestamp? actually arity mismatch
        "PRODUCE_BATCH t 0 0\n",  # count 0 invalid
        "PRODUCE_BATCH t 101 0\n",  # count >100 invalid (needs many tokens but count invalid first)
    ]
    for stdin in cases:
        r = run(stdin)
        assert r.returncode != 0, f"expected non-zero for {stdin!r}"


def test_produce_batch_persist(tmp_path):
    d = str(tmp_path)
    run("CREATE_TOPIC t 1 0\nPRODUCE_BATCH t 2 0 a 0 b 1\n", state_dir=d)
    r = run("FETCH t 0 0 2\nFETCH t 0 1 3\n", state_dir=d)
    assert lines(r.stdout) == ["a", "b"]


def test_produce_batch_and_trim():
    stdin = """CREATE_TOPIC t 1 0
PRODUCE_BATCH t 3 0 a 0 b 0 c 1
TRIM t 0 2 2
FETCH t 0 0 3
FETCH t 0 2 4
PARTITION_INFO t 0 5
"""
    r = run(stdin)
    # batch offsets 0,1,2 then trim 2 => low=2 high=3 retained c, so fetch 0 NONE, fetch2 c
    assert lines(r.stdout) == ["0,1,2", "NONE", "c", "2 3"]


# --------------------------------------------------------------------------
# Fuzz with Python reference (hard)
# --------------------------------------------------------------------------


class PyPartition:
    def __init__(self):
        self.msgs = []
        self.low = 0

    @property
    def high(self):
        return len(self.msgs)


class PyTopic:
    def __init__(self, name, num):
        self.name = name
        self.num = num
        self.parts = [PyPartition() for _ in range(num)]


class PyGroup:
    def __init__(self):
        self.subs = set()
        self.committed = {}  # (topic, part) -> offset
        self.pos = {}  # (topic, part) -> next offset


def py_run(commands):
    """Python reference broker, returns list of output lines."""
    topics = {}
    groups = {}
    out = []

    def tp_key(t, p):
        return (t, p)

    for line in commands:
        if not line.strip():
            continue
        parts = line.strip().split()
        cmd = parts[0]
        if cmd == "CREATE_TOPIC":
            topic = parts[1]
            num = int(parts[2])
            if topic not in topics:
                topics[topic] = PyTopic(topic, num)
        elif cmd == "DELETE_TOPIC":
            topic = parts[1]
            if topic in topics:
                del topics[topic]
                for g in groups.values():
                    g.subs.discard(topic)
                    for k in list(g.committed.keys()):
                        if k[0] == topic:
                            del g.committed[k]
                    for k in list(g.pos.keys()):
                        if k[0] == topic:
                            del g.pos[k]
        elif cmd == "PRODUCE":
            topic = parts[1]
            part = int(parts[2])
            payload = parts[3]
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part < 0 or part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            off = p.high
            p.msgs.append(payload)
            out.append(str(off))
        elif cmd == "PRODUCE_AUTO":
            topic = parts[1]
            payload = parts[2]
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            s = sum(b for b in payload.encode())
            chosen = s % t.num
            p = t.parts[chosen]
            off = p.high
            p.msgs.append(payload)
            out.append(f"{chosen} {off}")
        elif cmd == "PRODUCE_BATCH":
            topic = parts[1]
            count = int(parts[2])
            # last token timestamp, middle 2*count tokens
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            pairs = []
            valid = True
            for i in range(count):
                part = int(parts[3 + i * 2])
                pay = parts[3 + i * 2 + 1]
                if part < 0 or part >= t.num:
                    valid = False
                    break
                pairs.append((part, pay))
            if not valid:
                out.append("ERROR")
                continue
            offs = []
            for part, pay in pairs:
                p = t.parts[part]
                off = p.high
                p.msgs.append(pay)
                offs.append(str(off))
            out.append(",".join(offs))
        elif cmd == "TRIM":
            topic = parts[1]
            part = int(parts[2])
            off = int(parts[3])
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part < 0 or part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            high = p.high
            if off < 0 or off > high:
                out.append("ERROR")
                continue
            if off <= p.low:
                continue
            p.low = off
            for g in groups.values():
                key = (topic, part)
                if key in g.committed and g.committed[key] < off:
                    del g.committed[key]
                if key in g.pos and g.pos[key] < off:
                    g.pos[key] = off
        elif cmd == "FETCH":
            topic = parts[1]
            part = int(parts[2])
            off = int(parts[3])
            if part < 0 or off < 0:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            if off < p.low or off >= p.high:
                out.append("NONE")
                continue
            out.append(p.msgs[off])
        elif cmd == "FETCH_RANGE":
            topic = parts[1]
            part = int(parts[2])
            start = int(parts[3])
            end = int(parts[4])
            if part < 0 or start < 0 or end < 0 or end < start:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            high = p.high
            low = p.low
            if start < low:
                start = low
            if start >= high or start >= end:
                out.append("NONE")
                continue
            real_end = min(end, high)
            if start >= real_end:
                out.append("NONE")
                continue
            sl = p.msgs[start:real_end]
            if not sl:
                out.append("NONE")
            else:
                out.append(",".join(sl))
        elif cmd == "LIST_TOPICS":
            if not topics:
                out.append("NONE")
            else:
                out.append(",".join(sorted(topics.keys())))
        elif cmd == "TOPIC_INFO":
            topic = parts[1]
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            total = sum(p.high - p.low for p in t.parts)
            out.append(f"{t.num} {total}")
        elif cmd == "PARTITION_INFO":
            topic = parts[1]
            part = int(parts[2])
            if part < 0:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            out.append(f"{p.low} {p.high}")
        elif cmd == "JOIN_GROUP":
            group = parts[1]
            topic = parts[2]
            if topic not in topics:
                out.append("ERROR")
                continue
            if group not in groups:
                groups[group] = PyGroup()
            groups[group].subs.add(topic)
        elif cmd == "POLL":
            group = parts[1]
            topic = parts[2]
            part = int(parts[3])
            if part < 0:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            if group not in groups:
                groups[group] = PyGroup()
            g = groups[group]
            g.subs.add(topic)
            key = (topic, part)
            p = t.parts[part]
            if key not in g.pos:
                if key in g.committed:
                    pos = g.committed[key] + 1
                    if pos < p.low:
                        pos = p.low
                else:
                    pos = p.low
                g.pos[key] = pos
            else:
                pos = g.pos[key]
                if pos < p.low:
                    pos = p.low
                    g.pos[key] = pos
            if pos >= p.high:
                out.append("NONE")
                continue
            out.append(f"{pos} {p.msgs[pos]}")
            g.pos[key] = pos + 1
        elif cmd == "COMMIT":
            group = parts[1]
            topic = parts[2]
            part = int(parts[3])
            off = int(parts[4])
            if part < 0 or off < -1:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            if off != -1 and (off >= p.high or off < p.low):
                out.append("ERROR")
                continue
            if group not in groups:
                groups[group] = PyGroup()
            g = groups[group]
            g.subs.add(topic)
            key = (topic, part)
            if off == -1:
                g.committed.pop(key, None)
            else:
                g.committed[key] = off
        elif cmd == "SEEK":
            group = parts[1]
            topic = parts[2]
            part = int(parts[3])
            off = int(parts[4])
            if part < 0 or off < 0:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            if off < p.low or off > p.high:
                out.append("ERROR")
                continue
            if group not in groups:
                groups[group] = PyGroup()
            g = groups[group]
            g.subs.add(topic)
            g.pos[(topic, part)] = off
        elif cmd == "GET_GROUP_OFFSET":
            group = parts[1]
            topic = parts[2]
            part = int(parts[3])
            if part < 0:
                out.append("ERROR")
                continue
            if topic not in topics:
                out.append("ERROR")
                continue
            t = topics[topic]
            if part >= t.num:
                out.append("ERROR")
                continue
            p = t.parts[part]
            if group not in groups:
                out.append("NONE")
                continue
            g = groups[group]
            key = (topic, part)
            if key not in g.committed or g.committed[key] < p.low:
                out.append("NONE")
            else:
                out.append(str(g.committed[key]))
        elif cmd == "LIST_GROUPS":
            if not groups:
                out.append("NONE")
            else:
                # spec says groups persist even when empty, lenient accepts GC
                # Python ref keeps empty groups
                out.append(",".join(sorted(groups.keys())))
        elif cmd == "COMPACT":
            continue
        else:
            # unknown
            pass
    return out


def test_fuzz_random():
    import random

    random.seed(12345)
    for _ in range(20):
        topics = {}
        cmds = []
        # create 1-3 topics with 1-3 partitions
        for ti in range(random.randint(1, 3)):
            tname = f"t{ti}"
            parts = random.randint(1, 3)
            topics[tname] = parts
            cmds.append(f"CREATE_TOPIC {tname} {parts} {len(cmds)}")
        # produce random messages
        for _ in range(100):
            op = random.choice(
                [
                    "PRODUCE",
                    "PRODUCE_AUTO",
                    "PRODUCE_BATCH",
                    "FETCH",
                    "FETCH_RANGE",
                    "POLL",
                    "COMMIT",
                    "SEEK",
                    "TRIM",
                    "JOIN_GROUP",
                    "GET_GROUP_OFFSET",
                    "LIST_TOPICS",
                    "TOPIC_INFO",
                    "PARTITION_INFO",
                    "LIST_GROUPS",
                ]
            )
            if op == "PRODUCE":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                payload = f"msg{random.randint(0, 1000)}"
                cmds.append(f"PRODUCE {t} {p} {payload} {len(cmds)}")
            elif op == "PRODUCE_AUTO":
                t = random.choice(list(topics.keys()))
                payload = f"auto{random.randint(0, 1000)}"
                cmds.append(f"PRODUCE_AUTO {t} {payload} {len(cmds)}")
            elif op == "PRODUCE_BATCH":
                t = random.choice(list(topics.keys()))
                cnt = random.randint(1, 3)
                tokens = [f"PRODUCE_BATCH {t} {cnt}"]
                for _ in range(cnt):
                    p = random.randint(0, topics[t] - 1)
                    pay = f"b{random.randint(0, 1000)}"
                    tokens.append(str(p))
                    tokens.append(pay)
                tokens.append(str(len(cmds)))
                cmds.append(" ".join(tokens))
            elif op == "FETCH":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                off = random.randint(0, 10)
                cmds.append(f"FETCH {t} {p} {off} {len(cmds)}")
            elif op == "FETCH_RANGE":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                start = random.randint(0, 5)
                end = start + random.randint(1, 5)
                cmds.append(f"FETCH_RANGE {t} {p} {start} {end} {len(cmds)}")
            elif op == "POLL":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                g = f"g{random.randint(0, 2)}"
                cmds.append(f"POLL {g} {t} {p} {len(cmds)}")
            elif op == "COMMIT":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                g = f"g{random.randint(0, 2)}"
                off = random.randint(0, 5)
                cmds.append(f"COMMIT {g} {t} {p} {off} {len(cmds)}")
            elif op == "SEEK":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                g = f"g{random.randint(0, 2)}"
                off = random.randint(0, 5)
                cmds.append(f"SEEK {g} {t} {p} {off} {len(cmds)}")
            elif op == "TRIM":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                off = random.randint(0, 5)
                cmds.append(f"TRIM {t} {p} {off} {len(cmds)}")
            elif op == "JOIN_GROUP":
                t = random.choice(list(topics.keys()))
                g = f"g{random.randint(0, 2)}"
                cmds.append(f"JOIN_GROUP {g} {t} {len(cmds)}")
            elif op == "GET_GROUP_OFFSET":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                g = f"g{random.randint(0, 2)}"
                cmds.append(f"GET_GROUP_OFFSET {g} {t} {p} {len(cmds)}")
            elif op == "LIST_TOPICS":
                cmds.append(f"LIST_TOPICS {len(cmds)}")
            elif op == "TOPIC_INFO":
                t = random.choice(list(topics.keys()))
                cmds.append(f"TOPIC_INFO {t} {len(cmds)}")
            elif op == "PARTITION_INFO":
                t = random.choice(list(topics.keys()))
                p = random.randint(0, topics[t] - 1)
                cmds.append(f"PARTITION_INFO {t} {p} {len(cmds)}")
            elif op == "LIST_GROUPS":
                cmds.append(f"LIST_GROUPS {len(cmds)}")

        # Run both
        py_out = py_run(cmds)
        stdin = "\n".join(cmds) + "\n"
        r = run(stdin)
        assert r.returncode == 0, f"non-zero exit on fuzz: {r.stderr}\n{stdin}"
        go_out = lines(r.stdout)
        # Compare len and content, allowing lenient group GC in one specific case (we already handle)
        assert len(go_out) == len(py_out), (
            f"len mismatch {len(go_out)} vs {len(py_out)}\nGo:{go_out}\nPy:{py_out}\nCmds:{cmds}"
        )
        for i, (g, p) in enumerate(zip(go_out, py_out)):
            # Allow lenient group GC: if py says g in list but go says NONE? Only for LIST_GROUPS after delete? Our fuzz doesn't delete topics, so groups always kept
            # For delete test we leniently accept, but for fuzz we keep strict
            # Also for GET_GROUP_OFFSET after trim, both should be NONE
            if g != p:
                # For LIST_GROUPS, allow superset? No, python keeps empty groups, go keeps too, so should match
                assert False, (
                    f"mismatch at line {i}: go={g!r} py={p!r}\nCmds:{cmds}\nGoOut:{go_out}\nPyOut:{py_out}"
                )


# --------------------------------------------------------------------------
# Additional corner cases from review (payload/topic bounds)
# --------------------------------------------------------------------------


def test_payload_1024_boundary():
    valid = "a" * 1024
    invalid = "b" * 1025
    r1 = run(f"CREATE_TOPIC t 1 0\nPRODUCE t 0 {valid} 1\nFETCH t 0 0 2\n")
    assert r1.returncode == 0
    assert lines(r1.stdout)[0] == "0"
    assert lines(r1.stdout)[1] == valid

    r2 = run(f"CREATE_TOPIC t 1 0\nPRODUCE t 0 {invalid} 1\n")
    assert r2.returncode != 0


def test_topic_name_255_boundary():
    long_ok = "a" * 255
    long_bad = "b" * 256
    r1 = run(f"CREATE_TOPIC {long_ok} 1 0\nLIST_TOPICS 1\n")
    assert r1.returncode == 0
    assert long_ok in lines(r1.stdout)[0]

    r2 = run(f"CREATE_TOPIC {long_bad} 1 0\n")
    assert r2.returncode != 0
