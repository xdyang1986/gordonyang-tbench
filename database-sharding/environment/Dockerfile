FROM public.ecr.aws/docker/library/golang:1.22-bookworm

ENV GOTOOLCHAIN=local
ENV GOFLAGS=-mod=mod
ENV GOCACHE=/tmp/codimango/gocache
ENV GOPATH=/tmp/codimango/gopath

WORKDIR /app
RUN mkdir -p /app/data

# Create initial config.json with 4 shards
RUN printf '{\n  "shard_count": 4,\n  "shards": [\n    {"id": 0, "path": "/app/data/shard_0.json"},\n    {"id": 1, "path": "/app/data/shard_1.json"},\n    {"id": 2, "path": "/app/data/shard_2.json"},\n    {"id": 3, "path": "/app/data/shard_3.json"}\n  ]\n}\n' > /app/config.json

# Create empty shard files
RUN echo '{}' > /app/data/shard_0.json && echo '{}' > /app/data/shard_1.json && echo '{}' > /app/data/shard_2.json && echo '{}' > /app/data/shard_3.json

# Create legacy data file with historical data (simulates old single DB dump)
RUN python3 << 'PY'
import json, random
data = {}
for i in range(120):
    data[f'user:{i}'] = {'id': i, 'name': f'User {i}', 'email': f'user{i}@example.com', 'balance': random.randint(0,10000)}
for i in range(30):
    data[f'order:{i}'] = {'order_id': i, 'user_id': i % 120, 'amount': random.randint(10,500)}
open('/app/data/legacy.json','w').write(json.dumps(data, indent=2))
print(f"Created legacy with {len(data)} keys")
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
