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

	// Allow space between number and unit: "8 MB" -> "8MB"
	s = strings.ReplaceAll(s, " ", "")
	upper := strings.ToUpper(s)

	var multiplier int64 = 1
	numStr := upper

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

	for _, ch := range numStr {
		if ch < '0' || ch > '9' {
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
	if size < 1024 {
		return 0, fmt.Errorf("invalid chunk size: must be at least 1KB, got %s", s)
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

func ParseParallel(s string) (int, error) {
	s = strings.TrimSpace(s)
	val, err := strconv.Atoi(s)
	if err != nil {
		return 0, fmt.Errorf("invalid parallel: %s", s)
	}
	if val < 1 || val > 32 {
		return 0, fmt.Errorf("invalid parallel: must be 1-32, got %s", s)
	}
	return val, nil
}

func ParseRetries(s string) (int, error) {
	s = strings.TrimSpace(s)
	val, err := strconv.Atoi(s)
	if err != nil {
		return 0, fmt.Errorf("invalid retries: %s", s)
	}
	if val < 0 || val > 10 {
		return 0, fmt.Errorf("invalid retries: must be 0-10, got %s", s)
	}
	return val, nil
}

func ParseChecksumAlgo(s string) (string, error) {
	s = strings.ToLower(strings.TrimSpace(s))
	switch s {
	case "sha256", "md5", "both":
		return s, nil
	default:
		return "", fmt.Errorf("invalid checksum algo: must be sha256|md5|both, got %s", s)
	}
}
