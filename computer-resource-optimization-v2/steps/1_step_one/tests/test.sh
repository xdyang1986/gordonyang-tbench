#!/bin/bash
# Fail-closed: reward 0 unless pytest exits 0 and ctrf.json exists.
set +e
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
rm -f /logs/verifier/ctrf.json

python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
EXIT=$?

if [ $EXIT -eq 0 ] && [ -f /logs/verifier/ctrf.json ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

# Isolation: Step 2 inherits this container (inherit_prior_session=true).
# Remove everything that lets the next agent read the hidden suite.
rm -rf /tests/test_outputs.py /tests/test.sh /tests/__pycache__ /tests/.pytest_cache 2>/dev/null || true
rm -rf /app/__pycache__ /app/.pytest_cache 2>/dev/null || true
find /app /tmp /root -maxdepth 3 -name 'test_outputs.py' -delete 2>/dev/null || true

exit 0
