FROM public.ecr.aws/docker/library/golang:1.22-bookworm

ENV GOTOOLCHAIN=local
ENV GOFLAGS=-mod=mod
ENV GOCACHE=/tmp/codimango/gocache
ENV GOPATH=/tmp/codimango/gopath

WORKDIR /app
RUN mkdir -p /app/data

# Install python3 pip and pytest for self-contained verifier (no apt-get/curl in test.sh)
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/* && \
    pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5

# Create initial config.json with 4 shards
RUN printf '{\n  "shard_count": 4,\n  "shards": [\n    {"id": 0, "path": "/app/data/shard_0.json"},\n    {"id": 1, "path": "/app/data/shard_1.json"},\n    {"id": 2, "path": "/app/data/shard_2.json"},\n    {"id": 3, "path": "/app/data/shard_3.json"}\n  ]\n}\n' > /app/config.json

# Create empty shard files in new format with checksum (initially empty data)
RUN python3 << 'PY'
import json, hashlib
def write_shard(path, data):
    data_json = json.dumps(data, sort_keys=True, separators=(',', ':'))
    checksum = hashlib.md5(data_json.encode()).hexdigest()
    with open(path, 'w') as f:
        json.dump({"data": data, "checksum": checksum}, f, indent=2)
for i in range(4):
    write_shard(f'/app/data/shard_{i}.json', {})
print("Created empty shards with checksum format")

# Legacy data (old flat format without checksum, simulates old single DB)
import random
data = {}
for i in range(120):
    data[f'user:{i}'] = {'id': i, 'name': f'User {i}', 'email': f'user{i}@example.com', 'balance': random.randint(0,10000)}
for i in range(30):
    data[f'order:{i}'] = {'order_id': i, 'user_id': i % 120, 'amount': random.randint(10,500)}
open('/app/data/legacy.json','w').write(json.dumps(data, indent=2))
print(f"Created legacy with {len(data)} keys (old format)")
PY

# Create skeleton go.mod
RUN printf 'module sharding\n\ngo 1.22\n' > /app/go.mod

# Create skeleton main.go (to be implemented by agent in turn1)
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

RUN ls -lh /app/ && cat /app/config.json && cat /app/go.mod
