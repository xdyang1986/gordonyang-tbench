#!/bin/bash
# Use this file to install test dependencies and run the tests.
# It will be copied to /tests/test.sh and run from the working directory.

# Fail closed from the first line: any abort below leaves this 0 standing, and
# a report left by an earlier run in this container cannot be graded as if it
# were this one's.
mkdir -p /logs/verifier
rm -f /logs/verifier/ctrf.json
printf '0' > /logs/verifier/reward.txt

apt-get update
apt-get install -y curl > /dev/null 2>&1 || true

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh > /dev/null 2>&1

source $HOME/.local/bin/env

# CTRF produces a standard test report in JSON format, which is what the reward
# below is computed from.
uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  --with requests==2.32.3 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

# Grade from the CTRF report, never from a process exit status
uv run --no-project python -I -S - /logs/verifier/ctrf.json /logs/verifier/reward.txt <<'PY'
import json
import sys

CTRF, REWARD = sys.argv[1], sys.argv[2]

try:
    with open(CTRF) as fh:
        summary = json.load(fh)['results']['summary']
    total = int(summary['tests'])
    bad = int(summary.get('failed', 0)) + int(summary.get('other', 0))
    passed = int(summary['passed'])
    ok = total > 0 and bad == 0 and passed > 0
except Exception as exc:
    print(f'reward 0: cannot read {CTRF}: {exc}')
    ok = False

with open(REWARD, 'w') as fh:
    fh.write('1' if ok else '0')
PY
