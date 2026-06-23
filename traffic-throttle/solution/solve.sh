#!/bin/bash
# Oracle solution: drop the reference throttler implementation into /app and
# initialize a Go module so the verifier can build and test it.
set -euo pipefail

cd /app

cp /solution/throttle.go /app/throttle.go

cat > /app/go.mod <<'EOF'
module throttle

go 1.22
EOF

# Sanity check that the reference compiles inside the task environment.
go build ./...
