package main

import (
	"fmt"
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
	return ""
}

func CreateManifest(sourceFile string, sourceSize int64, chunkSize int64, destDir string, fileChecksum string) (*Manifest, error) {
	return nil, fmt.Errorf("not implemented")
}

func SaveManifest(manifest *Manifest, path string) error {
	return fmt.Errorf("not implemented")
}

func LoadManifest(path string) (*Manifest, error) {
	return nil, fmt.Errorf("not implemented")
}

func VerifyChunkFile(destDir string, chunk ChunkInfo) (bool, error) {
	return false, fmt.Errorf("not implemented")
}
