package main

import "fmt"

var SupportedFormats = []string{"mp4", "mov", "mkv", "webm", "avi", "flv", "mpeg", "mpg", "3gp", "wmv"}

type FileInfo struct {
	File        string `json:"file"`
	Size        int64  `json:"size"`
	Format      string `json:"format"`
	Valid       bool   `json:"valid"`
	Checksum    string `json:"checksum"`
	ChecksumMD5 string `json:"checksum_md5,omitempty"`
	ChunkInfo   struct {
		ChunkSize   int64 `json:"chunk_size"`
		TotalChunks int64 `json:"total_chunks"`
	} `json:"chunk_info"`
}

func DetectFormat(filePath string) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func ValidateVideoFile(filePath string) (string, error) {
	return "", fmt.Errorf("not implemented")
}

func GetFileInfo(filePath string, chunkSize int64, checksumAlgo string) (*FileInfo, error) {
	return nil, fmt.Errorf("not implemented")
}

func isSupportedFormat(f string) bool {
	return false
}
