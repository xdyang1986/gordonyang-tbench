#!/bin/bash
# Bake pytest into image via environment/Dockerfile, so no apt/curl network needed at test time
# Try to use pytest directly (baked), fallback to uv if needed

# Ensure /app/go.mod exists and build fresh binary to /tmp/uploader for verifier to use (avoids stale /app/uploader from starter image)
cd /app 2>/dev/null || true
if [ -f "go.mod" ]; then
  go build -o /tmp/uploader . 2>&1 | head -20
fi

# Run tests with baked pytest, produce CTRF report
if command -v pytest >/dev/null 2>&1; then
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
else
  # Fallback to uvx if pytest not baked (local dev)
  if command -v uvx >/dev/null 2>&1; then
    uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
  else
    curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
    source $HOME/.local/bin/env
    uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
  fi
fi

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
