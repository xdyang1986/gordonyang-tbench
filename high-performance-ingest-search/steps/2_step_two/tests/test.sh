#!/bin/bash
mkdir -p /logs/verifier
rm -f /logs/verifier/ctrf.json
printf '0' > /logs/verifier/reward.txt

apt-get update
apt-get install -y curl > /dev/null 2>&1 || true

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh > /dev/null 2>&1
source $HOME/.local/bin/env

uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  --with requests==2.32.3 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -s

uv run --no-project python -I -S - /logs/verifier/ctrf.json /logs/verifier/reward.txt <<'PY'
import json, sys
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
