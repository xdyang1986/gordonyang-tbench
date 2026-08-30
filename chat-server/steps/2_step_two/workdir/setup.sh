#!/bin/bash
# CHAT-003: prevent step-1 verifier artifacts leakage into step-2 agent container
rm -rf /tests/ 2>/dev/null || true
rm -rf /logs/verifier/ 2>/dev/null || true
rm -rf /app/data/*.corrupt.* 2>/dev/null || true
rm -f /app/data/global.lock 2>/dev/null || true
rm -rf /tmp/codimango/ 2>/dev/null || true
mkdir -p /tmp/codimango
mkdir -p /app/data
echo "CHAT-003 cleanup done"
