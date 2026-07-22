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

// ChunkInfo tracks single chunk upload
type ChunkInfo struct {
	Index    int64  `json:"index"`
	Offset   int64  `json:"offset"`
	Size     int64  `json:"size"`
	Checksum string `json:"checksum"`
	Uploaded bool   `json:"uploaded"`
	Path     string `json:"path"` // relative path to chunk file in dest
}

// Manifest tracks entire upload session
type Manifest struct {
	SessionID    string      `json:"session_id"`
	SourceFile   string      `json:"source_file"`
	SourceSize   int64       `json:"source_size"`
	ChunkSize    int64       `json:"chunk_size"`
	TotalChunks  int64       `json:"total_chunks"`
	FileChecksum string      `json:"file_checksum"`
	Chunks       []ChunkInfo `json:"chunks"`
	CreatedAt    string      `json:"created_at"`
	UpdatedAt    string      `json:"updated_at"`
	DestDir      string      `json:"dest_dir"`
}

// GenerateSessionID generates unique session ID
func GenerateSessionID() string {
	// TODO: generate uuid-like id (use crypto/rand)
	b := make([]byte, 16)
	_, err := rand.Read(b)
	if err != nil {
		// fallback
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}

// CreateManifest creates new manifest for file upload
func CreateManifest(sourceFile string, sourceSize int64, chunkSize int64, destDir string, fileChecksum string) (*Manifest, error) {
	// TODO: implement
	// - Generate session_id
	// - Calculate total chunks
	// - Create chunks slice with offsets, sizes, paths
	// - Set timestamps RFC3339
	// - SourceFile absolute? Use given path

	totalChunks := CalculateTotalChunks(sourceSize, chunkSize)

	m := &Manifest{
		SessionID:    GenerateSessionID(),
		SourceFile:   sourceFile,
		SourceSize:   sourceSize,
		ChunkSize:    chunkSize,
		TotalChunks:  totalChunks,
		FileChecksum: fileChecksum,
		CreatedAt:    time.Now().Format(time.RFC3339),
		UpdatedAt:    time.Now().Format(time.RFC3339),
		DestDir:      destDir,
	}

	chunks := make([]ChunkInfo, totalChunks)
	for i := int64(0); i < totalChunks; i++ {
		offset := i * chunkSize
		size := chunkSize
		if i == totalChunks-1 {
			// Last chunk
			size = sourceSize - offset
		}
		chunks[i] = ChunkInfo{
			Index:    i,
			Offset:   offset,
			Size:     size,
			Checksum: "", // Will be filled during upload
			Uploaded: false,
			Path:     fmt.Sprintf("chunks/chunk_%06d", i),
		}
	}
	m.Chunks = chunks

	return m, nil
}

// SaveManifest saves manifest to file atomically (temp file + rename)
func SaveManifest(manifest *Manifest, path string) error {
	// TODO: implement atomic write
	// - Update UpdatedAt timestamp
	// - Marshal JSON with indent
	// - Write to temp file in same dir (path + ".tmp")
	// - Sync and rename to final path
	// - Ensure directory exists

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

	// Sync dir? Simplified rename
	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("rename failed: %w", err)
	}

	return nil
}

// LoadManifest loads manifest from JSON file
func LoadManifest(path string) (*Manifest, error) {
	// TODO: implement load with validation
	// - Read file (manifest is small, ok to read all)
	// - Unmarshal JSON
	// - Validate required fields
	// - Return error if corrupted JSON (caller should handle as WARN and start fresh)

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read manifest failed: %w", err)
	}

	var m Manifest
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, fmt.Errorf("corrupted manifest JSON: %w", err)
	}

	// Basic validation
	if m.SourceFile == "" || m.TotalChunks < 0 {
		return nil, fmt.Errorf("corrupted manifest: invalid fields")
	}

	return &m, nil
}

// VerifyChunkFile verifies a chunk file on disk matches expected checksum
func VerifyChunkFile(destDir string, chunk ChunkInfo) (bool, error) {
	chunkPath := filepath.Join(destDir, chunk.Path)

	info, err := os.Stat(chunkPath)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}

	// Size check
	if info.Size() != chunk.Size {
		return false, nil
	}

	// If manifest has no checksum yet (not yet uploaded in this session), size match is enough for existence?
	// But if checksum present, verify
	if chunk.Checksum != "" {
		actual, err := ComputeFileSHA256(chunkPath)
		if err != nil {
			return false, err
		}
		if actual != chunk.Checksum {
			return false, nil // corrupted
		}
	}

	return true, nil
}
