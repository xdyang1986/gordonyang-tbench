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
	Index    int64  `json:"index"`
	Offset   int64  `json:"offset"`
	Size     int64  `json:"size"`
	Checksum string `json:"checksum"`
	Uploaded bool   `json:"uploaded"`
	Path     string `json:"path"`
}

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

func GenerateSessionID() string {
	b := make([]byte, 16)
	_, err := rand.Read(b)
	if err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}

func CreateManifest(sourceFile string, sourceSize int64, chunkSize int64, destDir string, fileChecksum string) (*Manifest, error) {
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
			size = sourceSize - offset
		}
		chunks[i] = ChunkInfo{
			Index:    i,
			Offset:   offset,
			Size:     size,
			Checksum: "",
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

	// Ensure file sync? For atomicity, rename after write
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

	return &m, nil
}

func VerifyChunkFile(destDir string, chunk ChunkInfo) (bool, error) {
	chunkPath := filepath.Join(destDir, chunk.Path)

	info, err := os.Stat(chunkPath)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}

	if info.Size() != chunk.Size {
		return false, nil
	}

	if chunk.Checksum != "" {
		actual, err := ComputeFileSHA256(chunkPath)
		if err != nil {
			return false, err
		}
		if actual != chunk.Checksum {
			return false, nil
		}
	}

	return true, nil
}
