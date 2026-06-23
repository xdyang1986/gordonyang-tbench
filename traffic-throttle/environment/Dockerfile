# Go toolchain from the ECR mirror (avoids docker.io pull-rate limits on cloud builds).
# The bookworm image already bundles gcc, which the race detector needs at test time.
FROM public.ecr.aws/docker/library/golang:1.23.4-bookworm

# Keep module/toolchain resolution deterministic and offline-friendly.
ENV GOTOOLCHAIN=local \
    GOFLAGS=-mod=mod

WORKDIR /app
