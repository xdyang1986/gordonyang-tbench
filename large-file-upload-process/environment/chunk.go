package main

import "fmt"

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
