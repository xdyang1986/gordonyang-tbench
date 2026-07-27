#!/bin/bash

# Self-contained verifier: no apt-get/curl/uv; writes reward.txt even on failure

mkdir -p /logs/verifier

set +e

if command -v pytest >/dev/null 2>&1; then
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
  status=$?
else
  python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
  status=$?
fi

if [ $status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit $status
