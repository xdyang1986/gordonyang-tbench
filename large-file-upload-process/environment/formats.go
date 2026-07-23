package main

import "fmt"

var SupportedFormats = []string{"mp4", "mov", "mkv", "webm", "avi", "flv", "mpeg", "mpg", "3gp", "wmv"}

// TODO: Implement magic-byte detection for 10 formats
// Read first 256 bytes streaming (not whole file) via os.Open + io.ReadFull
// Support uppercase extensions via strings.ToLower, multiple dots via filepath.Ext last suffix, no-extension via magic-only
// Magic rules:
// - mp4: byte 4-7 "ftyp" and brand not containing "qt" nor "3gp"
// - mov: ftyp containing "qt" or moov at start
// - mkv: 0x1A 0x45 0xDF 0xA3
// - webm: same EBML + "webm" in first 64 bytes
// - avi: "RIFF" at 0 and "AVI " at 8
// - flv: "FLV" at 0
// - mpeg/mpg: 0x00 0x00 0x01 + 0xBA/B3 or 0x47 sync
// - 3gp: ftyp + "3gp" brand
// - wmv: 0x30 0x26 0xB2 0x75 0x8E 0x66 0xCF 0x11
// Policy: magic first, if matches return format VALID, else if ext supported but magic mismatch -> INVALID magic mismatch, else unsupported
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
