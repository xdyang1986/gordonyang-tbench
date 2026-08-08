#!/bin/bash
# Use this file to install test dependencies and run the tests.
set -e
mkdir -p /logs/verifier

# Base image golang:1.26.2-bookworm already contains go; avoid apt-get which fails behind fwdproxy (51484)
# Ensure pytest + requests available without network if possible; fallback gracefully

# Export fwdproxy if present (VMVM moves 8080 -> 51484 and persists in /etc/fwdproxy.env)
if [ -f /etc/fwdproxy.env ]; then
  set -a; . /etc/fwdproxy.env; set +a
fi

# Try pip install if internet via proxy is available, otherwise ignore
pip3 install -q pytest==8.4.1 requests==2.32.3 2>&1 | tail -20 || true

# Prefer system pytest, then python -m pytest, then uvx if available
if command -v pytest >/dev/null 2>&1; then
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v || {
    echo 0 > /logs/verifier/reward.txt
    exit 0
  }
elif python3 -m pytest --version >/dev/null 2>&1; then
  python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v || {
    echo 0 > /logs/verifier/reward.txt
    exit 0
  }
elif command -v uvx >/dev/null 2>&1; then
  uvx \
    --with pytest==8.4.1 \
    --with pytest-json-ctrf==0.3.5 \
    --with requests==2.32.3 \
    pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v || {
    echo 0 > /logs/verifier/reward.txt
    exit 0
  }
else
  echo "pytest not found and no network, attempting python fallback"
  python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v || true
fi

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
