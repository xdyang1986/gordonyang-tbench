#!/bin/bash
set -e

# Self-contained verifier: uses pytest baked into image (no apt-get/curl/uv download during grading)

mkdir -p /logs/verifier

# Try pytest binary first, fallback to python3 -m pytest for compatibility
if command -v pytest >/dev/null 2>&1; then
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
else
  python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
fi

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
