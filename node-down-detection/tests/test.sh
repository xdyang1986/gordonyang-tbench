#!/bin/bash
# Install test dependencies and run the tests. Copied to /tests/test.sh and run
# from the working directory (/app) in the grading container.

set -u

mkdir -p /logs/verifier

# The grading tests are an external (_test) package for failuredetector, so they
# must live alongside the package under test to compile against it.
cp /tests/detector_test.go /app/failuredetector/detector_test.go

cd /app

# Use only the toolchain present in the image; never fetch over the network.
export GOTOOLCHAIN=local
export GOFLAGS=-mod=mod

go vet ./failuredetector/ 2>&1 || true
# -race verifies the "safe for concurrent use" contract from instruction.md.
# The golang:1.26-bookworm base image ships gcc, so CGO (required by -race) works.
go test ./failuredetector/ -race -count=1 -v
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit 0
