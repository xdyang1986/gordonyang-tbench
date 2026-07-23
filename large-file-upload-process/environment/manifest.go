package main

import "fmt"

// TODO: Manifest tracks upload progress with thread-safety
// CreateManifest: generate session_id via crypto/rand, calculate total chunks, create chunks slice with offset/size/path
// SaveManifest: update UpdatedAt RFC3339, atomic write via temp+rename, ensure dir exists
// LoadManifest: read file (small), unmarshal JSON, validate fields, error if corrupted JSON -> caller warns and starts fresh
// VerifyChunkFile: check file exists and size matches, then streaming verification: read chunk file in 1MB buffer, decrypt via XOR if encryptKey present (key cycling with offset), compute SHA256 and/or MD5 and compare to manifest's stored checksums (original unencrypted)
// Must handle both algos: sha256 field checksum len 64, md5 32, both stores both
type ChunkInfo struct {
	Index       int64  `json:"index"`
	Offset      int64  `json:"offset"`
	Size        int64  `json:"size"`
	Checksum    string `json:"checksum"`
	ChecksumMD5 string `json:"checksum_md5,omitempty"`
	Uploaded    bool   `json:"uploaded"`
	Path        string `json:"path"`
}

type Manifest struct {
	SessionID       string      `json:"session_id"`
	SourceFile      string      `json:"source_file"`
	SourceSize      int64       `json:"source_size"`
	ChunkSize       int64       `json:"chunk_size"`
	TotalChunks     int64       `json:"total_chunks"`
	FileChecksum    string      `json:"file_checksum"`
	FileChecksumMD5 string      `json:"file_checksum_md5,omitempty"`
	ChecksumAlgo    string      `json:"checksum_algo"`
	Chunks          []ChunkInfo `json:"chunks"`
	CreatedAt       string      `json:"created_at"`
	UpdatedAt       string      `json:"updated_at"`
	DestDir         string      `json:"dest_dir"`
	Parallel        int         `json:"parallel"`
	EncryptKey      string      `json:"encrypt_key"`
}

func GenerateSessionID() string {
	return ""
}

func CreateManifest(sourceFile string, sourceSize int64, chunkSize int64, destDir string, fileChecksum string, fileChecksumMD5 string, checksumAlgo string, parallel int, encryptKey string) (*Manifest, error) {
	return nil, fmt.Errorf("not implemented")
}

func SaveManifest(manifest *Manifest, path string) error {
	return fmt.Errorf("not implemented")
}

func LoadManifest(path string) (*Manifest, error) {
	return nil, fmt.Errorf("not implemented")
}

func VerifyChunkFile(destDir string, chunk ChunkInfo, encryptKey string, checksumAlgo string) (bool, error) {
	return false, fmt.Errorf("not implemented")
}
