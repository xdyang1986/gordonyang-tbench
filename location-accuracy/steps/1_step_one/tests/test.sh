#!/bin/bash
# No set -e for baseline – must capture pytest exit even on failure, but avoid apt-get behind fwdproxy
set +e
mkdir -p /logs/verifier
if [ -f /etc/fwdproxy.env ]; then set -a; . /etc/fwdproxy.env; set +a; fi

# Base image ubuntu:24.04 + golang-go installed in Dockerfile, avoid apt-get which fails behind proxy
pip3 install -q pytest==8.4.1 requests==2.32.3 2>&1 | tail -20 || true

if command -v pytest >/dev/null 2>&1; then
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
  EXIT=$?
elif python3 -m pytest --version >/dev/null 2>&1; then
  python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
  EXIT=$?
elif command -v uvx >/dev/null 2>&1; then
  uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 --with requests==2.32.3 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
  EXIT=$?
else
  python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
  EXIT=$?
fi

if [ $EXIT -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
# Materialize buggy starter for Step 2 (appears exactly when Step2 needs it, never before)
mkdir -p /app/buggy && cp /tests/buggy_main.go /app/buggy/main.go || true
cp /tests/buggy_main.go /app/buggy/buggy_main.go 2>/dev/null || true
# Isolation: remove test files so Step-2 trajectory cannot read /tests/
rm -rf /tests/test_outputs.py /tests/test.sh /tests/buggy_main.go || true
rm -rf /tests/test_outputs.py /tests/test.sh /tests/buggy_main.go 2>/dev/null || true
# Also clean possible copies in working dir
rm -rf ./test_outputs.py ./test.sh ./buggy_main.go 2>/dev/null || true
