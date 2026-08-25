#!/bin/bash
# Fail-closed: reward 0 unless ctrf.json shows all tests passed.
# Reward is derived from /logs/verifier/ctrf.json, not from pytest exit code,
# to prevent sitecustomize.py atexit os._exit(0) bypass.
set +e
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
rm -f /logs/verifier/ctrf.json

# AFTR fix: ensure Go binary is built fresh, not a stale script. Force build before tests.
if [ -f /app/go.mod ]; then
  rm -f /app/router || true
  go build -o /app/router . 2>&1 | head -n 20 || true
  ls -lh /app/router 2>&1 | head -n 5 || true
fi

python3 -I -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v

# Parse CTRF under isolated interpreter so sitecustomize cannot kill parser.
# -S skips site initialization (no sitecustomize), -I drops PYTHONPATH and cwd from sys.path.
python3 -I -S << 'PY'
import json
import os
import sys

ctrf_path = "/logs/verifier/ctrf.json"
reward_path = "/logs/verifier/reward.txt"

def write_reward(val):
    try:
        with open(reward_path, "w") as f:
            f.write(str(val))
    except Exception:
        pass

try:
    if not os.path.exists(ctrf_path):
        write_reward(0)
        sys.exit(0)
    with open(ctrf_path, "r") as fh:
        data = json.load(fh)
    summary = {}
    if isinstance(data, dict):
        if "results" in data and isinstance(data["results"], dict):
            summary = data["results"].get("summary", {})
        if not summary:
            summary = data.get("summary", {})
    try:
        tests = int(summary.get("tests", 0))
        passed = int(summary.get("passed", 0))
        failed = int(summary.get("failed", 0))
        other = int(summary.get("other", 0))
    except Exception:
        write_reward(0)
        sys.exit(0)

    if tests > 0 and passed > 0 and (failed + other) == 0:
        write_reward(1)
    else:
        write_reward(0)
except Exception as exc:
    try:
        print(f"reward parser error: {exc}", file=sys.stderr)
    except:
        pass
    write_reward(0)
PY

# Scrub test suite so next session cannot read hidden tests (inherit_prior_session=true)
rm -rf /tests/test_outputs.py /tests/test.sh /tests/__pycache__ /tests/.pytest_cache 2>/dev/null || true
