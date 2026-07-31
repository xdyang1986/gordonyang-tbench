FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pyyaml==6.0.2 \
    watchdog==6.0.0 \
    pytest==8.4.1

# Create required directories
RUN mkdir -p /app/data/incoming \
             /app/state \
             /app/metrics \
             /app/logs

# Create initial config.yaml (batch mode default)
RUN printf 'mode: batch\nincoming_dir: /app/data/incoming\nwarehouse_db: /app/warehouse.db\ncheckpoint_dir: /app/state\nmetrics_path: /app/metrics/freshness.json\nbatch:\n  dedup_strategy: latest_event_time\nstream:\n  poll_interval_ms: 200\n  freshness_sla_sec: 2\n  offsets_file: /app/state/stream_offsets.json\n' > /app/config.yaml

# Ensure empty placeholder for readability
RUN touch /app/README.md
