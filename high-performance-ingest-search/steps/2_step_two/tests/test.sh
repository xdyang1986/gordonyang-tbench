#!/bin/bash
mkdir -p /logs/verifier
rm -f /logs/verifier/ctrf.json
printf '0' > /logs/verifier/reward.txt

# Use preinstalled python3 + pytest (installed in Dockerfile)
python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -s

python3 -I -S - /logs/verifier/ctrf.json /logs/verifier/reward.txt <<'PY'
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
