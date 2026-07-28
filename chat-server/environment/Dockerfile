FROM ubuntu:24.04

RUN apt-get update && apt-get install -y golang-go python3 python3-pip python3-pytest curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-create data dir
RUN mkdir -p /app/data /tmp/codimango/gocache /tmp/codimango/gopath /logs/verifier
