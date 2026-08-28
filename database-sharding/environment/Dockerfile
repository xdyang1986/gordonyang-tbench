FROM public.ecr.aws/docker/library/golang:1.22-bookworm

ENV GOTOOLCHAIN=local
ENV GOFLAGS=-mod=mod
ENV GOCACHE=/tmp/codimango/gocache
ENV GOPATH=/tmp/codimango/gopath

WORKDIR /app
RUN mkdir -p /app/data

# Install pytest for self-contained verifier (no apt-get/curl in test.sh)
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/* && \
    pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5

# Create initial config with 4 shards, some with explicit weights (to test weighted routing)
RUN printf '{\n  "shard_count": 4,\n  "shards": [\n    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},\n    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},\n    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},\n    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}\n  ]\n}\n' > /app/config.json

# Create empty shard files in versioned format with shard_id+version+checksum and empty ops.log (enhanced hard for step2)
RUN python3 << 'PY'
import json, hashlib
def write_shard_versioned(p,d,sid,ver=0):
    cs=hashlib.md5(json.dumps(d, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    open(p,'w').write(json.dumps({"shard_id":sid,"version":ver,"data":d,"checksum":cs}, indent=2))
for i in range(4):
    write_shard_versioned(f'/app/data/shard_{i}.json', {}, i, 0)
open('/app/data/ops.log','w').write('')
print("Created empty versioned shards with shard_id, version, checksum and empty ops.log")

# Legacy data old flat format (120 users + 30 orders) plus some global keys
import random
data = {}
for i in range(120):
    data[f'user:{i}'] = {'id': i, 'name': f'User {i}', 'email': f'user{i}@example.com', 'balance': random.randint(0,10000)}
for i in range(30):
    data[f'order:{i}'] = {'order_id': i, 'user_id': i % 120, 'amount': random.randint(10,500)}
for i in range(5):
    data[f'global:config:{i}'] = {'cfg': i, 'value': f'g{i}'}
open('/app/data/legacy.json','w').write(json.dumps(data, indent=2))
print(f"Created legacy with {len(data)} keys including 5 global: keys (old format)")
PY

# Create skeleton go.mod
RUN printf 'module sharding\n\ngo 1.22\n' > /app/go.mod

# Create skeleton main.go
RUN cat > /app/main.go << 'GO'
package main
import (
  "fmt"
  "os"
)
func main() {
  fmt.Fprintln(os.Stderr, "Sharding proxy not implemented yet")
  os.Exit(1)
}
GO

RUN ls -lh /app/ && ls -lh /app/data && cat /app/config.json && cat /app/go.mod
