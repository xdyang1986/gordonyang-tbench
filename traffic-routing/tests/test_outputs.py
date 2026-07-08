"""
Grader for `router` — a consistent-hash traffic router.

Behaviors under test (several are only implied by the instruction):
  - config (JSON): {"replicas": R, "nodes": [{"id","weight","status"}...]}.
    weight = number of virtual nodes the node places on the ring; status up/down.
  - hash = CRC-32 IEEE (crc32.ChecksumIEEE == zlib.crc32) over UTF-8 bytes;
    ring space [0, 2**32). A virtual point for node id at index i sits at
    H("<id>#<i>"); a key sits at H(key). Ring sorted by (pos, id, i).
  - a key routes to the first ring point clockwise from its position, then to
    further DISTINCT nodes clockwise, until R nodes are collected. The ring is
    CIRCULAR (wraps past the last point).
  - down nodes and weight-0 nodes place NO points (never routed to). Eligible
    node = up AND weight > 0.
  - requests: newline-delimited keys; a trailing newline at EOF is not an extra
    empty key; a blank line in the middle IS an empty key ("" is routable).
  - output: one JSON array per request, in input order (duplicates repeat);
    [] when there are no eligible nodes.
  - exit 0 iff every request routed to R nodes; 1 if any request is
    under-replicated (fewer than R eligible nodes); 2 on unusable input
    (bad/missing config JSON, replicas<1, empty/duplicate id, negative weight,
    bad status, unreadable requests).

`route_all()` is a reference implementation of the whole contract; every test's
expected values are computed from it, so hard-coded outputs cannot pass.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import zlib

import pytest

SRC_DIR = "/app/src"


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def router():
    assert os.path.isdir(SRC_DIR), f"{SRC_DIR} does not exist"
    assert list(_walk_go(SRC_DIR)), f"no .go source files found under {SRC_DIR}"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "missing go.mod"
    go = shutil.which("go")
    assert go, "the go toolchain is not available in the verifier environment"
    out_dir = tempfile.mkdtemp(prefix="router_build_")
    binary = os.path.join(out_dir, "router")
    proc = subprocess.run(
        [go, "build", "-o", binary, "./..."], cwd=SRC_DIR, capture_output=True, text=True
    )
    assert proc.returncode == 0, f"`go build ./...` failed:\n{proc.stdout}\n{proc.stderr}"
    assert os.path.isfile(binary), "go build did not produce a binary"
    return binary


def _walk_go(root):
    for dp, _d, files in os.walk(root):
        for f in files:
            if f.endswith(".go"):
                yield os.path.join(dp, f)


# --------------------------------------------------------------------------- #
# Reference contract
# --------------------------------------------------------------------------- #
def H(s: str) -> int:
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


def build_ring(cfg):
    ring = []  # (pos, id, i)
    eligible = 0
    for nd in cfg["nodes"]:
        if nd.get("status") != "up" or int(nd.get("weight", 0)) <= 0:
            continue
        eligible += 1
        for i in range(int(nd["weight"])):
            ring.append((H(f'{nd["id"]}#{i}'), nd["id"], i))
    ring.sort(key=lambda t: (t[0], t[1], t[2]))
    return ring, eligible


def zones_of(cfg):
    # A node's zone defaults to its own id when unset.
    zoneOf = {}
    for nd in cfg["nodes"]:
        z = nd.get("zone") or nd["id"]
        zoneOf[nd["id"]] = z
    return zoneOf


def route_key(ring, R, key, zoneOf=None):
    if not ring:
        return []
    pos = H(key)
    # first index with ring[idx].pos >= pos, else wrap to 0
    lo, hi = 0, len(ring)
    while lo < hi:
        mid = (lo + hi) // 2
        if ring[mid][0] >= pos:
            hi = mid
        else:
            lo = mid + 1
    idx = lo if lo < len(ring) else 0
    # distinct eligible nodes in clockwise first-encounter order
    seen = set()
    distinct = []
    for c in range(len(ring)):
        e = ring[(idx + c) % len(ring)]
        if e[1] not in seen:
            seen.add(e[1])
            distinct.append(e[1])
    # zone-diverse selection (default zone = node id when unset/None)
    used = set()
    pending = []
    result = []
    for d in distinct:
        if len(result) >= R:
            break
        z = (zoneOf or {}).get(d, d)
        if z not in used:
            used.add(z)
            result.append(d)
        else:
            pending.append(d)
    for d in pending:
        if len(result) >= R:
            break
        result.append(d)
    return result


def route_all(cfg, keys):
    """Returns (routes, exit_code). Raises ValueError to signal exit-2 inputs."""
    if not isinstance(cfg, dict):
        raise ValueError("config not an object")
    R = cfg.get("replicas", 0)
    if not isinstance(R, int) or R < 1:
        raise ValueError("bad replicas")
    seen = set()
    for nd in cfg.get("nodes", []):
        nid = nd.get("id", "")
        if not nid:
            raise ValueError("empty id")
        if nid in seen:
            raise ValueError("dup id")
        seen.add(nid)
        if int(nd.get("weight", 0)) < 0:
            raise ValueError("neg weight")
        if nd.get("status") not in ("up", "down"):
            raise ValueError("bad status")
    ring, _elig = build_ring(cfg)
    zoneOf = zones_of(cfg)
    routes = [route_key(ring, R, k, zoneOf) for k in keys]
    degraded = any(len(r) < R for r in routes)
    return routes, (1 if degraded else 0)


def split_requests(raw: bytes):
    if len(raw) == 0:
        return []
    s = raw.decode("utf-8")
    parts = s.split("\n")
    if s.endswith("\n"):
        parts = parts[:-1]
    return parts


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run(router, config, requests):
    """config: dict (json-encoded) or bytes (verbatim).
    requests: list[str] (joined with '\n' + trailing '\n') or bytes (verbatim)."""
    tmp = tempfile.mkdtemp(prefix="router_run_")
    cfg_path = os.path.join(tmp, "config.json")
    req_path = os.path.join(tmp, "requests.txt")
    with open(cfg_path, "wb") as fh:
        fh.write(config if isinstance(config, bytes) else json.dumps(config).encode())
    with open(req_path, "wb") as fh:
        if isinstance(requests, bytes):
            fh.write(requests)
        else:
            fh.write(("".join(k + "\n" for k in requests)).encode())
    proc = subprocess.run(
        [router, "--config", cfg_path, "--requests", req_path],
        capture_output=True, text=True, timeout=60,
    )
    lines = [ln for ln in proc.stdout.splitlines()]
    parsed = None
    try:
        parsed = [json.loads(ln) for ln in lines]
    except json.JSONDecodeError:
        parsed = None
    return proc, parsed


def cfg(replicas, *nodes):
    return {"replicas": replicas, "nodes": list(nodes)}


def nd(id, weight, status="up", zone=""):
    d = {"id": id, "weight": weight, "status": status}
    if zone:
        d["zone"] = zone
    return d


def check(router, config, keys, expect_exit=None):
    routes, exit_code = route_all(config, keys)
    if expect_exit is not None:
        assert exit_code == expect_exit, f"oracle exit {exit_code} != {expect_exit}"
    proc, parsed = run(router, config, keys)
    assert proc.returncode == exit_code, (
        f"exit {proc.returncode} != {exit_code}; stderr={proc.stderr!r} stdout={proc.stdout!r}"
    )
    assert parsed == routes, f"routes {parsed} != {routes}"
    return routes


# --------------------------------------------------------------------------- #
# Standard-library-only constraint
# --------------------------------------------------------------------------- #
def test_go_mod_has_no_external_requires():
    with open(os.path.join(SRC_DIR, "go.mod")) as fh:
        for line in fh.read().splitlines():
            m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line.strip())
            if m:
                assert "." not in m.group(2).split("/")[0], f"external dep: {m.group(2)}"


def test_imports_are_stdlib_only():
    import_re = re.compile(r'"([^"]+)"')
    found = False
    for path in _walk_go(SRC_DIR):
        text = open(path).read()
        for block in re.findall(r"import\s*\((.*?)\)", text, flags=re.S):
            for imp in import_re.findall(block):
                found = True
                assert "." not in imp.split("/")[0], f"non-stdlib import {imp!r}"
        for imp in re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', text):
            found = True
            assert "." not in imp.split("/")[0], f"non-stdlib import {imp!r}"
    assert found, "no imports found (unexpected)"


# --------------------------------------------------------------------------- #
# Basic routing
# --------------------------------------------------------------------------- #
def test_single_node_all_keys_route_to_it(router):
    c = cfg(1, nd("n1", 3))
    check(router, c, ["a", "b", "c", ""], expect_exit=0)


def test_primary_is_first_point_clockwise(router):
    c = cfg(1, nd("n1", 2), nd("n2", 2), nd("n3", 2))
    check(router, c, [f"key{i}" for i in range(20)], expect_exit=0)


def test_route_length_equals_replicas_when_enough_nodes(router):
    c = cfg(3, nd("n1", 4), nd("n2", 4), nd("n3", 4), nd("n4", 4))
    routes = check(router, c, [f"user-{i}" for i in range(30)], expect_exit=0)
    assert all(len(r) == 3 for r in routes)


def test_replicas_are_distinct_nodes(router):
    c = cfg(3, nd("n1", 5), nd("n2", 5), nd("n3", 5), nd("n4", 5))
    routes = check(router, c, [f"k{i}" for i in range(40)], expect_exit=0)
    for r in routes:
        assert len(set(r)) == len(r), f"non-distinct route {r}"


# --------------------------------------------------------------------------- #
# Circular wraparound
# --------------------------------------------------------------------------- #
def test_wraparound_key_past_last_point(router):
    # Find a key whose hash exceeds the max ring position -> must wrap to index 0.
    c = cfg(1, nd("n1", 3), nd("n2", 3))
    ring, _ = build_ring(c)
    maxpos = max(p[0] for p in ring)
    key = next(k for k in (f"probe{i}" for i in range(100000)) if H(k) > maxpos)
    check(router, c, [key], expect_exit=0)


def test_wraparound_collecting_replicas(router):
    # A key near the top of the ring must wrap to gather all R distinct nodes.
    c = cfg(3, nd("n1", 2), nd("n2", 2), nd("n3", 2))
    ring, _ = build_ring(c)
    maxpos = max(p[0] for p in ring)
    key = next(k for k in (f"w{i}" for i in range(100000)) if H(k) > maxpos)
    routes = check(router, c, [key], expect_exit=0)
    assert len(routes[0]) == 3 and len(set(routes[0])) == 3


def test_full_replication_reaches_all_nodes(router):
    c = cfg(4, nd("a", 2), nd("b", 2), nd("c", 2), nd("d", 2))
    routes = check(router, c, [f"x{i}" for i in range(25)], expect_exit=0)
    for r in routes:
        assert set(r) == {"a", "b", "c", "d"}


# --------------------------------------------------------------------------- #
# R clamping: fewer eligible nodes than R
# --------------------------------------------------------------------------- #
def test_replicas_exceed_node_count_is_degraded(router):
    c = cfg(5, nd("n1", 3), nd("n2", 3))
    routes = check(router, c, ["a", "b", "c"], expect_exit=1)
    for r in routes:
        assert set(r) == {"n1", "n2"} and len(r) == 2


def test_replicas_exceed_by_one(router):
    c = cfg(4, nd("n1", 2), nd("n2", 2), nd("n3", 2))
    routes = check(router, c, ["a", "b"], expect_exit=1)
    for r in routes:
        assert len(r) == 3


def test_no_eligible_nodes_routes_empty(router):
    c = cfg(2, nd("n1", 2, status="down"), nd("n2", 0))
    routes = check(router, c, ["a", "b"], expect_exit=1)
    assert routes == [[], []]


# --------------------------------------------------------------------------- #
# weight 0 -> no points -> never routed to
# --------------------------------------------------------------------------- #
def test_weight_zero_node_is_never_selected(router):
    c = cfg(2, nd("n1", 3), nd("n2", 3), nd("ghost", 0))
    routes = check(router, c, [f"k{i}" for i in range(30)], expect_exit=0)
    assert all("ghost" not in r for r in routes)


def test_weight_zero_reduces_eligibility(router):
    # Two real nodes + one weight-0 node, R=3 -> degraded (only 2 eligible).
    c = cfg(3, nd("n1", 3), nd("n2", 3), nd("n3", 0))
    routes = check(router, c, ["a", "b", "c"], expect_exit=1)
    for r in routes:
        assert set(r) == {"n1", "n2"}


# --------------------------------------------------------------------------- #
# Down nodes: excluded from ring, keys migrate, never in any route
# --------------------------------------------------------------------------- #
def test_down_node_never_appears(router):
    c = cfg(2, nd("n1", 4), nd("n2", 4, status="down"), nd("n3", 4))
    routes = check(router, c, [f"k{i}" for i in range(40)], expect_exit=0)
    assert all("n2" not in r for r in routes)


def test_down_node_keys_migrate_to_next_up(router):
    # Routes with n2 down must equal routing over {n1,n3} only (migration),
    # not "route over all three then drop n2 from the output".
    up_only = cfg(2, nd("n1", 4), nd("n3", 4))
    with_down = cfg(2, nd("n1", 4), nd("n2", 4, status="down"), nd("n3", 4))
    keys = [f"m{i}" for i in range(40)]
    routes = check(router, with_down, keys, expect_exit=0)
    ring, _ = build_ring(up_only)
    expected = [route_key(ring, 2, k) for k in keys]
    assert routes == expected


def test_all_down_is_empty_routes(router):
    c = cfg(1, nd("n1", 3, status="down"), nd("n2", 3, status="down"))
    routes = check(router, c, ["a", "b"], expect_exit=1)
    assert routes == [[], []]


# --------------------------------------------------------------------------- #
# Request parsing edges
# --------------------------------------------------------------------------- #
def test_trailing_newline_is_not_an_extra_key(router):
    c = cfg(1, nd("n1", 3))
    proc, parsed = run(router, c, b"a\nb\n")
    assert proc.returncode == 0
    assert parsed == [["n1"], ["n1"]]


def test_no_trailing_newline(router):
    c = cfg(1, nd("n1", 3))
    proc, parsed = run(router, c, b"a\nb")
    assert proc.returncode == 0
    assert parsed == [["n1"], ["n1"]]


def test_blank_line_in_middle_is_empty_key(router):
    c = cfg(1, nd("n1", 3))
    keys = split_requests(b"a\n\nb\n")
    assert keys == ["a", "", "b"]
    check(router, c, keys, expect_exit=0)


def test_empty_key_is_routable(router):
    c = cfg(2, nd("n1", 3), nd("n2", 3))
    routes = check(router, c, [""], expect_exit=0)
    assert len(routes[0]) == 2


def test_empty_requests_file_no_output(router):
    c = cfg(2, nd("n1", 3), nd("n2", 3))
    proc, parsed = run(router, c, b"")
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_duplicate_request_lines_repeat_in_order(router):
    c = cfg(2, nd("n1", 4), nd("n2", 4), nd("n3", 4))
    routes = check(router, c, ["dup", "dup", "other", "dup"], expect_exit=0)
    assert routes[0] == routes[1] == routes[3]
    assert routes[0] == route_key(build_ring(c)[0], 2, "dup")


# --------------------------------------------------------------------------- #
# Unusable input -> exit 2
# --------------------------------------------------------------------------- #
def test_invalid_json_is_exit_2(router):
    proc, _ = run(router, b"{not json", ["a"])
    assert proc.returncode == 2


def test_replicas_zero_is_exit_2(router):
    proc, _ = run(router, cfg(0, nd("n1", 3)), ["a"])
    assert proc.returncode == 2


def test_missing_replicas_is_exit_2(router):
    proc, _ = run(router, {"nodes": [nd("n1", 3)]}, ["a"])
    assert proc.returncode == 2


def test_duplicate_node_id_is_exit_2(router):
    proc, _ = run(router, cfg(1, nd("n1", 3), nd("n1", 2)), ["a"])
    assert proc.returncode == 2


def test_empty_node_id_is_exit_2(router):
    proc, _ = run(router, cfg(1, nd("", 3)), ["a"])
    assert proc.returncode == 2


def test_negative_weight_is_exit_2(router):
    proc, _ = run(router, cfg(1, nd("n1", -1)), ["a"])
    assert proc.returncode == 2


def test_bad_status_is_exit_2(router):
    proc, _ = run(router, cfg(1, {"id": "n1", "weight": 3, "status": "maybe"}), ["a"])
    assert proc.returncode == 2


def test_missing_requests_file_is_exit_2(router):
    tmp = tempfile.mkdtemp(prefix="router_nc_")
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w") as fh:
        json.dump(cfg(1, nd("n1", 3)), fh)
    missing = os.path.join(tmp, "nope.txt")
    proc = subprocess.run(
        [router, "--config", cfg_path, "--requests", missing],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2


def test_missing_config_file_is_exit_2(router):
    tmp = tempfile.mkdtemp(prefix="router_mc_")
    req_path = os.path.join(tmp, "requests.txt")
    with open(req_path, "w") as fh:
        fh.write("a\n")
    proc = subprocess.run(
        [router, "--config", os.path.join(tmp, "nope.json"), "--requests", req_path],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2


# --------------------------------------------------------------------------- #
# Binary-ish / unicode keys and ids
# --------------------------------------------------------------------------- #
def test_unicode_keys(router):
    c = cfg(2, nd("n1", 4), nd("n2", 4), nd("n3", 4))
    check(router, c, ["café", "naïve", "日本語", "emoji-🚀"], expect_exit=0)


def test_ids_with_hash_separator_do_not_collide(router):
    # "a#1#0" vs "a#1" + "#0": point strings are unambiguous per the stated format.
    c = cfg(1, nd("a", 2), nd("a#1", 2))
    check(router, c, [f"k{i}" for i in range(15)], expect_exit=0)


# --------------------------------------------------------------------------- #
# Zone-aware replica placement: replicas prefer distinct zones, falling back to
# same-zone nodes only when fewer than R zones are reachable.
# --------------------------------------------------------------------------- #
def test_replicas_prefer_distinct_zones(router):
    # 3 zones available, R=3 -> every route spans 3 distinct zones.
    c = cfg(3, nd("a", 5, zone="z1"), nd("b", 5, zone="z1"),
            nd("c", 5, zone="z2"), nd("d", 5, zone="z3"), nd("e", 5, zone="z3"))
    zoneOf = zones_of(c)
    routes = check(router, c, [f"k{i}" for i in range(60)], expect_exit=0)
    for r in routes:
        assert len(r) == 3
        assert len({zoneOf[n] for n in r}) == 3, f"route {r} not zone-diverse"


def test_zone_rule_defers_same_zone_and_changes_output(router):
    # Two nodes share z1, one is in z2. With R=2 a same-zone pick must be
    # deferred behind the z2 node -> output differs from plain first-2-distinct.
    c = cfg(2, nd("a", 5, zone="z1"), nd("b", 5, zone="z1"), nd("c", 5, zone="z2"))
    zoneOf = zones_of(c)
    ring, _ = build_ring(c)
    keys = [f"k{i}" for i in range(80)]
    routes = check(router, c, keys, expect_exit=0)
    for r in routes:
        assert len({zoneOf[n] for n in r}) == 2, f"route {r} not zone-diverse"
    plain = [route_key(ring, 2, k, None) for k in keys]  # zone-ignoring baseline
    assert any(routes[i] != plain[i] for i in range(len(keys))), \
        "zone rule never changed the output (test not exercising diversity)"


def test_zone_fallback_when_fewer_zones_than_replicas(router):
    # Only 2 zones but R=3 -> the third replica falls back to a same-zone node.
    c = cfg(3, nd("a", 5, zone="z1"), nd("b", 5, zone="z1"), nd("c", 5, zone="z2"))
    routes = check(router, c, [f"k{i}" for i in range(40)], expect_exit=0)
    for r in routes:
        assert len(r) == 3 and set(r) == {"a", "b", "c"}, f"fallback failed: {r}"


def test_missing_zone_defaults_to_own_rack(router):
    # No zone field -> each node its own zone -> plain first-R-distinct behavior.
    c = cfg(2, nd("a", 5), nd("b", 5), nd("c", 5))
    ring, _ = build_ring(c)
    keys = [f"k{i}" for i in range(30)]
    routes = check(router, c, keys, expect_exit=0)
    assert routes == [route_key(ring, 2, k, None) for k in keys]


def test_unzoned_nodes_are_distinct_zones_not_one_shared_zone(router):
    # Mixed: two unzoned nodes + one zoned. Unzoned default to their OWN id as
    # zone (three distinct zones), NOT a single shared "" zone. With R=3 every
    # route must contain all three; the discriminator is that an impl treating
    # unzoned nodes as one shared zone would order/spread them differently.
    c = cfg(3, nd("x", 5), nd("y", 5), nd("z", 5, zone="zoneA"))
    zoneOf = zones_of(c)
    assert zoneOf == {"x": "x", "y": "y", "z": "zoneA"}
    routes = check(router, c, [f"k{i}" for i in range(60)], expect_exit=0)
    for r in routes:
        assert len({zoneOf[n] for n in r}) == 3


def test_all_same_zone_is_plain_walk(router):
    # Every node in one zone -> diversity can never fire; route == first-R walk.
    c = cfg(2, nd("a", 5, zone="z"), nd("b", 5, zone="z"), nd("c", 5, zone="z"))
    ring, _ = build_ring(c)
    keys = [f"k{i}" for i in range(30)]
    routes = check(router, c, keys, expect_exit=0)
    # first pass picks one node (zone z), the rest fall back in clockwise order
    assert routes == [route_key(ring, 2, k, None) for k in keys]


# --------------------------------------------------------------------------- #
# Randomized model (ref and oracle share the CRC + tie-break, so exact compare)
# --------------------------------------------------------------------------- #
def test_randomized_model(router):
    import random

    rng = random.Random(20260708)
    for trial in range(30):
        nnodes = rng.randint(1, 8)
        nodes = []
        for j in range(nnodes):
            nodes.append(nd(
                f"node{j}",
                rng.choice([0, 1, 1, 2, 3, 5]),
                status=rng.choice(["up", "up", "up", "down"]),
                zone=rng.choice(["", "zA", "zB", "zA", "zC"]),
            ))
        c = cfg(rng.randint(1, 6), *nodes)
        keys = [f"t{trial}-req{i}" for i in range(rng.randint(0, 25))]
        if rng.random() < 0.3:
            keys.append("")
        check(router, c, keys)
