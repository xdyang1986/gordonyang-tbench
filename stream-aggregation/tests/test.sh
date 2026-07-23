#!/bin/bash
# Verifier entrypoint — run pytest and write reward.

set -e

# Ensure pytest is available (Dockerfile pre-installs it)
if ! pytest --version >/dev/null 2>&1; then
  if ! python3 -m pytest --version >/dev/null 2>&1; then
    apt-get update && apt-get install -y python3 python3-pip curl
    pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5 || pip3 install pytest==8.4.1 pytest-json-ctrf==0.3.5
  fi
fi

mkdir -p /logs/verifier

# Run tests — capture exit code directly, do not use $? after an if/elif with ':' which masks status.
set +e
pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
STATUS=$?

# Fallback to alternative python interpreters if first invocation failed due to missing plugin/path
if [ $STATUS -ne 0 ]; then
  echo "pytest failed, trying python3 -m pytest fallback..." >&2
  python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
  STATUS=$?
fi

if [ $STATUS -ne 0 ]; then
  echo "python3 fallback failed, trying /usr/bin/python3 -m pytest..." >&2
  /usr/bin/python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
  STATUS=$?
fi
set -e

if [ $STATUS -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit $STATUS
