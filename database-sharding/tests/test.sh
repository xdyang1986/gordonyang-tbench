#!/bin/bash

# Self-contained verifier: no apt-get/curl/uv; writes reward.txt even on failure
# Reward derived from CTRF JSON, not pytest exit code, to prevent sitecustomize.py atexit hijack.

mkdir -p /logs/verifier

set +e

# Run pytest on all tests in /tests (split into two files <64KB each to pass inspectability)
if command -v pytest >/dev/null 2>&1; then
  pytest --ctrf /logs/verifier/ctrf.json /tests -rA
else
  python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests -rA
fi

# Parse CTRF with isolated interpreter (-I -S) to ignore sitecustomize/usercustomize and avoid hijack.
# Reward = 1 iff summary.tests >0, passed >0, failed==0, other==0
python3 -I -S << 'PY'
import json
import sys
import os

ctrf_path = "/logs/verifier/ctrf.json"
reward_path = "/logs/verifier/reward.txt"

def write_reward(val):
    try:
        with open(reward_path, "w") as out:
            out.write(str(val))
    except Exception as e:
        print(f"Failed to write reward: {e}", file=sys.stderr)

try:
    with open(ctrf_path, "r") as f:
        data = json.load(f)
    results = data.get("results", {})
    summary = results.get("summary", {})
    tests = summary.get("tests", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    other = summary.get("other", 0)
    pending = summary.get("pending", 0)
    skipped = summary.get("skipped", 0)
    # Require at least one test, some passed, no failed/other
    if tests > 0 and passed > 0 and failed == 0 and other == 0:
        reward = 1
    else:
        reward = 0
        print(f"CTRF summary: tests={tests} passed={passed} failed={failed} other={other} pending={pending} skipped={skipped}", file=sys.stderr)
except FileNotFoundError:
    print(f"CTRF file not found at {ctrf_path}", file=sys.stderr)
    reward = 0
except Exception as e:
    print(f"Failed to parse CTRF {ctrf_path}: {e}", file=sys.stderr)
    reward = 0

write_reward(reward)
# Exit 0 for pass, 1 for fail to signal verifier
sys.exit(0 if reward == 1 else 1)
PY
parser_status=$?

# Ensure reward.txt exists even if parser crashed
if [ ! -f /logs/verifier/reward.txt ]; then
  echo 0 > /logs/verifier/reward.txt
fi

exit $parser_status
