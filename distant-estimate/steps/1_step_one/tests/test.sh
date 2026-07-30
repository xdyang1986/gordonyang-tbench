#!/bin/bash
mkdir -p /logs/verifier
set +e
pip3 install --quiet --break-system-packages pytest-json-ctrf==0.3.5 2>&1 | head -n 5 || true
if pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA 2>&1; then
  status=${PIPESTATUS[0]}
else
  pytest /tests/test_outputs.py -rA 2>&1
  status=$?
  if [ ! -f /logs/verifier/ctrf.json ]; then
    echo '{"tests":[]}' > /logs/verifier/ctrf.json
  fi
fi
if [ $status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit $status
