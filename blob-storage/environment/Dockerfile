FROM public.ecr.aws/docker/library/golang:1.22-bookworm

WORKDIR /app

# Install Python3 and dependencies for tests
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv and Python test dependencies
RUN curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Pre-cache pytest and requests for faster test runs (use pip with break-system-packages for Debian)
RUN pip3 install --break-system-packages pytest==8.4.1 requests==2.32.3 pytest-json-ctrf==0.3.5 2>&1 || pip install --break-system-packages pytest==8.4.1 requests==2.32.3 pytest-json-ctrf==0.3.5 2>&1 || /root/.local/bin/uv pip install --system --break-system-packages pytest==8.4.1 requests==2.32.3 pytest-json-ctrf==0.3.5

# Copy go.mod if exists (optional, will be overwritten by agent)
# Ensure /app exists and is writable
RUN mkdir -p /app /tmp/blob-data && chmod -R 777 /app /tmp/blob-data

# Default command
CMD ["/bin/bash"]
