package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
)

func ComputeFileSHA256(filePath string) (string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	hash := sha256.New()
	buf := make([]byte, 1024*1024) // 1MB buffer - constant memory regardless of file size
	if _, err := io.CopyBuffer(hash, f, buf); err != nil {
		return "", fmt.Errorf("hash copy failed: %w", err)
	}

	return hex.EncodeToString(hash.Sum(nil)), nil
}

func ComputeChunkSHA256(filePath string, offset int64, size int64) (string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	_, err = f.Seek(offset, io.SeekStart)
	if err != nil {
		return "", fmt.Errorf("seek failed: %w", err)
	}

	hash := sha256.New()
	limited := io.LimitReader(f, size)
	buf := make([]byte, 1024*1024)
	if _, err := io.CopyBuffer(hash, limited, buf); err != nil {
		return "", fmt.Errorf("chunk hash copy failed: %w", err)
	}

	return hex.EncodeToString(hash.Sum(nil)), nil
}

func ComputeBytesSHA256(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func VerifyFileChecksum(filePath string, expected string) (bool, error) {
	actual, err := ComputeFileSHA256(filePath)
	if err != nil {
		return false, err
	}
	return actual == expected, nil
}
