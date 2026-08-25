#!/bin/bash
# Placeholder for structure validation - multi-turn uses steps/*/tests/test.sh
# Secure: reward derived from CTRF if present, fail-closed otherwise, with canary
set +e
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
rm -f /logs/verifier/ctrf.json
if [ -f /app/conftest.py ] || [ -f /app/sitecustomize.py ]; then
  echo "Canary triggered" >&2
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi
unset PYTHONPATH
unset PYTHONHOME
python3 -I -S << 'PY'
import json, os
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
    else:
        data = json.load(open(ctrf_path))
        summary = data.get("results", {}).get("summary", data.get("summary", {}))
        tests = int(summary.get("tests", 0))
        passed = int(summary.get("passed", 0))
        failed = int(summary.get("failed", 0))
        other = int(summary.get("other", 0))
        if tests>0 and passed>0 and failed+other==0:
            write_reward(1)
        else:
            write_reward(0)
except:
    write_reward(0)
PY
exit 0
