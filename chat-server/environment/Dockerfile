FROM ubuntu:24.04

RUN apt-get update && apt-get install -y golang-go python3 python3-pip curl && rm -rf /var/lib/apt/lists/* \
    && pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5

WORKDIR /app

# Pre-create data dir
RUN mkdir -p /app/data /tmp/codimango/gocache /tmp/codimango/gopath /logs/verifier
