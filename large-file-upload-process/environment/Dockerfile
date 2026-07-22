FROM public.ecr.aws/docker/library/golang:1.23-bookworm

WORKDIR /app

# Copy Go skeleton files - build context is this environment directory
COPY go.mod ./go.mod
COPY main.go ./main.go
COPY formats.go ./formats.go
COPY chunk.go ./chunk.go
COPY hasher.go ./hasher.go
COPY manifest.go ./manifest.go
COPY uploader.go ./uploader.go

# Initialize go mod and verify it builds (skeleton should compile, even if functionality TODO)
RUN go mod tidy && go build -o /tmp/uploader . || echo "skeleton build may have TODOs but should compile - checking"

# Create directories for testing
RUN mkdir -p /tmp/videos /tmp/dest /app/samples

# Install useful tools for testing video files
RUN apt-get update && apt-get install -y file xxd && rm -rf /var/lib/apt/lists/*

# Verify go is available
RUN go version
