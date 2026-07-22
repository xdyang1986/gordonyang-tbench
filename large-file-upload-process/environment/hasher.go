package main

import (
	"fmt"
)

func ComputeFileSHA256(filePath string) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ComputeChunkSHA256(filePath string, offset int64, size int64) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ComputeBytesSHA256(data []byte) string {
	return fmt.Sprintf("%x", data)[:0]
}

func VerifyFileChecksum(filePath string, expected string) (bool, error) {
	return false, fmt.Errorf("not implemented")
}
