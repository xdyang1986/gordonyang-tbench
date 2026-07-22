package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
)

// ComputeFileSHA256 computes SHA256 of file using streaming (must NOT load whole file into memory)
// Use 1MB buffer or similar
func ComputeFileSHA256(filePath string) (string, error) {
	// TODO: implement streaming SHA256
	// Requirements:
	// - Open file
	// - Create sha256 hash
	// - Stream via io.Copy or manual buffer (e.g., 1MB buffer)
	// - Return hex encoded string
	// - Handle large files (100s of GB) without OOM
	// - Use int64 offsets, not load whole file

	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	// TODO: streaming implementation
	// Hint: hash := sha256.New(); _, err = io.Copy(hash, f); then hex.EncodeToString
	// Do NOT use os.ReadFile or io.ReadAll!

	hash := sha256.New()
	// Placeholder buffer streaming - needs real streaming
	buf := make([]byte, 1024*1024) // 1MB buffer
	if _, err := io.CopyBuffer(hash, f, buf); err != nil {
		return "", fmt.Errorf("hash copy failed: %w", err)
	}

	return hex.EncodeToString(hash.Sum(nil)), nil
}

// ComputeChunkSHA256 computes SHA256 for a chunk defined by offset and size
// Must use Seek and streaming read, not load whole file
func ComputeChunkSHA256(filePath string, offset int64, size int64) (string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("open failed: %w", err)
	}
	defer f.Close()

	// TODO: Seek to offset (int64)
	// Use io.SeekStart
	_, err = f.Seek(offset, io.SeekStart)
	if err != nil {
		return "", fmt.Errorf("seek failed: %w", err)
	}

	hash := sha256.New()
	// TODO: limit reading to size bytes
	// Use io.LimitReader or manual loop with bounded buffer
	// Must not read beyond size

	limited := io.LimitReader(f, size)
	buf := make([]byte, 1024*1024) // 1MB buffer
	if _, err := io.CopyBuffer(hash, limited, buf); err != nil {
		return "", fmt.Errorf("chunk hash copy failed: %w", err)
	}

	return hex.EncodeToString(hash.Sum(nil)), nil
}

// ComputeBytesSHA256 computes SHA256 for byte slice (for chunk data in memory, small)
func ComputeBytesSHA256(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

// VerifyFileChecksum verifies file SHA256 matches expected
func VerifyFileChecksum(filePath string, expected string) (bool, error) {
	actual, err := ComputeFileSHA256(filePath)
	if err != nil {
		return false, err
	}
	return actual == expected, nil
}
