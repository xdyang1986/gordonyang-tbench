#!/bin/bash
# No set -e — we must capture pytest exit code and write reward.txt even on failure.
# Install python deps (image already has pip but ensure)
apt-get update -qq && apt-get install -y -qq python3-pip > /dev/null 2>&1 || true
pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5 > /dev/null 2>&1 || pip3 install pytest==8.4.1 pytest-json-ctrf==0.3.5

# Ensure go is available and tidy
export GOCACHE=/tmp/codimango/gocache
export GOPATH=/tmp/codimango/gopath
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local

cd /app
go mod tidy || true
go vet ./... || true

# Run pytest without set -e aborting — capture exit
set +e
python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
EXIT=$?
set -e

if [ $EXIT -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit $EXIT
