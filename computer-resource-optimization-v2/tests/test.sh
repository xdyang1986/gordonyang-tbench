#!/bin/bash
# Placeholder for structure validation - multi-turn uses steps/*/tests/test.sh
# Secure: reward derived from CTRF, not hardcoded, to prevent sitecustomize bypass.
set +e
echo "Top-level test placeholder for multi-turn task."
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
rm -f /logs/verifier/ctrf.json

# If per-step tests didn't run, keep reward 0 (fail-closed)
# If CTRF exists from prior run, parse it securely under isolated interpreter
python3 -I -S << 'PY'
import json, os, sys
ctrf_path = "/logs/verifier/ctrf.json"
reward_path = "/logs/verifier/reward.txt"
def write_reward(v):
    try:
        open(reward_path, "w").write(str(v))
    except:
        pass
try:
    if not os.path.exists(ctrf_path):
        write_reward(0)
        sys.exit(0)
    data = json.load(open(ctrf_path))
    summary = data.get("results", {}).get("summary", data.get("summary", {}))
    tests = int(summary.get("tests", 0))
    passed = int(summary.get("passed", 0))
    failed = int(summary.get("failed", 0))
    other = int(summary.get("other", 0))
    if tests > 0 and passed > 0 and (failed + other) == 0:
        write_reward(1)
    else:
        write_reward(0)
except Exception:
    write_reward(0)
PY
exit 0
