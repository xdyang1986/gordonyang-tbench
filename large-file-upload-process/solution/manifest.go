package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type ChunkInfo struct {
	Index       int64  `json:"index"`
	Offset      int64  `json:"offset"`
	Size        int64  `json:"size"`
	Checksum    string `json:"checksum"`               // SHA256 (always, or MD5 if algo=md5, or SHA256 if both)
	ChecksumMD5 string `json:"checksum_md5,omitempty"` // MD5 if algo=md5 or both
	Uploaded    bool   `json:"uploaded"`
	Path        string `json:"path"`
}

type Manifest struct {
	SessionID       string      `json:"session_id"`
	SourceFile      string      `json:"source_file"`
	SourceSize      int64       `json:"source_size"`
	ChunkSize       int64       `json:"chunk_size"`
	TotalChunks     int64       `json:"total_chunks"`
	FileChecksum    string      `json:"file_checksum"`               // SHA256 or MD5 or SHA256 if both
	FileChecksumMD5 string      `json:"file_checksum_md5,omitempty"` // MD5 if needed
	ChecksumAlgo    string      `json:"checksum_algo"`               // sha256|md5|both
	Chunks          []ChunkInfo `json:"chunks"`
	CreatedAt       string      `json:"created_at"`
	UpdatedAt       string      `json:"updated_at"`
	DestDir         string      `json:"dest_dir"`
	Parallel        int         `json:"parallel"`
	EncryptKey      string      `json:"encrypt_key"`
}

func GenerateSessionID() string {
	b := make([]byte, 16)
	_, err := rand.Read(b)
	if err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}

func CreateManifest(sourceFile string, sourceSize int64, chunkSize int64, destDir string, fileChecksum string, fileChecksumMD5 string, checksumAlgo string, parallel int, encryptKey string) (*Manifest, error) {
	totalChunks := CalculateTotalChunks(sourceSize, chunkSize)

	m := &Manifest{
		SessionID:       GenerateSessionID(),
		SourceFile:      sourceFile,
		SourceSize:      sourceSize,
		ChunkSize:       chunkSize,
		TotalChunks:     totalChunks,
		FileChecksum:    fileChecksum,
		FileChecksumMD5: fileChecksumMD5,
		ChecksumAlgo:    checksumAlgo,
		CreatedAt:       time.Now().Format(time.RFC3339),
		UpdatedAt:       time.Now().Format(time.RFC3339),
		DestDir:         destDir,
		Parallel:        parallel,
		EncryptKey:      encryptKey,
	}

	chunks := make([]ChunkInfo, totalChunks)
	for i := int64(0); i < totalChunks; i++ {
		offset := i * chunkSize
		size := chunkSize
		if i == totalChunks-1 {
			size = sourceSize - offset
		}
		chunks[i] = ChunkInfo{
			Index:    i,
			Offset:   offset,
			Size:     size,
			Uploaded: false,
			Path:     fmt.Sprintf("chunks/chunk_%06d", i),
		}
	}
	m.Chunks = chunks

	return m, nil
}

func SaveManifest(manifest *Manifest, path string) error {
	manifest.UpdatedAt = time.Now().Format(time.RFC3339)

	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("mkdir manifest dir failed: %w", err)
	}

	data, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal failed: %w", err)
	}

	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0644); err != nil {
		return fmt.Errorf("write tmp failed: %w", err)
	}

	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("rename failed: %w", err)
	}

	return nil
}

func LoadManifest(path string) (*Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read manifest failed: %w", err)
	}

	var m Manifest
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, fmt.Errorf("corrupted manifest JSON: %w", err)
	}

	if m.SourceFile == "" || m.TotalChunks < 0 {
		return nil, fmt.Errorf("corrupted manifest: invalid fields")
	}

	// Defaults for older manifests
	if m.ChecksumAlgo == "" {
		m.ChecksumAlgo = "sha256"
	}
	if m.Parallel == 0 {
		m.Parallel = 1
	}

	return &m, nil
}

func VerifyChunkFile(destDir string, chunk ChunkInfo, encryptKey string, checksumAlgo string) (bool, error) {
	chunkPath := filepath.Join(destDir, chunk.Path)

	info, err := os.Stat(chunkPath)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}

	// If encrypted, on-disk size is same as original (XOR doesn't change size)
	if info.Size() != chunk.Size {
		return false, nil
	}

	// Read file, decrypt if needed, then checksum
	data, err := os.ReadFile(chunkPath)
	if err != nil {
		return false, err
	}

	// Decrypt if key present
	if encryptKey != "" {
		data = XorEncryptDecrypt(data, encryptKey)
	}

	// Verify checksums based on algo
	switch checksumAlgo {
	case "sha256":
		if chunk.Checksum == "" {
			return true, nil
		}
		actual := ComputeBytesSHA256(data)
		return actual == chunk.Checksum, nil
	case "md5":
		// In md5 mode, Checksum field holds md5
		if chunk.Checksum == "" && chunk.ChecksumMD5 == "" {
			return true, nil
		}
		expected := chunk.Checksum
		if expected == "" {
			expected = chunk.ChecksumMD5
		}
		actual := ComputeBytesMD5(data)
		return actual == expected, nil
	case "both":
		if chunk.Checksum != "" {
			actual := ComputeBytesSHA256(data)
			if actual != chunk.Checksum {
				return false, nil
			}
		}
		if chunk.ChecksumMD5 != "" {
			actualMD5 := ComputeBytesMD5(data)
			if actualMD5 != chunk.ChecksumMD5 {
				return false, nil
			}
		}
		return true, nil
	default:
		// Default sha256
		if chunk.Checksum == "" {
			return true, nil
		}
		actual := ComputeBytesSHA256(data)
		return actual == chunk.Checksum, nil
	}
}
