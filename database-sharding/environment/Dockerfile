FROM public.ecr.aws/docker/library/golang:1.22-bookworm

ENV GOTOOLCHAIN=local
ENV GOFLAGS=-mod=mod
ENV GOCACHE=/tmp/codimango/gocache
ENV GOPATH=/tmp/codimango/gopath

WORKDIR /app
RUN mkdir -p /app/data

# Install pytest
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/* && \
    pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5

# Create initial config with 4 shards, weighted
RUN printf '{\n  "shard_count": 4,\n  "shards": [\n    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},\n    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},\n    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},\n    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}\n  ]\n}\n' > /app/config.json

# Create empty shard files in Turn1 old format {"data":...,"checksum":...} without shard_id/version, plus empty ops.log
# This matches instruction.md that says agent will find old format. Turn1 proxy writes old format; Turn2 migration upgrades to versioned.
RUN python3 << 'PY'
import json, hashlib, random
def write_shard_old(p,d):
    cs=hashlib.md5(json.dumps(d, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    open(p,'w').write(json.dumps({"data":d,"checksum":cs}, indent=2))
for i in range(4):
    write_shard_old(f'/app/data/shard_{i}.json', {})
open('/app/data/ops.log','w').write('')
print("Created empty old-format shards (data+checksum) and empty ops.log")

# Legacy data old flat format (120 users + 30 orders) plus some global keys
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

# Pre-build Turn-1 proxy into /app at image-build time – entire point of single-turn collapse
# Turn1 is non-discriminating; grading is migration only. Build it to prove it compiles, then clean up.
# Uses base64 file to avoid needing steps/ tree (which will be deleted)
COPY environment/main_go_b64.txt /tmp/main_go_b64.txt
RUN printf 'module sharding\n\ngo 1.22\n' > /app/go.mod && \
    base64 -d /tmp/main_go_b64.txt > /app/main.go && \
    echo "Turn1 proxy pre-built" && \
    ls -lh /app/main.go && \
    go build -o /tmp/proxy_turn1 /app && \
    /tmp/proxy_turn1 get-shard-id foo && \
    /tmp/proxy_turn1 --help 2>&1 | head -20 && \
    rm /tmp/main_go_b64.txt /tmp/proxy_turn1 && \
    ls -lh /app/

RUN ls -lh /app/ && ls -lh /app/data && cat /app/config.json
