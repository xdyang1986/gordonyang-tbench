package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// Supported video formats for YouTube-like platform
var SupportedFormats = []string{"mp4", "mov", "mkv", "webm", "avi", "flv", "mpeg", "mpg", "3gp", "wmv"}

// Magic byte signatures
func isMP4(magic []byte) bool {
	// TODO: mp4 has ftyp at offset 4
	// Check len and ftyp box
	return false
}

func isMOV(magic []byte) bool {
	// TODO: mov has ftyp qt or moov
	return false
}

func isMKV(magic []byte) bool {
	// TODO: mkv EBML header 0x1A45DFA3
	return false
}

func isWebM(magic []byte, fullHeader []byte) bool {
	// TODO: same EBML + webm string
	return false
}

func isAVI(magic []byte) bool {
	// TODO: RIFF....AVI
	return false
}

func isFLV(magic []byte) bool {
	// TODO: FLV header
	return false
}

func isMPEG(magic []byte) bool {
	// TODO: 00 00 01 B3 or BA, or 0x47 TS sync
	return false
}

func is3GP(magic []byte) bool {
	// TODO: ftyp 3gp
	return false
}

func isWMV(magic []byte) bool {
	// TODO: 30 26 B2 75 8E 66 CF 11
	return false
}

// DetectFormat detects video format by magic bytes and extension
// Must check both and return lowercase format string
func DetectFormat(filePath string) (string, error) {
	// TODO: implement full detection
	// Steps:
	// 1. Open file, read first 64-256 bytes (don't load whole file)
	// 2. Check magic bytes for each format
	// 3. Consider extension as hint but verify magic
	// 4. Handle empty/small files
	// 5. Return format lowercase

	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("cannot open file: %w", err)
	}
	defer f.Close()

	// Read first 256 bytes for detection (enough for EBML + ftyp)
	buf := make([]byte, 256)
	n, err := io.ReadFull(f, buf)
	if err != nil && err != io.ErrUnexpectedEOF && err != io.EOF {
		return "", fmt.Errorf("cannot read header: %w", err)
	}
	if n < 16 {
		return "", fmt.Errorf("file too small")
	}
	magic := buf[:n]

	ext := strings.ToLower(strings.TrimPrefix(strings.ToLower(filepath.Ext(filePath)), "."))
	// Normalize mpg -> mpeg for check, but keep original handling
	normalizedExt := ext
	if ext == "mpg" {
		normalizedExt = "mpeg"
	}

	// TODO: Implement proper magic detection
	// Current stub only checks extension existence, not magic - must fix!
	// Hint: check each isXXX function

	// Check magic first (more reliable than extension)
	_ = magic
	_ = normalizedExt

	// Placeholder logic - needs real implementation
	for _, fmtStr := range SupportedFormats {
		if ext == fmtStr {
			return fmtStr, nil
		}
	}

	// If extension not supported, try detect by magic alone
	// TODO: implement magic-only detection

	return "", fmt.Errorf("unsupported format %s", ext)
}

// ValidateVideoFile validates that file is supported video format
// Checks both extension and magic bytes
func ValidateVideoFile(filePath string) (string, error) {
	// TODO: implement validation
	// - Stat file first, ensure exists and not empty
	// - Call DetectFormat
	// - If empty file, return INVALID
	// - Ensure format is in supported list
	// - For extra robustness: if extension says mp4 but magic says avi, report magic mismatch

	info, err := os.Stat(filePath)
	if err != nil {
		return "", fmt.Errorf("file not found: %s", filePath)
	}
	if info.Size() == 0 {
		return "", fmt.Errorf("empty file")
	}

	format, err := DetectFormat(filePath)
	if err != nil {
		return "", err
	}

	// TODO: add additional magic verification against claimed format
	// Example: if file says .mp4 but magic is AVI, reject

	return format, nil
}

// GetFileInfo returns file metadata including format, size, checksum
type FileInfo struct {
	File      string `json:"file"`
	Size      int64  `json:"size"`
	Format    string `json:"format"`
	Valid     bool   `json:"valid"`
	Checksum  string `json:"checksum"`
	ChunkInfo struct {
		ChunkSize   int64 `json:"chunk_size"`
		TotalChunks int64 `json:"total_chunks"`
	} `json:"chunk_info"`
}

func GetFileInfo(filePath string, chunkSize int64) (*FileInfo, error) {
	// TODO: implement
	// - Stat file for size (int64)
	// - Validate format (DetectFormat)
	// - Compute streaming SHA256 (use ComputeFileSHA256)
	// - Calculate total chunks
	info := &FileInfo{}
	info.File = filePath
	info.ChunkInfo.ChunkSize = chunkSize

	stat, err := os.Stat(filePath)
	if err != nil {
		return nil, fmt.Errorf("stat failed: %w", err)
	}
	info.Size = stat.Size()
	info.ChunkInfo.TotalChunks = CalculateTotalChunks(stat.Size(), chunkSize)

	format, err := DetectFormat(filePath)
	if err != nil {
		info.Valid = false
		info.Format = "unknown"
		// Still compute checksum if possible?
		checksum, _ := ComputeFileSHA256(filePath)
		info.Checksum = checksum
		return info, nil
	}
	info.Valid = true
	info.Format = format

	checksum, err := ComputeFileSHA256(filePath)
	if err != nil {
		return nil, fmt.Errorf("checksum failed: %w", err)
	}
	info.Checksum = checksum

	return info, nil
}

// Helper to check if format supported
func isSupportedFormat(f string) bool {
	f = strings.ToLower(f)
	for _, s := range SupportedFormats {
		if s == f {
			return true
		}
		if s == "mpeg" && f == "mpg" {
			return true
		}
	}
	return false
}
