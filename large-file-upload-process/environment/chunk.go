package main

import "fmt"

// TODO: Parse human-readable sizes: support K/KB/M/MB/G/GB/B suffix, case-insensitive, optional spaces like "8 MB"
// Multiply: K=1024, M=1024*1024, G=1024*1024*1024, reject 0, negative, >1GB, error must contain "invalid chunk size"
// CalculateTotalChunks: ceil(fileSize/chunkSize) with int64, handle 0 size -> 0
// ParseParallel: int 1-32, error "invalid parallel" if out of range
// ParseRetries: int 0-10, error "invalid retries"
// ParseChecksumAlgo: must be sha256|md5|both, error "invalid checksum algo"

func ParseChunkSize(s string) (int64, error) {
	return 0, fmt.Errorf("not implemented")
}

func CalculateTotalChunks(fileSize int64, chunkSize int64) int64 {
	return 0
}

func GetChunkSizeForIndex(fileSize int64, chunkSize int64, index int64, totalChunks int64) int64 {
	return 0
}

func ParseParallel(s string) (int, error) {
	return 0, fmt.Errorf("not implemented")
}

func ParseRetries(s string) (int, error) {
	return 0, fmt.Errorf("not implemented")
}

func ParseChecksumAlgo(s string) (string, error) {
	return "", fmt.Errorf("not implemented")
}
