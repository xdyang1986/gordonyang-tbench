#!/bin/bash
set -e

echo "Applying reference solution for large file upload processor..."

# Determine where solution Go files are
# In Harbor oracle run, solution files are in /solution
# Locally they might be in ./solution or /app/solution
SRC_DIR=""
if [ -f "/solution/main.go" ]; then
  SRC_DIR="/solution"
elif [ -f "./solution/main.go" ]; then
  SRC_DIR="./solution"
elif [ -f "solution/main.go" ]; then
  SRC_DIR="solution"
elif [ -f "/app/solution/main.go" ]; then
  SRC_DIR="/app/solution"
else
  # Fallback: we are already in solution dir? Check current dir for main.go
  SRC_DIR="."
fi

echo "Solution source dir: $SRC_DIR"
echo "Current workdir: $(pwd)"
ls -la $SRC_DIR/*.go 2>&1 | head -20

# Copy Go files to /app (working directory)
# Working dir for agent is /app, so ensure we copy there
TARGET_DIR="/app"
if [ "$(pwd)" != "/app" ]; then
  # If we're running from task root, target is /app if exists else current dir's ../?
  if [ -d "/app" ]; then
    TARGET_DIR="/app"
  else
    TARGET_DIR="."
  fi
fi

echo "Target dir: $TARGET_DIR"

# Copy all Go files
for f in main.go formats.go chunk.go hasher.go manifest.go uploader.go go.mod; do
  if [ -f "$SRC_DIR/$f" ]; then
    echo "Copying $f to $TARGET_DIR/"
    cp "$SRC_DIR/$f" "$TARGET_DIR/$f"
  fi
done

# Verify files copied
echo "Files in target:"
ls -la $TARGET_DIR/*.go $TARGET_DIR/go.mod

cd $TARGET_DIR
echo "Running go mod tidy..."
go mod tidy

echo "Building uploader binary..."
go build -o /tmp/uploader . || go build -o ./uploader .
if [ -f "/tmp/uploader" ]; then
  echo "Binary built at /tmp/uploader"
  /tmp/uploader help 2>&1 || true
elif [ -f "./uploader" ]; then
  echo "Binary built at ./uploader"
  ./uploader help 2>&1 || true
else
  echo "Trying go run..."
  go run . help 2>&1 || true
fi

echo "Solution applied successfully"
