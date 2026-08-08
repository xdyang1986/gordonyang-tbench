#!/bin/bash
set +e
mkdir -p /logs/verifier
if [ -f /etc/fwdproxy.env ]; then set -a; . /etc/fwdproxy.env; set +a; fi
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
