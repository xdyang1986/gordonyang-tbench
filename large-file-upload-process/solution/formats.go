package main

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

var SupportedFormats = []string{"mp4", "mov", "mkv", "webm", "avi", "flv", "mpeg", "mpg", "3gp", "wmv"}

var mp4Brands = map[string]bool{
	"isom": true, "iso2": true, "iso3": true, "iso4": true, "iso5": true, "iso6": true,
	"avc1": true, "avc3": true, "dash": true, "msnv": true, "m4v ": true, "m4a ": true,
	"mp41": true, "mp42": true, "m4p ": true, "isml": true, "mp71": true,
	"av01": true, "hev1": true, "hvc1": true, "mp21": true,
}

func isMP4(magic []byte) bool {
	if len(magic) < 12 {
		return false
	}
	if string(magic[4:8]) != "ftyp" {
		return false
	}
	brand := string(magic[8:12])
	brandTrim := strings.TrimSpace(brand)
	lowerBrand := strings.ToLower(brand)
	if strings.Contains(lowerBrand, "qt") {
		return false
	}
	if strings.Contains(strings.ToLower(brand), "3gp") || strings.Contains(strings.ToLower(brand), "3g2") || strings.Contains(strings.ToLower(brand), "3ge") {
		return false
	}
	if mp4Brands[brand] || mp4Brands[brandTrim] {
		return true
	}
	if len(magic) >= 20 {
		headerStr := string(magic[0:32])
		if strings.Contains(headerStr, "isom") || strings.Contains(headerStr, "mp42") || strings.Contains(headerStr, "avc1") {
			return true
		}
	}
	return true
}

func isMOV(magic []byte) bool {
	if len(magic) < 12 {
		if len(magic) >= 4 && string(magic[0:4]) == "moov" {
			return true
		}
		return false
	}
	if string(magic[4:8]) == "ftyp" {
		brand := strings.ToLower(string(magic[8:12]))
		if strings.Contains(brand, "qt") {
			return true
		}
	}
	if len(magic) >= 4 && string(magic[0:4]) == "moov" {
		return true
	}
	if bytes.Contains(magic, []byte("ftypqt")) {
		return true
	}
	if len(magic) >= 16 {
		if bytes.Contains(magic[:32], []byte("qt")) && bytes.Contains(magic[:32], []byte("ftyp")) {
			brand := string(magic[8:12])
			if strings.Contains(strings.ToLower(brand), "qt") {
				return true
			}
		}
	}
	return false
}

func isMKV(magic []byte) bool {
	if len(magic) < 4 {
		return false
	}
	return magic[0] == 0x1A && magic[1] == 0x45 && magic[2] == 0xDF && magic[3] == 0xA3
}

func isWebM(magic []byte, fullHeader []byte) bool {
	if !isMKV(magic) {
		return false
	}
	if len(fullHeader) < 4 {
		return false
	}
	lower := bytes.ToLower(fullHeader)
	searchLen := 64
	if len(lower) < searchLen {
		searchLen = len(lower)
	}
	return bytes.Contains(lower[:searchLen], []byte("webm"))
}

func isAVI(magic []byte) bool {
	if len(magic) < 12 {
		return false
	}
	if string(magic[0:4]) != "RIFF" {
		return false
	}
	if string(magic[8:12]) == "AVI " || string(magic[8:11]) == "AVI" {
		return true
	}
	return bytes.Contains(magic[0:12], []byte("AVI"))
}

func isFLV(magic []byte) bool {
	if len(magic) < 3 {
		return false
	}
	return magic[0] == 0x46 && magic[1] == 0x4C && magic[2] == 0x56
}

func isMPEG(magic []byte) bool {
	if len(magic) < 4 {
		return false
	}
	if magic[0] == 0x00 && magic[1] == 0x00 && magic[2] == 0x01 {
		if magic[3] == 0xBA || magic[3] == 0xB3 || magic[3] == 0xB2 || magic[3] == 0xB8 {
			return true
		}
	}
	if magic[0] == 0x47 {
		return true
	}
	if len(magic) >= 4 && magic[0] == 0 && magic[1] == 0 && magic[2] == 1 {
		return true
	}
	return false
}

func is3GP(magic []byte) bool {
	if len(magic) < 12 {
		return false
	}
	if string(magic[4:8]) != "ftyp" {
		return false
	}
	brand := strings.ToLower(string(magic[8:12]))
	if strings.Contains(brand, "3gp") || strings.Contains(brand, "3g2") || strings.Contains(brand, "3ge") {
		return true
	}
	if len(magic) >= 32 {
		lower := strings.ToLower(string(magic[:32]))
		if strings.Contains(lower, "3gp") {
			return true
		}
	}
	return false
}

func isWMV(magic []byte) bool {
	if len(magic) < 16 {
		return false
	}
	asfSig := []byte{0x30, 0x26, 0xB2, 0x75, 0x8E, 0x66, 0xCF, 0x11}
	if bytes.HasPrefix(magic, asfSig) {
		return true
	}
	return false
}

func extractExtension(filePath string) string {
	// Handle multiple dots: use last suffix
	// Handle uppercase: lower case
	// Handle no extension: return ""
	base := filepath.Base(filePath)
	// Find last dot
	ext := filepath.Ext(base)
	ext = strings.ToLower(strings.TrimPrefix(ext, "."))
	return ext
}

func DetectFormat(filePath string) (string, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("cannot open file: %w", err)
	}
	defer f.Close()

	buf := make([]byte, 256)
	n, err := io.ReadFull(f, buf)
	if err != nil && err != io.ErrUnexpectedEOF && err != io.EOF {
		return "", fmt.Errorf("cannot read header: %w", err)
	}
	if n < 16 {
		return "", fmt.Errorf("file too small")
	}
	magic := buf[:n]
	fullHeader := buf[:n]

	ext := extractExtension(filePath)
	normalizedExt := ext
	if ext == "mpg" {
		normalizedExt = "mpeg"
	}

	// Magic first
	if isWMV(magic) {
		return "wmv", nil
	}
	if isFLV(magic) {
		return "flv", nil
	}
	if isAVI(magic) {
		return "avi", nil
	}
	if isMKV(magic) {
		if isWebM(magic, fullHeader) {
			return "webm", nil
		}
		return "mkv", nil
	}
	if is3GP(magic) {
		return "3gp", nil
	}
	if isMOV(magic) {
		return "mov", nil
	}
	if isMP4(magic) {
		return "mp4", nil
	}
	if isMPEG(magic) {
		if ext == "mpg" {
			return "mpg", nil
		}
		return "mpeg", nil
	}

	// Magic didn't match any supported format
	if !isSupportedFormat(ext) && !isSupportedFormat(normalizedExt) {
		if ext == "" {
			return "", fmt.Errorf("unsupported format unknown (no extension and magic not recognized)")
		}
		return "", fmt.Errorf("unsupported format %s", ext)
	}

	// Extension supported but magic mismatch -> magic mismatch error (required string)
	if isSupportedFormat(ext) {
		return "", fmt.Errorf("magic mismatch: extension .%s but magic not recognized as valid %s", ext, ext)
	}

	return "", fmt.Errorf("unsupported format %s", ext)
}

func ValidateVideoFile(filePath string) (string, error) {
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

	if !isSupportedFormat(format) {
		return "", fmt.Errorf("unsupported format %s", format)
	}

	return format, nil
}

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

func GetFileInfo(filePath string, chunkSize int64, checksumAlgo string) (*FileInfo, error) {
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
		// Still compute checksum(s) if possible
		switch checksumAlgo {
		case "md5":
			cs, _ := ComputeFileMD5(filePath)
			info.Checksum = cs
		case "both":
			sha, md5sum, _ := ComputeFileBoth(filePath)
			info.Checksum = sha
			info.ChecksumMD5 = md5sum
		default:
			cs, _ := ComputeFileSHA256(filePath)
			info.Checksum = cs
		}
		return info, nil
	}
	info.Valid = true
	info.Format = format

	switch checksumAlgo {
	case "md5":
		cs, err := ComputeFileMD5(filePath)
		if err != nil {
			return nil, fmt.Errorf("checksum failed: %w", err)
		}
		info.Checksum = cs
	case "both":
		sha, md5sum, err := ComputeFileBoth(filePath)
		if err != nil {
			return nil, fmt.Errorf("checksum failed: %w", err)
		}
		info.Checksum = sha
		info.ChecksumMD5 = md5sum
	default:
		cs, err := ComputeFileSHA256(filePath)
		if err != nil {
			return nil, fmt.Errorf("checksum failed: %w", err)
		}
		info.Checksum = cs
	}

	return info, nil
}

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
