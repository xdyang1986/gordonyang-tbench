package main

import (
	"fmt"
	"strconv"
	"strings"
)

func ParseChunkSize(s string) (int64, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, fmt.Errorf("invalid chunk size: empty")
	}

	upper := strings.ToUpper(s)

	var multiplier int64 = 1
	numStr := upper

	// Order matters: check GB before G etc.
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
		multiplier = 1
		numStr = strings.TrimSuffix(upper, "B")
	}

	numStr = strings.TrimSpace(numStr)
	if numStr == "" {
		return 0, fmt.Errorf("invalid chunk size: %s", s)
	}

	// Disallow float, extra suffixes like B after M already handled, but reject if leftover letters
	// Ensure numStr is pure integer digits
	for _, ch := range numStr {
		if ch < '0' || ch > '9' {
			// Allow leading - for negative check but will be caught as <=0
			if ch == '-' {
				continue
			}
			return 0, fmt.Errorf("invalid chunk size: %s", s)
		}
	}

	val, err := strconv.ParseInt(numStr, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid chunk size: %s", s)
	}

	size := val * multiplier

	if size <= 0 {
		return 0, fmt.Errorf("invalid chunk size: must be positive, got %s", s)
	}
	if size > 1024*1024*1024 {
		return 0, fmt.Errorf("invalid chunk size: exceeds 1GB max, got %s", s)
	}

	return size, nil
}

func CalculateTotalChunks(fileSize int64, chunkSize int64) int64 {
	if fileSize == 0 {
		return 0
	}
	if chunkSize <= 0 {
		return 0
	}
	return (fileSize + chunkSize - 1) / chunkSize
}

func GetChunkSizeForIndex(fileSize int64, chunkSize int64, index int64, totalChunks int64) int64 {
	if index == totalChunks-1 {
		remaining := fileSize - index*chunkSize
		if remaining > 0 {
			return remaining
		}
	}
	return chunkSize
}
