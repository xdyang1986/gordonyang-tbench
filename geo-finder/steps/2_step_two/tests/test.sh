#!/bin/bash
# Fail-closed: reward 0 unless pytest exits 0 and ctrf.json exists.
set +e
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
rm -f /logs/verifier/ctrf.json

# Python toolchain installed at build time, no network needed in test.sh
pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
EXIT=$?

if [ $EXIT -eq 0 ] && [ -f /logs/verifier/ctrf.json ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
  if [ ! -f /logs/verifier/ctrf.json ]; then
    echo "ctrf.json missing after pytest exit $EXIT – keeping reward 0" >&2
  fi
fi

exit 0
