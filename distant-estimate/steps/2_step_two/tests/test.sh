#!/bin/bash
mkdir -p /logs/verifier
set +e
pip3 install --quiet --break-system-packages pytest-json-ctrf==0.3.5 2>&1 | head -n 5 || true

# AFTR fix: ensure Go binary is built fresh, not a stale script. Force build before tests.
if [ -f /app/go.mod ]; then
  rm -f /app/router || true
  go build -o /app/router . 2>&1 | head -n 20 || true
  ls -lh /app/router 2>&1 | head -n 5 || true
fi

pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA 2>&1
status=${PIPESTATUS[0]:-$?}
if [ ! -f /logs/verifier/ctrf.json ]; then
  echo '{"tests":[]}' > /logs/verifier/ctrf.json
fi
if [ $status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit $status
