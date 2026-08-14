#!/bin/bash
# Top-level test.sh for multi-turn task - delegates to step tests
# Harbor multi-turn runner uses per-step test.sh, this is for structure validation only
mkdir -p /logs/verifier
python3 -m pytest --collect-only /data/repos/workspace/gordonyang-tbench/computer-resource-optimization-v2/steps/1_step_one/tests/test_outputs.py -q 2>&1 | head -n 20
echo 1 > /logs/verifier/reward.txt
exit 0
