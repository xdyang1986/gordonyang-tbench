package main

import (
	"fmt"
	"strconv"
	"strings"
)

// ParseChunkSize parses human-readable chunk sizes like "8M", "4MB", "1G", "512K"
// Returns size in bytes as int64
// Supports: K, M, G suffix (case-insensitive), optional B suffix, plain bytes number
// Rejects: zero, negative, >1GB, non-numeric
func ParseChunkSize(s string) (int64, error) {
	// TODO: implement proper parsing
	// Requirements:
	// - Trim spaces
	// - Case-insensitive
	// - Support K/KB, M/MB, G/GB, plain number
	// - Multiply: K=1024, M=1024*1024, G=1024*1024*1024
	// - Reject 0, negative, >1GB (1*1024*1024*1024)
	// - Error message must contain "invalid chunk size" for invalid cases
	// - Handle float? No, only integer supported (or allow integer before suffix)

	s = strings.TrimSpace(s)
	if s == "" {
		return 0, fmt.Errorf("invalid chunk size: empty")
	}

	// Normalize
	upper := strings.ToUpper(s)

	var multiplier int64 = 1
	numStr := upper

	// Check suffixes in order G, M, K to avoid confusion
	// TODO: handle GB, MB, KB vs G, M, K vs B alone
	// Example: 8M, 8MB, 8m, 8mb, 8192KB, etc.

	if strings.HasSuffix(upper, "GB") {
		multiplier = 1024 * 1024 * 1024
		numStr = strings.TrimSuffix(upper, "GB")
	} else if strings.HasSuffix(upper, "MB") {
		multiplier = 1024 * 1024
		numStr = strings.TrimSuffix(upper, "MB")
	} else if strings.HasSuffix(upper, "KB") {
		multiplier = 1024
		numStr = strings.TrimSuffix(upper, "KB")
	} else if strings.HasSuffix(upper, "G") {
		multiplier = 1024 * 1024 * 1024
		numStr = strings.TrimSuffix(upper, "G")
	} else if strings.HasSuffix(upper, "M") {
		multiplier = 1024 * 1024
		numStr = strings.TrimSuffix(upper, "M")
	} else if strings.HasSuffix(upper, "K") {
		multiplier = 1024
		numStr = strings.TrimSuffix(upper, "K")
	} else if strings.HasSuffix(upper, "B") {
		// Plain bytes with B suffix
		multiplier = 1
		numStr = strings.TrimSuffix(upper, "B")
	}

	numStr = strings.TrimSpace(numStr)
	if numStr == "" {
		return 0, fmt.Errorf("invalid chunk size: %s", s)
	}

	// TODO: parse number, check errors
	val, err := strconv.ParseInt(numStr, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid chunk size: %s", s)
	}

	size := val * multiplier

	// TODO: validate constraints
	if size <= 0 {
		return 0, fmt.Errorf("invalid chunk size: must be positive, got %s", s)
	}
	if size > 1024*1024*1024 {
		return 0, fmt.Errorf("invalid chunk size: exceeds 1GB max, got %s", s)
	}

	return size, nil
}

// CalculateTotalChunks calculates total chunks needed
// Must handle int64 and handle last chunk smaller
func CalculateTotalChunks(fileSize int64, chunkSize int64) int64 {
	// TODO: implement ceil division
	// Must handle fileSize 0? Return 0 or 1? For 0 size, 0 chunks.
	// Use int64 math to avoid overflow for large files (100s of GB)
	if fileSize == 0 {
		return 0
	}
	if chunkSize <= 0 {
		return 0
	}
	// ceil(fileSize / chunkSize) = (fileSize + chunkSize - 1) / chunkSize
	// But careful with overflow: fileSize can be up to ~500GB
	return (fileSize + chunkSize - 1) / chunkSize
}

// GetChunkSizeForIndex returns size for a specific chunk index (last chunk may be smaller)
func GetChunkSizeForIndex(fileSize int64, chunkSize int64, index int64, totalChunks int64) int64 {
	// TODO: implement
	// If last chunk, size = fileSize - (index * chunkSize)
	if index == totalChunks-1 {
		remaining := fileSize - index*chunkSize
		if remaining > 0 {
			return remaining
		}
	}
	return chunkSize
}
