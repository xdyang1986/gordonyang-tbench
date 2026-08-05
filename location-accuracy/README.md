# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with accuracy improvements.

## Overview
- **Step 1**: Build a basic vehicle location service `locationctl` in Go that tracks vehicle positions with persistence, handles out-of-order GPS pings, validates inputs, and supports geospatial `near` queries using Haversine.
- **Step 2**: Improve accuracy to avoid inaccurate pickup/dropoff. Adds GPS outlier/teleport filtering, low-accuracy rejection, stale detection, history smoothing, motion-based prediction, road-snapping via `roads.json`, confidence scoring, and `validate-pickup` / `validate-dropoff` commands.

## Structure
- `environment/Dockerfile` - installs Go, creates /app/src and sample roads file
- `steps/1_step_one/` - basic tracking
- `steps/2_step_two/` - accuracy improvements, inherits prior session

## How to run tests locally
```bash
# Step1
cp steps/1_step_one/solution/solve.sh /tmp/s1.sh && bash /tmp/s1.sh
pytest steps/1_step_one/tests/test_outputs.py -v

# Step2
bash steps/2_step_two/solution/solve.sh
pytest steps/2_step_two/tests/test_outputs.py -v
```
